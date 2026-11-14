"""Persistent state of the unattended pipeline: session summaries and distilled memory.

Lives in the same SQLite file as the source registry so that one database, kept across
days, answers "what do we know about this workspace". Memory is never deleted: an entry
changes status (``active`` -> ``superseded`` / ``retired`` / ``stale``) and every change
is recorded in ``auto_memory_events``, so a bad automated decision can be reversed.

Scope implements the isolation rule: a row derived only from OpenClaw agent sessions has
scope ``openclaw``; anything a coding agent contributed to has scope ``coding``. A
``coding`` reader sees everything, an ``openclaw`` reader sees only ``openclaw`` rows.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal

from session2memory.auto.scope import Reader
from session2memory.auto.secrets import scrub_secrets

Scope = Literal["coding", "openclaw"]
MEMORY_KINDS = ("decision", "constraint", "pitfall", "fact", "preference", "procedure")
ACTIVE = "active"

SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS auto_sessions (
    source_key TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent TEXT,
    scope TEXT NOT NULL,
    project TEXT,
    title TEXT,
    summary TEXT,
    summary_origin TEXT,
    digest TEXT,
    digest_version INTEGER NOT NULL DEFAULT 0,
    curated_version INTEGER NOT NULL DEFAULT 0,
    last_activity TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_sessions_project ON auto_sessions (project);

CREATE TABLE IF NOT EXISTS auto_memories (
    memory_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    text_norm TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL,
    supersedes_id TEXT,
    evidence_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_memories_project ON auto_memories (project, status);

CREATE TABLE IF NOT EXISTS auto_memory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    op TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    run_id TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auto_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    report_json TEXT
);

CREATE TABLE IF NOT EXISTS auto_files (
    path TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    source_bytes INTEGER NOT NULL,
    source_mtime_ns INTEGER NOT NULL,
    target_bytes INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    done_at TEXT NOT NULL,
    purged_at TEXT
);

-- One embedding per searchable row (memory id or session source key) and model.
CREATE TABLE IF NOT EXISTS auto_vectors (
    ref TEXT NOT NULL,
    model TEXT NOT NULL,
    text_digest TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (ref, model)
);
"""


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


_WORD = re.compile(r"[a-z0-9][a-z0-9_.\-]+")
_CJK_RUN = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff]+")


def search_tokens(query: str) -> list[str]:
    """Latin words of two or more characters, and overlapping character pairs of CJK runs."""
    lowered = query.lower()
    tokens = _WORD.findall(lowered)
    for run in _CJK_RUN.findall(lowered):
        tokens += [run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)]
    return list(dict.fromkeys(tokens))


def memory_id_for(project: str, kind: str, text: str) -> str:
    raw = "\0".join((project, kind, normalize(text)))
    return "m_" + sha256(raw.encode("utf-8")).hexdigest()[:10]


@dataclass(frozen=True)
class SessionRow:
    source_key: str
    tool: str
    session_id: str
    agent: str | None
    scope: str
    project: str | None
    title: str | None
    summary: str | None
    summary_origin: str | None
    digest: str | None
    digest_version: int
    curated_version: int
    last_activity: str | None


@dataclass(frozen=True)
class MemoryRow:
    memory_id: str
    project: str
    scope: str
    kind: str
    text: str
    status: str
    created_at: str
    last_confirmed_at: str
    supersedes_id: str | None
    evidence: tuple[str, ...]


def _readable(reader: Reader) -> tuple[str, ...]:
    return ("coding", "openclaw") if reader == "coding" else ("openclaw",)


class AutoStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        connection.executescript(SCHEMA)

    @classmethod
    def open(cls, path: Path) -> AutoStore:
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(path))

    @classmethod
    def open_read_only(cls, path: Path) -> AutoStore | None:
        """For readers that must never create or change the database (the hook)."""
        if not path.is_file():
            return None
        store = cls.__new__(cls)
        store._connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        return store

    def close(self) -> None:
        self._connection.close()

    # ---- meta ----------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self._connection.execute("SELECT value FROM auto_meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO auto_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._connection.commit()

    # Sessions a person sent back to curation: the run reads them whatever their age.
    _BY_HAND_KEY = "curate_by_hand"

    def curate_by_hand(self) -> set[str]:
        raw = self.get_meta(self._BY_HAND_KEY)
        try:
            found = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            return set()
        return {str(key) for key in found} if isinstance(found, list) else set()

    def set_curate_by_hand(self, keys: set[str]) -> None:
        self.set_meta(self._BY_HAND_KEY, json.dumps(sorted(keys)))

    # ---- sessions ------------------------------------------------------------
    def upsert_session(
        self,
        *,
        source_key: str,
        tool: str,
        session_id: str,
        agent: str | None,
        scope: Scope,
        project: str | None,
        title: str | None,
        digest: str | None,
        version_no: int,
        last_activity: str | None,
        native_summary: str | None = None,
    ) -> None:
        """Record a session's current digest; an existing summary stays until re-curated."""
        self._connection.execute(
            "INSERT INTO auto_sessions (source_key, tool, session_id, agent, scope, project, "
            "title, summary, summary_origin, digest, digest_version, last_activity, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_key) DO UPDATE SET agent=excluded.agent, "
            # Once a coding session, always one: a copy that later turns up in an agent's
            # home must not make its summary readable by OpenClaw.
            "scope=CASE WHEN auto_sessions.scope='coding' THEN 'coding' "
            "ELSE excluded.scope END, "
            "project=excluded.project, title=COALESCE(excluded.title, auto_sessions.title), "
            "summary=COALESCE(excluded.summary, auto_sessions.summary), "
            # A native summary written before the session went on no longer covers it.
            "summary_origin=CASE WHEN excluded.summary_origin IS NOT NULL "
            "THEN excluded.summary_origin "
            "WHEN auto_sessions.summary_origin='native' "
            "AND excluded.digest_version > auto_sessions.digest_version THEN 'stale' "
            "ELSE auto_sessions.summary_origin END, "
            "digest=excluded.digest, digest_version=excluded.digest_version, "
            "last_activity=excluded.last_activity, updated_at=excluded.updated_at",
            (
                source_key,
                tool,
                session_id,
                agent,
                scope,
                project,
                scrub_secrets(title) if title else title,
                scrub_secrets(native_summary) if native_summary else native_summary,
                "native" if native_summary else None,
                scrub_secrets(digest) if digest else digest,
                version_no,
                last_activity,
                now_iso(),
            ),
        )
        self._connection.commit()

    def apply_native_summary(self, source_key: str, *, title: str | None, summary: str) -> bool:
        """Adopt a summary the agent itself wrote after the session was last scanned."""
        cursor = self._connection.execute(
            "UPDATE auto_sessions SET summary=?, summary_origin='native', "
            "title=COALESCE(?, title), updated_at=? "
            "WHERE source_key=? AND (summary IS NOT ? OR summary_origin IS NOT 'native')",
            (
                scrub_secrets(summary),
                scrub_secrets(title) if title else None,
                now_iso(),
                source_key,
                scrub_secrets(summary),
            ),
        )
        self._connection.commit()
        return bool(cursor.rowcount)

    def sessions_without_project(self, tool: str) -> list[tuple[str, str]]:
        """``(source_key, session_id)`` of this tool's sessions registered without a workspace."""
        rows = self._connection.execute(
            "SELECT source_key, session_id FROM auto_sessions WHERE tool=? AND project IS NULL",
            (tool,),
        ).fetchall()
        return [(str(key), str(session_id)) for key, session_id in rows]

    def fill_session_project(self, source_key: str, project: str, scope: Scope) -> bool:
        """Give a session registered without a workspace the one that is known now.

        Some agents keep the workspace in an index beside the transcript; when that index
        lagged behind at the first scan, the session has no project and would never be
        curated, although its content did not change.
        """
        cursor = self._connection.execute(
            # A session already readable by coding agents stays so, wherever its
            # workspace turns out to be: scope never narrows.
            "UPDATE auto_sessions SET project=?, "
            "scope=CASE WHEN scope='coding' THEN 'coding' ELSE ? END, updated_at=? "
            "WHERE source_key=? AND project IS NULL",
            (project, scope, now_iso(), source_key),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def widen_session_to_coding(self, source_key: str) -> bool:
        """A coding location of the session turned up: it and what it fed become coding."""
        cursor = self._connection.execute(
            "UPDATE auto_sessions SET scope='coding', updated_at=? "
            "WHERE source_key=? AND scope!='coding'",
            (now_iso(), source_key),
        )
        # Memory this session fed follows it, whether or not the session row itself was
        # already coding (it may have been updated by another path).
        rows = self._connection.execute(
            "SELECT memory_id, evidence_json FROM auto_memories "
            "WHERE scope!='coding' AND evidence_json LIKE ?",
            (f"%{source_key}%",),
        ).fetchall()
        for memory_id, evidence in rows:
            if source_key in json.loads(str(evidence)):
                self._connection.execute(
                    "UPDATE auto_memories SET scope='coding', updated_at=? WHERE memory_id=?",
                    (now_iso(), memory_id),
                )
                self._event(str(memory_id), "widen", {"evidence": [source_key]}, None)
        self._connection.commit()
        return bool(cursor.rowcount)

    def _session(self, row: tuple[object, ...]) -> SessionRow:
        return SessionRow(
            source_key=str(row[0]),
            tool=str(row[1]),
            session_id=str(row[2]),
            agent=str(row[3]) if row[3] is not None else None,
            scope=str(row[4]),
            project=str(row[5]) if row[5] is not None else None,
            title=str(row[6]) if row[6] is not None else None,
            summary=str(row[7]) if row[7] is not None else None,
            summary_origin=str(row[8]) if row[8] is not None else None,
            digest=str(row[9]) if row[9] is not None else None,
            digest_version=int(str(row[10])),
            curated_version=int(str(row[11])),
            last_activity=str(row[12]) if row[12] is not None else None,
        )

    _SESSION_COLUMNS = (
        "source_key, tool, session_id, agent, scope, project, title, summary, summary_origin, "
        "digest, digest_version, curated_version, last_activity"
    )

    def pending_sessions(self) -> list[SessionRow]:
        """Sessions whose latest digest has not been through curation yet, newest first."""
        rows = self._connection.execute(
            f"SELECT {self._SESSION_COLUMNS} FROM auto_sessions "
            "WHERE digest IS NOT NULL AND digest != '' AND project IS NOT NULL "
            "AND digest_version > curated_version ORDER BY last_activity DESC"
        ).fetchall()
        return [self._session(row) for row in rows]

    def get_session(self, source_key: str) -> SessionRow | None:
        row = self._connection.execute(
            f"SELECT {self._SESSION_COLUMNS} FROM auto_sessions WHERE source_key=?", (source_key,)
        ).fetchone()
        return self._session(row) if row else None

    def sessions(
        self, *, project: str | None = None, reader: Reader = "coding", limit: int = 50
    ) -> list[SessionRow]:
        scopes = _readable(reader)
        query = (
            f"SELECT {self._SESSION_COLUMNS} FROM auto_sessions "
            f"WHERE scope IN ({','.join('?' * len(scopes))})"
        )
        args: list[object] = [*scopes]
        if project is not None:
            query += " AND project=?"
            args.append(project)
        rows = self._connection.execute(
            query + " ORDER BY last_activity DESC LIMIT ?", [*args, limit]
        ).fetchall()
        return [self._session(row) for row in rows]

    def mark_curated(
        self,
        source_key: str,
        version_no: int,
        *,
        title: str | None = None,
        summary: str | None = None,
    ) -> None:
        """Curation consumed this version; an LLM summary never overwrites a native one."""
        self._connection.execute(
            "UPDATE auto_sessions SET curated_version=?, "
            "title=COALESCE(title, ?), "
            "summary=CASE WHEN summary_origin='native' THEN summary ELSE COALESCE(?, summary) END, "
            "summary_origin=CASE WHEN summary_origin='native' THEN 'native' "
            "WHEN ? IS NOT NULL THEN 'llm' ELSE summary_origin END, updated_at=? "
            "WHERE source_key=?",
            (
                version_no,
                scrub_secrets(title) if title else title,
                scrub_secrets(summary) if summary else summary,
                summary,
                now_iso(),
                source_key,
            ),
        )
        self._connection.commit()

    # ---- memories ------------------------------------------------------------
    def _memory(self, row: tuple[object, ...]) -> MemoryRow:
        evidence = json.loads(str(row[9]))
        return MemoryRow(
            memory_id=str(row[0]),
            project=str(row[1]),
            scope=str(row[2]),
            kind=str(row[3]),
            text=str(row[4]),
            status=str(row[5]),
            created_at=str(row[6]),
            last_confirmed_at=str(row[7]),
            supersedes_id=str(row[8]) if row[8] is not None else None,
            evidence=tuple(str(item) for item in evidence) if isinstance(evidence, list) else (),
        )

    _MEMORY_COLUMNS = (
        "memory_id, project, scope, kind, text, status, created_at, last_confirmed_at, "
        "supersedes_id, evidence_json"
    )

    def memories(
        self,
        *,
        project: str | None = None,
        status: str | None = ACTIVE,
        reader: Reader = "coding",
        limit: int = 200,
    ) -> list[MemoryRow]:
        scopes = _readable(reader)
        query = (
            f"SELECT {self._MEMORY_COLUMNS} FROM auto_memories "
            f"WHERE scope IN ({','.join('?' * len(scopes))})"
        )
        args: list[object] = [*scopes]
        if project is not None:
            query += " AND project=?"
            args.append(project)
        if status is not None:
            query += " AND status=?"
            args.append(status)
        rows = self._connection.execute(
            query + " ORDER BY last_confirmed_at DESC, memory_id LIMIT ?", [*args, limit]
        ).fetchall()
        return [self._memory(row) for row in rows]

    def memory_projects(self) -> list[str]:
        """Projects that have active memory, the most recently changed first."""
        rows = self._connection.execute(
            "SELECT project FROM auto_memories WHERE status=? "
            "GROUP BY project ORDER BY MAX(updated_at) DESC, project",
            (ACTIVE,),
        ).fetchall()
        return [str(project) for (project,) in rows]

    def get_memory(self, memory_id: str) -> MemoryRow | None:
        row = self._connection.execute(
            f"SELECT {self._MEMORY_COLUMNS} FROM auto_memories WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return self._memory(row) if row else None

    def _event(
        self, memory_id: str, op: str, detail: dict[str, object], run_id: str | None
    ) -> None:
        self._connection.execute(
            "INSERT INTO auto_memory_events (memory_id, op, detail_json, run_id, at) "
            "VALUES (?, ?, ?, ?, ?)",
            (memory_id, op, json.dumps(detail, ensure_ascii=False), run_id, now_iso()),
        )

    def add_memory(
        self,
        *,
        project: str,
        scope: Scope,
        kind: str,
        text: str,
        evidence: list[str],
        run_id: str | None,
        supersedes_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[str, bool]:
        """Insert a memory, or confirm the identical one. Returns ``(id, created)``."""
        # Scrub first: the id, the stored text and the normalised index all come from
        # the same cleaned text.
        text = scrub_secrets(text.strip())
        memory_id = memory_id_for(project, kind, text)
        stamp = now_iso(now)
        existing = self.get_memory(memory_id)
        if existing is not None:
            merged = sorted({*existing.evidence, *evidence})
            # Saying it again makes it current again, and widens who may read it.
            widened = "coding" if "coding" in (existing.scope, scope) else "openclaw"
            self._connection.execute(
                "UPDATE auto_memories SET status=?, scope=?, last_confirmed_at=?, updated_at=?, "
                "evidence_json=? WHERE memory_id=?",
                (ACTIVE, widened, stamp, stamp, json.dumps(merged), memory_id),
            )
            self._event(memory_id, "confirm", {"evidence": evidence}, run_id)
            self._connection.commit()
            return memory_id, False
        self._connection.execute(
            "INSERT INTO auto_memories (memory_id, project, scope, kind, text, text_norm, status, "
            "created_at, updated_at, last_confirmed_at, supersedes_id, evidence_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                project,
                scope,
                kind,
                text,
                normalize(text),
                ACTIVE,
                stamp,
                stamp,
                stamp,
                supersedes_id,
                json.dumps(sorted(set(evidence))),
            ),
        )
        self._event(memory_id, "add", {"evidence": evidence, "supersedes": supersedes_id}, run_id)
        self._connection.commit()
        return memory_id, True

    def set_memory_status(
        self, memory_id: str, status: str, *, reason: str, run_id: str | None, evidence: list[str]
    ) -> bool:
        cursor = self._connection.execute(
            "UPDATE auto_memories SET status=?, updated_at=? WHERE memory_id=? AND status!=?",
            (status, now_iso(), memory_id, status),
        )
        if cursor.rowcount:
            self._event(memory_id, status, {"reason": reason, "evidence": evidence}, run_id)
        self._connection.commit()
        return bool(cursor.rowcount)

    def restore_memory(self, memory_id: str, *, reason: str, now: datetime | None = None) -> bool:
        """Make a retired or stale memory active again; it counts as confirmed now, so the
        next run does not mark it stale straight away."""
        stamp = now_iso(now)
        cursor = self._connection.execute(
            "UPDATE auto_memories SET status=?, last_confirmed_at=?, updated_at=? "
            "WHERE memory_id=? AND status IN ('retired', 'stale')",
            (ACTIVE, stamp, stamp, memory_id),
        )
        if cursor.rowcount:
            self._event(memory_id, "restore", {"reason": reason}, None)
        self._connection.commit()
        return bool(cursor.rowcount)

    def confirm_memory(
        self,
        memory_id: str,
        *,
        evidence: list[str],
        run_id: str | None,
        now: datetime | None = None,
        scope: Scope | None = None,
    ) -> bool:
        existing = self.get_memory(memory_id)
        if existing is None or existing.status != ACTIVE:
            return False
        merged = sorted({*existing.evidence, *evidence})
        stamp = now_iso(now)
        # Scope only ever widens: coding evidence makes the memory a coding memory.
        widened = "coding" if "coding" in (existing.scope, scope) else existing.scope
        self._connection.execute(
            "UPDATE auto_memories SET last_confirmed_at=?, updated_at=?, evidence_json=?, scope=? "
            "WHERE memory_id=?",
            (stamp, stamp, json.dumps(merged), widened, memory_id),
        )
        self._event(memory_id, "confirm", {"evidence": evidence}, run_id)
        self._connection.commit()
        return True

    def mark_stale(self, *, older_than_days: int, run_id: str | None, now: datetime) -> int:
        """Active memories nobody has re-confirmed for a long time stop being injected."""
        cutoff = now_iso(now - timedelta(days=older_than_days))
        rows = self._connection.execute(
            "SELECT memory_id FROM auto_memories WHERE status=? AND last_confirmed_at < ?",
            (ACTIVE, cutoff),
        ).fetchall()
        for (memory_id,) in rows:
            self.set_memory_status(
                str(memory_id),
                "stale",
                reason=f"not confirmed for {older_than_days} days",
                run_id=run_id,
                evidence=[],
            )
        return len(rows)

    def events(self, memory_id: str) -> list[tuple[str, dict[str, object], str]]:
        rows = self._connection.execute(
            "SELECT op, detail_json, at FROM auto_memory_events WHERE memory_id=? ORDER BY id",
            (memory_id,),
        ).fetchall()
        return [(str(op), json.loads(str(detail)), str(at)) for op, detail, at in rows]

    # ---- search --------------------------------------------------------------
    def _search_rows(
        self, project: str | None, reader: Reader
    ) -> list[tuple[int, str, tuple[str, str, str | None, str], str]]:
        """Every row ``reader`` may search: ``(order, recency, hit, lowered text)``."""
        scopes = _readable(reader)
        marks = ",".join("?" * len(scopes))
        memory_sql = (
            "SELECT 'memory:' || kind, memory_id, project, text, last_confirmed_at, 0 "
            f"FROM auto_memories WHERE status=? AND scope IN ({marks})"
        )
        session_sql = (
            "SELECT 'session', source_key, project, "
            "COALESCE(title, '') || '｜' || summary, COALESCE(last_activity, ''), 1 "
            f"FROM auto_sessions WHERE summary IS NOT NULL AND scope IN ({marks})"
        )
        memory_args: list[object] = [ACTIVE, *scopes]
        session_args: list[object] = [*scopes]
        if project is not None:
            memory_sql += " AND project=?"
            session_sql += " AND project=?"
            memory_args.append(project)
            session_args.append(project)
        rows: list[tuple[int, str, tuple[str, str, str | None, str], str]] = []
        for sql, args in ((memory_sql, memory_args), (session_sql, session_args)):
            for kind, ref, row_project, text, recency, order in self._connection.execute(sql, args):
                hit = (str(kind), str(ref), str(row_project) if row_project else None, str(text))
                rows.append((int(order), str(recency), hit, str(text).lower()))
        return rows

    def search_rows(
        self, *, project: str | None = None, reader: Reader = "coding"
    ) -> list[tuple[str, str, str | None, str]]:
        """``(kind, ref, project, text)`` of every active memory and summarised session."""
        return [hit for _, _, hit, _ in self._search_rows(project, reader)]

    # ---- vectors -------------------------------------------------------------
    def vector_digests(self, model: str) -> dict[str, str]:
        try:
            rows = self._connection.execute(
                "SELECT ref, text_digest FROM auto_vectors WHERE model=?", (model,)
            ).fetchall()
        except sqlite3.OperationalError:  # a database from before vectors, opened read-only
            return {}
        return {str(ref): str(digest) for ref, digest in rows}

    def put_vectors(self, model: str, items: list[tuple[str, str, bytes]]) -> None:
        self._connection.executemany(
            "INSERT INTO auto_vectors (ref, model, text_digest, vector) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(ref, model) DO UPDATE SET "
            "text_digest=excluded.text_digest, vector=excluded.vector",
            [(ref, model, digest, vector) for ref, digest, vector in items],
        )
        self._connection.commit()

    def drop_vectors(self, model: str, *, keep: set[str]) -> int:
        """Remove vectors of rows that are no longer searchable, and of other models."""
        stale = [ref for ref in self.vector_digests(model) if ref not in keep]
        self._connection.executemany(
            "DELETE FROM auto_vectors WHERE model=? AND ref=?", [(model, ref) for ref in stale]
        )
        other = self._connection.execute(
            "DELETE FROM auto_vectors WHERE model != ?", (model,)
        ).rowcount
        self._connection.commit()
        return len(stale) + max(other, 0)

    def has_vectors(
        self, model: str, *, project: str | None = None, reader: Reader = "coding"
    ) -> bool:
        """Whether any row this reader may search in this workspace has a vector."""
        known = self.vector_digests(model)
        if not known:
            return False
        return any(
            ref in known for _, ref, _, _ in self.search_rows(project=project, reader=reader)
        )

    def vectors(self, model: str, refs: set[str]) -> dict[str, bytes]:
        try:
            rows = self._connection.execute(
                "SELECT ref, vector FROM auto_vectors WHERE model=?", (model,)
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {str(ref): bytes(vector) for ref, vector in rows if ref in refs}

    def search(
        self, query: str, *, project: str | None = None, reader: Reader = "coding", limit: int = 8
    ) -> list[tuple[str, str, str | None, str]]:
        """Ranked keyword search over active memories and session summaries.

        Returns ``(kind, ref, project, text)``. The query may be a few keywords or a
        sentence: it is split into words and, for CJK text, character pairs. Pieces that
        occur nowhere (the filler of a question, pairs straddling two words) are ignored;
        a row must contain two of the rest (one, when there is only one). Rows matching
        more and rarer pieces rank first, then memories before session summaries, newer
        before older.
        """
        tokens = search_tokens(query)
        if not tokens:
            return []
        rows = self._search_rows(project, reader)
        frequency = {
            token: sum(1 for _, _, _, haystack in rows if token in haystack) for token in tokens
        }
        known = [token for token in tokens if frequency[token]]
        if not known:
            return []
        weight = {token: math.log(1 + len(rows) / frequency[token]) for token in known}
        needed = min(2, len(known))
        scored: list[tuple[float, int, str, tuple[str, str, str | None, str]]] = []
        for order, recency, hit, haystack in rows:
            matched = [token for token in known if token in haystack]
            if len(matched) < needed:
                continue
            scored.append((sum(weight[token] for token in matched), order, recency, hit))
        scored.sort(key=lambda item: item[2], reverse=True)  # newest first
        scored.sort(key=lambda item: (-round(item[0], 6), item[1]))  # rarer matches first
        return [hit for _, _, _, hit in scored[:limit]]

    # ---- runs and file actions -----------------------------------------------
    def start_run(self, run_id: str) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO auto_runs (run_id, started_at) VALUES (?, ?)",
            (run_id, now_iso()),
        )
        self._connection.commit()

    def finish_run(self, run_id: str, report: dict[str, object]) -> None:
        self._connection.execute(
            "UPDATE auto_runs SET finished_at=?, report_json=? WHERE run_id=?",
            (now_iso(), json.dumps(report, ensure_ascii=False), run_id),
        )
        self._connection.commit()

    def last_run(self) -> tuple[str, str | None, dict[str, object]] | None:
        row = self._connection.execute(
            "SELECT run_id, finished_at, report_json FROM auto_runs "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        report = json.loads(str(row[2])) if row[2] else {}
        return str(row[0]), (str(row[1]) if row[1] else None), report

    def file_action(self, path: Path) -> tuple[str, int, int, str | None, str] | None:
        """``(target, source_bytes, source_mtime_ns, purged_at, sha256)`` of an action."""
        row = self._connection.execute(
            "SELECT target, source_bytes, source_mtime_ns, purged_at, content_sha256 "
            "FROM auto_files WHERE path=?",
            (path.as_posix(),),
        ).fetchone()
        if row is None:
            return None
        purged = str(row[3]) if row[3] else None
        return str(row[0]), int(row[1]), int(row[2]), purged, str(row[4])

    def record_file_action(
        self,
        *,
        path: Path,
        action: str,
        target: Path,
        source_bytes: int,
        source_mtime_ns: int,
        target_bytes: int,
        content_sha256: str,
        now: datetime | None = None,
    ) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO auto_files (path, action, target, source_bytes, "
            "source_mtime_ns, target_bytes, content_sha256, done_at, purged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                path.as_posix(),
                action,
                target.as_posix(),
                source_bytes,
                source_mtime_ns,
                target_bytes,
                content_sha256,
                now_iso(now),
            ),
        )
        self._connection.commit()

    def file_actions(
        self, action: str, *, unpurged_before: str | None = None
    ) -> list[tuple[str, str]]:
        query = "SELECT path, done_at FROM auto_files WHERE action=?"
        args: list[object] = [action]
        if unpurged_before is not None:
            query += " AND purged_at IS NULL AND done_at < ?"
            args.append(unpurged_before)
        rows = self._connection.execute(query + " ORDER BY path", args).fetchall()
        return [(str(path), str(done_at)) for path, done_at in rows]

    def mark_purged(self, path: str) -> None:
        self._connection.execute(
            "UPDATE auto_files SET purged_at=? WHERE path=?", (now_iso(), path)
        )
        self._connection.commit()

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for label, sql in (
            ("sessions", "SELECT count(*) FROM auto_sessions"),
            (
                "sessions_with_summary",
                "SELECT count(*) FROM auto_sessions WHERE summary IS NOT NULL",
            ),
            (
                "sessions_pending",
                "SELECT count(*) FROM auto_sessions WHERE digest_version > curated_version "
                "AND digest IS NOT NULL AND digest != '' AND project IS NOT NULL",
            ),
            ("memories_active", "SELECT count(*) FROM auto_memories WHERE status='active'"),
            ("memories_superseded", "SELECT count(*) FROM auto_memories WHERE status='superseded'"),
            ("memories_retired", "SELECT count(*) FROM auto_memories WHERE status='retired'"),
            ("memories_stale", "SELECT count(*) FROM auto_memories WHERE status='stale'"),
        ):
            result[label] = int(self._connection.execute(sql).fetchone()[0])
        return result
