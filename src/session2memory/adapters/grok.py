from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from session2memory.adapters.base import (
    ContentDigest,
    file_session_touches_date,
    make_message,
    parse_datetime,
    read_jsonl,
    skipped_file_reason,
    text_from_content,
)
from session2memory.models import SessionMessage, SessionRecord

CHAT_HISTORY = "chat_history.jsonl"
MESSAGE_TYPES = {"user", "assistant", "system"}
HOME_ID = "grok@main"


class GrokAdapter:
    """Reads Grok CLI sessions: ``<root>/<url-encoded cwd>/<session id>/chat_history.jsonl``.

    The session id is the directory name. Chat records carry no id of their own, so a
    message is addressed by its position in ``chat_history.jsonl`` (``msg:<n>``); the
    registry's message digest catches a history that was rewound and rewritten.
    """

    tool = "grok"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        if not self.root.is_dir():
            return
        for path in sorted(self.root.glob(f"*/*/{CHAT_HISTORY}")):
            try:
                record = self.read_file(path)
            except (OSError, UnicodeError, ValueError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if file_session_touches_date(record, date):
                yield record

    def read_file(self, path: Path) -> SessionRecord:
        session_dir = path.parent
        session_id = session_dir.name
        summary = _read_summary(session_dir / "summary.json")
        info = summary.get("info")
        cwd_value = info.get("cwd") if isinstance(info, dict) else None
        if not cwd_value:
            decoded = unquote(session_dir.parent.name)
            cwd_value = decoded if decoded.startswith("/") else None
        cwd = Path(str(cwd_value)) if cwd_value else None
        started_at = _time(summary.get("created_at"))
        updated_at = _time(summary.get("last_active_at")) or _time(summary.get("updated_at"))
        digest = ContentDigest()
        messages: list[SessionMessage] = []
        position = -1
        for line_number, event in read_jsonl(path, on_line=digest.add):
            position += 1
            kind = event.get("type")
            if kind not in MESSAGE_TYPES:
                continue
            text = text_from_content(event.get("content"))
            if not text:
                continue
            messages.append(
                make_message(
                    tool=self.tool,
                    session_id=session_id,
                    source_path=path,
                    line_number=line_number,
                    role=str(kind),
                    text=text,
                    timestamp=None,
                    cwd=cwd,
                    locator=f"msg:{position}",
                )
            )
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=path,
            started_at=started_at,
            updated_at=updated_at or started_at,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=session_dir.parent.name,
            messages=messages,
            home_id=HOME_ID,
            agent=self.tool,
            content_digest=digest.value(),
            record_count=digest.record_count,
        )

    def read_session(self, path: Path, session_id: str) -> SessionRecord | None:
        record = self.read_file(path)
        return record if record.session_id == session_id else None


def _read_summary(path: Path) -> dict[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None
