from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from session2memory.adapters.base import (
    make_message,
    parse_datetime,
    read_jsonl,
    skipped_file_reason,
)
from session2memory.models import SessionMessage, SessionRecord


class QwenAdapter:
    tool = "qwen"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        paths = {
            *self.root.glob("project*/chats/*.jsonl"),
            *self.root.glob("projects/*/chats/*.jsonl"),
        }
        for path in sorted(paths):
            try:
                record = self._read_file(path)
            except (OSError, UnicodeError, ValueError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if record.started_at and record.started_at.date().isoformat() == date:
                yield record

    def _read_file(self, path: Path) -> SessionRecord:
        session_id = path.stem
        cwd: Path | None = None
        started_at = None
        updated_at = None
        messages: list[SessionMessage] = []
        for line_number, event in read_jsonl(path):
            session_id = str(event.get("sessionId") or session_id)
            timestamp = event.get("timestamp")
            event_time = parse_datetime(str(timestamp)) if timestamp else None
            started_at = started_at or event_time
            updated_at = event_time or updated_at
            cwd_value = event.get("cwd")
            cwd = Path(str(cwd_value)) if cwd_value else cwd
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            parts = message.get("parts")
            texts: list[str] = []
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str):
                            texts.append(text)
            if texts:
                raw_role = str(message.get("role") or event.get("type") or "unknown")
                role = "assistant" if raw_role == "model" else raw_role
                messages.append(
                    make_message(
                        tool=self.tool,
                        session_id=session_id,
                        source_path=path,
                        line_number=line_number,
                        role=role,
                        text="\n".join(texts),
                        timestamp=event_time,
                        cwd=cwd,
                    )
                )
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=path,
            started_at=started_at,
            updated_at=updated_at,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=path.parent.parent.name,
            messages=messages,
        )
