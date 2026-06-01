from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from session2memory.adapters.base import (
    file_session_touches_date,
    jsonl_candidate_paths,
    make_message,
    parse_datetime,
    read_jsonl,
    skipped_file_reason,
)
from session2memory.models import SessionMessage, SessionRecord


class _ClaudeJsonlAdapter:
    tool = "claude"
    primary_patterns = ("**/*.jsonl",)
    modified_patterns = ("**/*.jsonl",)

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        for path in jsonl_candidate_paths(
            self.root,
            date=date,
            primary_patterns=self.primary_patterns,
            modified_patterns=self.modified_patterns,
            exclude=_is_subagent_path,
        ):
            try:
                record = self._read_file(path)
            except (OSError, UnicodeError, ValueError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if file_session_touches_date(record, date):
                yield record

    def _read_file(self, path: Path) -> SessionRecord:
        session_id = path.stem
        cwd: Path | None = None
        started_at = None
        updated_at = None
        messages: list[SessionMessage] = []
        for line_number, event in read_jsonl(path):
            session_id = str(event.get("sessionId") or event.get("session_id") or session_id)
            timestamp = event.get("timestamp")
            event_time = parse_datetime(str(timestamp)) if timestamp else None
            started_at = started_at or event_time
            updated_at = event_time or updated_at
            cwd_value = event.get("cwd")
            cwd = Path(str(cwd_value)) if cwd_value else cwd
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            texts: list[str] = []
            if isinstance(content, str):
                texts.append(content)
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
                        role=str(message.get("role") or event.get("type") or "unknown"),
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
            tool_workspace_id=path.parent.name,
            messages=messages,
        )


def _is_subagent_path(path: Path) -> bool:
    return "/subagents/" in path.as_posix()


class ClaudeAdapter(_ClaudeJsonlAdapter):
    tool = "claude"


class ClaudeDesktopAdapter(_ClaudeJsonlAdapter):
    tool = "claude-desktop"
    primary_patterns = ("**/.claude/projects/**/*.jsonl",)
    modified_patterns = primary_patterns
