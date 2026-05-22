from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from session2memory.adapters.base import make_message
from session2memory.models import SessionMessage, SessionRecord


class OpenCodeAdapter:
    tool = "opencode"

    def __init__(self, path: Path) -> None:
        self.path = path

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        if not self.path.exists():
            return

        connection = sqlite3.connect(self.path)
        try:
            rows = connection.execute(
                """
                select id, directory, time_created, time_updated, workspace_id
                from session
                order by time_created, id
                """
            )
            for session_id, directory, time_created, time_updated, workspace_id in rows:
                started_at = _datetime_from_millis(int(time_created))
                if started_at.date().isoformat() != date:
                    continue

                yield self._read_session(
                    connection=connection,
                    session_id=str(session_id),
                    cwd=Path(str(directory)),
                    started_at=started_at,
                    updated_at=_datetime_from_millis(int(time_updated)),
                    workspace_id=str(workspace_id) if workspace_id is not None else None,
                )
        finally:
            connection.close()

    def _read_session(
        self,
        *,
        connection: sqlite3.Connection,
        session_id: str,
        cwd: Path,
        started_at: datetime,
        updated_at: datetime,
        workspace_id: str | None,
    ) -> SessionRecord:
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

        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=self.path,
            started_at=started_at,
            updated_at=updated_at,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=workspace_id,
            messages=messages,
        )


def _datetime_from_millis(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _json_object(value: str) -> dict[str, object]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        return {}
    return cast("dict[str, object]", loaded)
