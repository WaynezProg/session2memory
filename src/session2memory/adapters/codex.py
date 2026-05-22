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


class CodexAdapter:
    tool = "codex"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        year, month, day = date.split("-")
        for path in sorted((self.root / year / month / day).glob("*.jsonl")):
            try:
                yield self._read_file(path)
            except (OSError, UnicodeError, ValueError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))

    def _read_file(self, path: Path) -> SessionRecord:
        session_id = path.stem
        cwd: Path | None = None
        started_at = None
        updated_at = None
        messages: list[SessionMessage] = []
        for line_number, event in read_jsonl(path):
            if event.get("type") == "session_meta":
                payload = event.get("payload")
                if isinstance(payload, dict):
                    session_id = str(payload.get("id") or session_id)
                    cwd_value = payload.get("cwd")
                    cwd = Path(str(cwd_value)) if cwd_value else cwd
                    timestamp = payload.get("timestamp")
                    started_at = parse_datetime(str(timestamp)) if timestamp else started_at
            if event.get("type") != "response_item":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            content = payload.get("content")
            texts: list[str] = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str):
                            texts.append(text)
            if texts:
                messages.append(
                    make_message(
                        tool=self.tool,
                        session_id=session_id,
                        source_path=path,
                        line_number=line_number,
                        role=str(payload.get("role") or "unknown"),
                        text="\n".join(texts),
                        timestamp=None,
                        cwd=cwd,
                    )
                )
        if messages:
            updated_at = messages[-1].timestamp
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=path,
            started_at=started_at,
            updated_at=updated_at,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=None,
            messages=messages,
        )
