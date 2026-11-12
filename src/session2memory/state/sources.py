"""Source registry: stable session identity, source versions, and a location manifest.

A session is identified by ``tool:native_session_id``. Each message is addressed by a
native locator, never by file path or line number. Every on-disk copy of the session
(plain, natively compressed, archived, a copy under another home) is one *location* of
the same source. Evidence is resolved by walking the locations that are still present
and verifying the message digest, so it survives the original file being compressed,
moved or archived, and fails loudly when no copy is left.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from session2memory.models import SessionMessage, SessionRecord, SourceHome

ObservationReason = Literal[
    "first-seen", "unchanged", "alias", "append", "rewrite", "older-copy"
]
# Re-reads one session from a location: ``(path, session_id) -> record`` (``None`` when the
# container no longer holds that session, e.g. rows deleted from a sqlite store).
SessionReader = Callable[[Path, str], SessionRecord | None]


@dataclass(frozen=True)
class SourceObservation:
    source_key: str
    version_no: int
    reason: ObservationReason
    new_locators: frozenset[str]

    @property
    def has_new_content(self) -> bool:
        return self.reason in {"first-seen", "append", "rewrite"}


@dataclass(frozen=True)
class SourceLocation:
    source_key: str
    path: Path
    home_id: str | None
    encoding: str
    state: str
    content_digest: str
    version_no: int


@dataclass(frozen=True)
class ResolvedEvidence:
    ok: bool
    source_key: str | None = None
    locator: str | None = None
    path: Path | None = None
    text: str | None = None
    error: str | None = None


def _encoding_for(path: Path) -> str:
    if path.name.endswith((".zst", ".zstd")):
        return "zstd"
    if path.suffix in {".sqlite", ".db"}:
        return "sqlite"
    return "plain"


def message_locator(message: SessionMessage) -> str:
    """Native locator when the adapter provides one, physical line number otherwise."""
    return message.locator or f"line:{message.index}"


class SourceRegistry:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # ---- homes ---------------------------------------------------------------
    def register_home(self, home: SourceHome) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO source_homes (home_id, tool, agent, root) VALUES (?, ?, ?, ?)",
            (home.home_id, home.tool, home.agent, home.root.as_posix()),
        )
        self._connection.commit()

    def list_homes(self) -> list[SourceHome]:
        rows = self._connection.execute(
            "SELECT home_id, tool, agent, root FROM source_homes ORDER BY home_id"
        ).fetchall()
        return [SourceHome(str(r[0]), str(r[1]), str(r[2]), Path(str(r[3]))) for r in rows]

    # ---- observe -------------------------------------------------------------
    def observe(self, record: SessionRecord, *, workspace_id: str | None) -> SourceObservation:
        """Register one read of a session file and report what is new about it."""
        if not record.content_digest:
            raise ValueError("observe() requires an adapter that sets content_digest")
        key = record.source_key
        now = datetime.now(UTC).isoformat()
        current = {
            message_locator(message): (position, message.role, message.raw_pointer.digest)
            for position, message in enumerate(record.messages)
        }
        row = self._connection.execute(
            "SELECT version_no FROM sources WHERE source_key=?", (key,)
        ).fetchone()
        reason: ObservationReason
        new_locators: frozenset[str]
        if row is None:
            version_no, reason, new_locators = 1, "first-seen", frozenset(current)
            self._connection.execute(
                "INSERT INTO sources (source_key, tool, session_id, agent, workspace_id, "
                "first_home_id, version_no) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (key, record.tool, record.session_id, record.agent, workspace_id, record.home_id),
            )
            self._insert_version(key, 1, record, reason, now)
            self._write_messages(key, current, set(current), 1)
        else:
            known = self._connection.execute(
                "SELECT version_no FROM source_versions WHERE source_key=? AND content_digest=?",
                (key, record.content_digest),
            ).fetchone()
            if known is not None:
                # Same content: the same file again, its compressed form, or a copy elsewhere.
                version_no = int(known[0])
                seen_here = self._connection.execute(
                    "SELECT 1 FROM source_locations WHERE source_key=? AND path=? "
                    "AND content_digest=?",
                    (key, record.source_path.as_posix(), record.content_digest),
                ).fetchone()
                reason = "unchanged" if seen_here else "alias"
                new_locators = frozenset()
                latest = int(row[0])
                held_latest = self._connection.execute(
                    "SELECT 1 FROM source_locations WHERE source_key=? AND path=? AND version_no=?",
                    (key, record.source_path.as_posix(), latest),
                ).fetchone()
                if not seen_here and held_latest and version_no != latest:
                    # This very file held the latest version and now shows earlier content
                    # again: the history was rolled back, which is new content to act on.
                    reason = "rewrite"
                    version_no = latest + 1
                    new_locators = frozenset(current)
                    self._connection.execute(
                        "UPDATE sources SET version_no=? WHERE source_key=?", (version_no, key)
                    )
                    self._insert_version(key, version_no, record, reason, now)
                    self._write_messages(key, current, set(current), version_no)
            else:
                existing = {
                    str(r[0]): str(r[1])
                    for r in self._connection.execute(
                        "SELECT locator, message_digest FROM source_messages WHERE source_key=?",
                        (key,),
                    )
                }
                changed = {
                    locator
                    for locator, (_, _, digest) in current.items()
                    if existing.get(locator) != digest
                }
                kept = all(
                    locator in current and current[locator][2] == digest
                    for locator, digest in existing.items()
                )
                at_latest = self._connection.execute(
                    "SELECT 1 FROM source_locations "
                    "WHERE source_key=? AND path=? AND version_no=?",
                    (key, record.source_path.as_posix(), int(row[0])),
                ).fetchone()
                if not changed and len(current) < len(existing) and at_latest is None:
                    # Nothing new, and less than we already hold, at a path that never held
                    # the latest version: an older snapshot of the session kept somewhere
                    # else. (The same file getting shorter is a rewind, handled below.)
                    # It is recorded as a location (version 0) and must not become the
                    # current version.
                    reason = "older-copy"
                    version_no = 0
                    new_locators = frozenset()
                else:
                    reason = "append" if kept else "rewrite"
                    version_no = int(row[0]) + 1
                    new_locators = frozenset(changed)
                    self._connection.execute(
                        "UPDATE sources SET version_no=? WHERE source_key=?", (version_no, key)
                    )
                    self._insert_version(key, version_no, record, reason, now)
                    self._write_messages(key, current, changed, version_no)
        self._upsert_location(record, version_no, now)
        self._refresh_location_states(key)
        self._connection.commit()
        return SourceObservation(key, version_no, reason, new_locators)

    def _insert_version(
        self, key: str, version_no: int, record: SessionRecord, reason: str, now: str
    ) -> None:
        self._connection.execute(
            "INSERT INTO source_versions (source_key, version_no, content_digest, record_count, "
            "reason, observed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, version_no, record.content_digest, record.record_count, reason, now),
        )

    def _write_messages(
        self,
        key: str,
        current: Mapping[str, tuple[int, str, str]],
        locators: set[str],
        version_no: int,
    ) -> None:
        self._connection.executemany(
            "INSERT OR REPLACE INTO source_messages (source_key, locator, ord, role, "
            "message_digest, version_no) VALUES (?, ?, ?, ?, ?, ?)",
            [(key, loc, *current[loc], version_no) for loc in sorted(locators)],
        )

    def _upsert_location(self, record: SessionRecord, version_no: int, now: str) -> None:
        path = record.source_path
        try:
            stat = path.stat()
            size, mtime_ns = stat.st_size, stat.st_mtime_ns
        except OSError:
            size, mtime_ns = 0, 0
        self._connection.execute(
            "INSERT OR REPLACE INTO source_locations (source_key, path, home_id, encoding, state, "
            "content_digest, version_no, bytes, mtime_ns, observed_at) "
            "VALUES (?, ?, ?, ?, 'present', ?, ?, ?, ?, ?)",
            (
                record.source_key,
                path.as_posix(),
                record.home_id,
                _encoding_for(path),
                record.content_digest,
                version_no,
                size,
                mtime_ns,
                now,
            ),
        )

    def _refresh_location_states(self, key: str) -> None:
        rows = self._connection.execute(
            "SELECT path, state FROM source_locations WHERE source_key=?", (key,)
        ).fetchall()
        for raw_path, state in rows:
            actual = "present" if Path(str(raw_path)).exists() else "missing"
            if actual != state:
                self._connection.execute(
                    "UPDATE source_locations SET state=? WHERE source_key=? AND path=?",
                    (actual, key, raw_path),
                )

    def add_copy(self, original: Path, copy: Path, *, original_gone: bool) -> int:
        """Register ``copy`` as a location of every source located at ``original``.

        Used when a session file is compressed in place (``original_gone``) or archived
        next to an untouched original, so evidence keeps resolving without a rescan.
        """
        rows = self._connection.execute(
            "SELECT source_key, home_id, content_digest, version_no FROM source_locations "
            "WHERE path=?",
            (original.as_posix(),),
        ).fetchall()
        try:
            stat = copy.stat()
            size, mtime_ns = stat.st_size, stat.st_mtime_ns
        except OSError:
            size, mtime_ns = 0, 0
        now = datetime.now(UTC).isoformat()
        for source_key, home_id, content_digest, version_no in rows:
            self._connection.execute(
                "INSERT OR REPLACE INTO source_locations (source_key, path, home_id, encoding, "
                "state, content_digest, version_no, bytes, mtime_ns, observed_at) "
                "VALUES (?, ?, ?, ?, 'present', ?, ?, ?, ?, ?)",
                (
                    source_key,
                    copy.as_posix(),
                    home_id,
                    _encoding_for(copy),
                    content_digest,
                    version_no,
                    size,
                    mtime_ns,
                    now,
                ),
            )
            if original_gone:
                self._connection.execute(
                    "UPDATE source_locations SET state='missing' WHERE source_key=? AND path=?",
                    (source_key, original.as_posix()),
                )
        self._connection.commit()
        return len(rows)

    # ---- candidates and refs -------------------------------------------------
    def link_candidate(
        self, *, candidate_id: str, source_key: str, locator: str, message_digest: str
    ) -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO candidate_sources (candidate_id, source_key, locator, "
            "message_digest) VALUES (?, ?, ?, ?)",
            (candidate_id, source_key, locator, message_digest),
        )
        self._connection.commit()

    def add_ref(self, *, parent_kind: str, parent_id: str, source_key: str) -> None:
        """Record that an external task/run/thread points at a session (reference only)."""
        self._connection.execute(
            "INSERT OR IGNORE INTO source_refs (parent_kind, parent_id, source_key) "
            "VALUES (?, ?, ?)",
            (parent_kind, parent_id, source_key),
        )
        self._connection.commit()

    def refs_for(self, source_key: str) -> list[tuple[str, str]]:
        rows = self._connection.execute(
            "SELECT parent_kind, parent_id FROM source_refs WHERE source_key=? "
            "ORDER BY parent_kind, parent_id",
            (source_key,),
        ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    # ---- queries -------------------------------------------------------------
    def source_keys(
        self, *, agent: str | None = None, workspace_id: str | None = None
    ) -> list[str]:
        query = "SELECT source_key FROM sources WHERE 1=1"
        args: list[str] = []
        if agent is not None:
            query += " AND agent=?"
            args.append(agent)
        if workspace_id is not None:
            query += " AND workspace_id=?"
            args.append(workspace_id)
        rows = self._connection.execute(query + " ORDER BY source_key", args).fetchall()
        return [str(r[0]) for r in rows]

    def versions(self, source_key: str) -> list[tuple[int, str]]:
        rows = self._connection.execute(
            "SELECT version_no, reason FROM source_versions WHERE source_key=? ORDER BY version_no",
            (source_key,),
        ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def locations(self, source_key: str) -> list[SourceLocation]:
        self._refresh_location_states(source_key)
        self._connection.commit()
        rows = self._connection.execute(
            "SELECT path, home_id, encoding, state, content_digest, version_no "
            "FROM source_locations WHERE source_key=? "
            "ORDER BY version_no DESC, encoding='plain' DESC, path",
            (source_key,),
        ).fetchall()
        return [
            SourceLocation(
                source_key=source_key,
                path=Path(str(r[0])),
                home_id=str(r[1]) if r[1] is not None else None,
                encoding=str(r[2]),
                state=str(r[3]),
                content_digest=str(r[4]),
                version_no=int(r[5]),
            )
            for r in rows
        ]

    # ---- evidence resolution -------------------------------------------------
    def resolve_candidate(
        self, candidate_id: str, *, readers: Mapping[str, SessionReader]
    ) -> ResolvedEvidence:
        """Find the exact source message behind a candidate in any location still present."""
        row = self._connection.execute(
            "SELECT source_key, locator, message_digest FROM candidate_sources "
            "WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return ResolvedEvidence(ok=False, error="candidate has no registered source")
        key, locator, digest = str(row[0]), str(row[1]), str(row[2])
        tool, session_id = key.split(":", 1)
        reader = readers.get(tool)
        if reader is None:
            return ResolvedEvidence(
                ok=False, source_key=key, locator=locator, error=f"no reader for tool {tool}"
            )
        tried: list[str] = []
        for location in self.locations(key):
            if location.state != "present":
                continue
            tried.append(location.path.as_posix())
            try:
                record = reader(location.path, session_id)
            except (OSError, UnicodeError, ValueError, sqlite3.Error):
                continue
            if record is None:
                continue
            for message in record.messages:
                if message_locator(message) == locator and message.raw_pointer.digest == digest:
                    return ResolvedEvidence(
                        ok=True,
                        source_key=key,
                        locator=locator,
                        path=location.path,
                        text=message.text,
                    )
        detail = f" (checked {len(tried)} location(s))" if tried else ""
        return ResolvedEvidence(
            ok=False,
            source_key=key,
            locator=locator,
            error="no present location holds this message" + detail,
        )
