from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from session2memory.adapters.base import (
    file_modified_on_date,
    file_session_touches_date,
    make_message,
    skipped_file_reason,
)
from session2memory.models import SessionMessage, SessionRecord

_ISO_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?\s+"
)
_HERMES_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \w+\s+")
_ROLE_PREFIX = re.compile(r"^\[(user|assistant|tool|system)\]\s*", re.IGNORECASE)
_COMMENT_PREFIX = re.compile(r"^\s*#")


def iter_text_log_sessions(
    *,
    root: Path,
    tool: str,
    date: str,
    skipped: list[str],
) -> Iterator[SessionRecord]:
    if not root.exists():
        return
    for path in sorted(_candidate_files(root)):
        if not file_modified_on_date(path, date):
            continue
        try:
            record = _read_log_file(path=path, tool=tool)
        except (OSError, UnicodeError, ValueError) as exc:
            skipped.append(skipped_file_reason(tool, path, exc))
            continue
        if file_session_touches_date(record, date):
            yield record


def _candidate_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file():
            paths.append(path)
    return paths


def _read_log_file(*, path: Path, tool: str) -> SessionRecord:
    session_id = path.stem
    cwd: Path | None = None
    messages: list[SessionMessage] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or _COMMENT_PREFIX.match(line):
            continue
        role, text = _parse_line(line)
        if not text:
            continue
        messages.append(
            make_message(
                tool=tool,
                session_id=session_id,
                source_path=path,
                line_number=line_number,
                role=role,
                text=text,
                timestamp=None,
                cwd=cwd,
            )
        )
    updated_at = None
    if messages:
        try:
            updated_at = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            updated_at = None
    return SessionRecord(
        tool=tool,
        session_id=session_id,
        source_path=path,
        started_at=updated_at,
        updated_at=updated_at,
        cwd=cwd,
        repo_root=None,
        tool_workspace_id=None,
        messages=messages,
    )


def _parse_line(line: str) -> tuple[str, str]:
    normalized = line
    normalized = _ISO_PREFIX.sub("", normalized)
    normalized = _HERMES_PREFIX.sub("", normalized)
    role_match = _ROLE_PREFIX.match(normalized)
    if role_match:
        role = role_match.group(1).lower()
        text = normalized[role_match.end() :].strip()
        return role, text
    return "unknown", normalized.strip()


def touch_file_mtime(path: Path, date: str) -> None:
    year, month, day = (int(part) for part in date.split("-"))
    timestamp = datetime(year, month, day, 12, 0, 0).timestamp()
    os.utime(path, (timestamp, timestamp))
