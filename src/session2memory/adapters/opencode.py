from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from session2memory.adapters.base import make_message, timestamps_touch_date
from session2memory.models import SessionMessage, SessionRecord

# OpenCode moved to `session_v2` / `session_message` (around 2026-10). Older
# databases, and sessions written before the move, only have `session` /
# `message` / `part`. Both are read; a session found in both is read once.
_V2_ROLES = {"user", "assistant"}


class OpenCodeAdapter:
    tool = "opencode"

    def __init__(self, path: Path) -> None:
        self.path = path

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        if not self.path.exists():
            return

        connection = sqlite3.connect(self.path)
        try:
            tables = _table_names(connection)
            has_v2 = {"session_v2", "session_message"} <= tables
            has_legacy = {"session", "message", "part"} <= tables
            seen: set[str] = set()
            sources: list[tuple[str, bool]] = []
            if has_v2:
                sources.append(("session_v2", True))
            if "session" in tables:
                sources.append(("session", False))

            for table, from_v2 in sources:
                rows = connection.execute(
                    f"""
                    select id, directory, time_created, time_updated, workspace_id
                    from {table}
                    order by time_created, id
                    """
                ).fetchall()
                for session_id, directory, time_created, time_updated, workspace_id in rows:
                    session_id = str(session_id)
                    if session_id in seen:
                        continue
                    seen.add(session_id)
                    started_at = _datetime_from_millis(int(time_created))
                    updated_at = _datetime_from_millis(int(time_updated))
                    if not timestamps_touch_date(date, started_at, updated_at):
                        continue

                    cwd = Path(str(directory))
                    messages: list[SessionMessage] = []
                    if from_v2:
                        messages = self._read_v2_messages(connection, session_id, cwd)
                    if not messages and has_legacy:
                        messages = self._read_legacy_messages(connection, session_id, cwd)

                    yield SessionRecord(
                        tool=self.tool,
                        session_id=session_id,
                        source_path=self.path,
                        started_at=started_at,
                        updated_at=updated_at,
                        cwd=cwd,
                        repo_root=None,
                        tool_workspace_id=str(workspace_id) if workspace_id is not None else None,
                        messages=messages,
                    )
        finally:
            connection.close()

    def _read_v2_messages(
        self, connection: sqlite3.Connection, session_id: str, cwd: Path
    ) -> list[SessionMessage]:
        messages: list[SessionMessage] = []
        rows = connection.execute(
            """
            select type, seq, time_created, data
            from session_message
            where session_id = ?
            order by seq
            """,
            (session_id,),
        )
        for message_type, seq, time_created, data in rows:
            role = str(message_type)
            if role not in _V2_ROLES:
                continue
            try:
                payload = _json_object(str(data))
            except json.JSONDecodeError:
                continue
            for text in _v2_texts(role, payload):
                messages.append(
                    make_message(
                        tool=self.tool,
                        session_id=session_id,
                        source_path=self.path,
                        line_number=int(seq),
                        role=role,
                        text=text,
                        timestamp=_datetime_from_millis(int(time_created)),
                        cwd=cwd,
                    )
                )
        return messages

    def _read_legacy_messages(
        self, connection: sqlite3.Connection, session_id: str, cwd: Path
    ) -> list[SessionMessage]:
        messages: list[SessionMessage] = []
        rows = connection.execute(
            """
            select m.data, p.data, p.time_created
            from message m
            join part p on p.message_id = m.id and p.session_id = m.session_id
            where m.session_id = ?
            order by m.time_created, m.id, p.id
            """,
            (session_id,),
        )
        for index, (message_data, part_data, part_time_created) in enumerate(rows, start=1):
            try:
                message_json = _json_object(str(message_data))
                part_json = _json_object(str(part_data))
            except json.JSONDecodeError:
                continue
            if part_json.get("type") != "text":
                continue
            text = part_json.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            messages.append(
                make_message(
                    tool=self.tool,
                    session_id=session_id,
                    source_path=self.path,
                    line_number=index,
                    role=str(message_json.get("role") or "unknown"),
                    text=text,
                    timestamp=_datetime_from_millis(int(part_time_created)),
                    cwd=cwd,
                )
            )
        return messages


def _v2_texts(role: str, payload: dict[str, object]) -> list[str]:
    if role == "user":
        text = payload.get("text")
        return [text] if isinstance(text, str) and text.strip() else []
    texts: list[str] = []
    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
    return texts


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("select name from sqlite_master where type = 'table'")
    return {str(name) for (name,) in rows}


def _datetime_from_millis(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _json_object(value: str) -> dict[str, object]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        return {}
    return cast("dict[str, object]", loaded)
