from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
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
    primary_patterns: Sequence[str] = ("**/*.jsonl",)
    modified_patterns: Sequence[str] = ("**/*.jsonl",)

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
            timestamp = event.get("timestamp") or event.get("_audit_timestamp")
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
    primary_patterns = ("local-agent-mode-sessions/**/.claude/projects/**/*.jsonl",)
    modified_patterns = primary_patterns

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        yield from super().iter_sessions(date)
        yield from self._iter_metadata_sessions(date)

    def _iter_metadata_sessions(self, date: str) -> Iterator[SessionRecord]:
        for path in sorted(self.root.glob("claude-code-sessions/**/*.json")):
            try:
                record = self._read_metadata_file(path)
            except (OSError, UnicodeError, ValueError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if file_session_touches_date(record, date):
                yield record

    def _read_metadata_file(self, path: Path) -> SessionRecord:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.as_posix()}: expected JSON object")

        session_id = str(raw.get("sessionId") or path.stem)
        started_at = _parse_epoch_ms(raw.get("createdAt"))
        updated_at = _parse_epoch_ms(raw.get("lastActivityAt"))
        cwd_value = raw.get("cwd") or raw.get("originCwd")
        cwd = Path(str(cwd_value)) if cwd_value else None
        title = str(raw.get("title") or "").strip()
        messages: list[SessionMessage] = []
        if title:
            messages.append(
                make_message(
                    tool=self.tool,
                    session_id=session_id,
                    source_path=path,
                    line_number=1,
                    role="assistant",
                    text=f"Done: {title}",
                    timestamp=updated_at or started_at,
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


def _parse_epoch_ms(value: object) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)
