from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from session2memory.adapters.base import (
    ContentDigest,
    make_message,
    parse_datetime,
    skipped_file_reason,
    text_from_content,
    timestamps_touch_date,
)
from session2memory.models import SessionMessage, SessionRecord

DATABASE_NAME = "openclaw-agent.sqlite"
MESSAGE_ROLES = {"user", "assistant", "system"}


class OpenClawNativeAdapter:
    """Reads OpenClaw's per-agent session store (``openclaw-agent.sqlite``), read-only.

    ``root`` is either ``<state>/agents`` (one database per agent) or a single database.
    A session is ``<agent>/<native session id>``: native ids such as explicit session names
    are only unique within one agent. Messages are addressed by the store's stable
    ``event_id``. Rows stored as ``event_zstd`` are decoded through the ``zstd`` executable.
    """

    tool = "openclaw-native"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def databases(self) -> list[tuple[str, Path]]:
        if self.root.is_file():
            return [(_agent_for(self.root), self.root)]
        return [
            (path.parent.parent.name, path)
            for path in sorted(self.root.glob(f"*/agent/{DATABASE_NAME}"))
        ]

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        for agent, path in self.databases():
            try:
                connection = _connect_read_only(path)
            except sqlite3.Error as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            try:
                for native_id, started_at, updated_at in _session_windows(connection):
                    if not timestamps_touch_date(date, started_at, updated_at):
                        continue
                    yield _read_session(
                        connection,
                        tool=self.tool,
                        path=path,
                        agent=agent,
                        native_id=native_id,
                        started_at=started_at,
                        updated_at=updated_at,
                    )
            except (sqlite3.Error, ValueError, OSError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
            finally:
                connection.close()

    def read_session(self, path: Path, session_id: str) -> SessionRecord | None:
        agent, _, native_id = session_id.partition("/")
        connection = _connect_read_only(path)
        try:
            row = next(iter(_session_windows(connection, native_id)), None)
            if row is None:
                return None
            return _read_session(
                connection,
                tool=self.tool,
                path=path,
                agent=agent,
                native_id=native_id,
                started_at=row[1],
                updated_at=row[2],
            )
        finally:
            connection.close()


def _agent_for(database: Path) -> str:
    parent = database.parent
    return parent.parent.name if parent.name == "agent" else database.stem


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    connection.execute("PRAGMA query_only=1")
    return connection


def _from_millis(value: object) -> datetime | None:
    if not isinstance(value, int | float) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _session_windows(
    connection: sqlite3.Connection, session_id: str | None = None
) -> list[tuple[str, datetime | None, datetime | None]]:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(session_windows)")}
    started = "created_at" if "created_at" in columns else "NULL"
    updated = "transcript_updated_at" if "transcript_updated_at" in columns else started
    query = f"SELECT session_id, {started}, {updated} FROM session_windows"
    if session_id is None:
        rows = connection.execute(query + " ORDER BY session_id").fetchall()
    else:
        rows = connection.execute(query + " WHERE session_id=?", (session_id,)).fetchall()
    return [
        (str(row[0]), _from_millis(row[1]), _from_millis(row[2]) or _from_millis(row[1]))
        for row in rows
    ]


def _decode_events(rows: list[tuple[int, str | None, bytes | None, int | None]]) -> list[str]:
    """Return every event as JSON text; all ``event_zstd`` rows share one zstd process."""
    packed = [(index, blob, size) for index, (_, _, blob, size) in enumerate(rows) if blob]
    decoded: dict[int, str] = {}
    if packed:
        executable = shutil.which("zstd")
        if executable is None:
            raise ValueError("zstd executable not found; cannot read event_zstd rows")
        result = subprocess.run(
            [executable, "-dc"],
            input=b"".join(blob for _, blob, _ in packed),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(f"zstd exited with status {result.returncode}")
        offset = 0
        for index, _, size in packed:
            if size is None:
                raise ValueError("event_zstd row without event_utf8_bytes")
            decoded[index] = result.stdout[offset : offset + size].decode("utf-8")
            offset += size
        if offset != len(result.stdout):
            raise ValueError("event_zstd rows decoded to an unexpected length")
    return [
        decoded[index] if index in decoded else str(event_json)
        for index, (_, event_json, _, _) in enumerate(rows)
    ]


def _read_session(
    connection: sqlite3.Connection,
    *,
    tool: str,
    path: Path,
    agent: str,
    native_id: str,
    started_at: datetime | None,
    updated_at: datetime | None,
) -> SessionRecord:
    session_id = f"{agent}/{native_id}"
    rows = connection.execute(
        "SELECT e.seq, e.event_json, e.event_zstd, e.event_utf8_bytes, i.event_id "
        "FROM transcript_events e LEFT JOIN transcript_event_identities i "
        "ON i.session_id = e.session_id AND i.seq = e.seq "
        "WHERE e.session_id = ? ORDER BY e.seq",
        (native_id,),
    ).fetchall()
    texts = _decode_events([(int(r[0]), r[1], r[2], r[3]) for r in rows])
    digest = ContentDigest()
    cwd: Path | None = None
    pending: list[tuple[int, str, str, datetime | None, str]] = []
    for (seq, _, _, _, event_id), raw in zip(rows, texts, strict=True):
        digest.add(f"{seq}\t{raw}")
        event = json.loads(raw)
        if not isinstance(event, dict):
            continue
        if event.get("type") == "session" and event.get("cwd"):
            cwd = Path(str(event["cwd"]))
        message = event.get("message")
        if event.get("type") != "message" or not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        text = text_from_content(message.get("content"))
        if role not in MESSAGE_ROLES or not text:
            continue
        timestamp = event.get("timestamp")
        identity = event_id or event.get("id") or seq
        pending.append(
            (
                int(seq),
                role,
                text,
                parse_datetime(timestamp) if isinstance(timestamp, str) else None,
                f"event:{identity}",
            )
        )
    messages: list[SessionMessage] = [
        make_message(
            tool=tool,
            session_id=session_id,
            source_path=path,
            line_number=seq,
            role=role,
            text=text,
            timestamp=timestamp,
            cwd=cwd,
            locator=locator,
        )
        for seq, role, text, timestamp, locator in pending
    ]
    return SessionRecord(
        tool=tool,
        session_id=session_id,
        source_path=path,
        started_at=started_at,
        updated_at=updated_at,
        cwd=cwd,
        repo_root=None,
        tool_workspace_id=None,
        messages=messages,
        home_id=f"openclaw@{agent}",
        agent=f"openclaw/{agent}",
        content_digest=digest.value(),
        record_count=digest.record_count,
    )
