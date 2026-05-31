from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from string import hexdigits
from typing import cast

from session2memory.adapters.base import (
    file_session_touches_date,
    jsonl_candidate_paths,
    make_message,
    read_jsonl,
    skipped_file_reason,
)
from session2memory.models import SessionMessage, SessionRecord

_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_WORKSPACE_PATH_RE = re.compile(r"^Workspace Path:\s*(.+?)\s*$", re.MULTILINE)


class CursorAdapter:
    tool = "cursor"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*/*/store.db")):
            try:
                record = self._read_db(path)
            except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if file_session_touches_date(record, date):
                yield record

    def _read_db(self, path: Path) -> SessionRecord:
        connection = sqlite3.connect(path)
        try:
            meta = _read_cursor_meta(connection)
            session_id = str(meta.get("agentId") or path.parent.name)
            started_at = _datetime_from_millis(meta.get("createdAt"))
            updated_at = _file_modified_at(path)
            cwd: Path | None = None
            messages: list[SessionMessage] = []
            rows = connection.execute("select rowid, data from blobs order by rowid")
            for rowid, data in rows:
                blob = _json_object_from_blob(data)
                role = str(blob.get("role") or "unknown")
                if role == "system":
                    continue
                raw_texts = cursor_texts_from_content(blob.get("content"), clean=False)
                if cwd is None:
                    cwd = _first_workspace_path(raw_texts)
                texts = [cursor_clean_text(text) for text in raw_texts]
                text = "\n".join(text for text in texts if text)
                if not text.strip():
                    continue
                messages.append(
                    make_message(
                        tool=self.tool,
                        session_id=session_id,
                        source_path=path,
                        line_number=int(rowid),
                        role=role,
                        text=text,
                        timestamp=None,
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
                tool_workspace_id=None,
                messages=messages,
            )
        finally:
            connection.close()


class CursorCliAdapter:
    tool = "cursor-cli"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        for path in self._candidate_paths(date):
            try:
                record = self._read_file(path)
            except (OSError, UnicodeError, ValueError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if file_session_touches_date(record, date):
                yield record

    def _candidate_paths(self, date: str) -> list[Path]:
        return jsonl_candidate_paths(
            self.root,
            date=date,
            primary_patterns=(),
            modified_patterns=("*/*/*.jsonl", "*/agent-transcripts/*/*.jsonl"),
        )

    def _read_file(self, path: Path) -> SessionRecord:
        session_id = path.stem
        cwd = _cwd_from_cursor_project_path(self.root, path)
        updated_at = _file_modified_at(path)
        messages: list[SessionMessage] = []
        for line_number, event in read_jsonl(path):
            role = str(event.get("role") or "unknown")
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            raw_texts = cursor_texts_from_content(message.get("content"), clean=False)
            if cwd is None:
                cwd = _first_workspace_path(raw_texts)
            texts = [cursor_clean_text(text) for text in raw_texts]
            text = "\n".join(text for text in texts if text)
            if not text.strip():
                continue
            messages.append(
                make_message(
                    tool=self.tool,
                    session_id=session_id,
                    source_path=path,
                    line_number=line_number,
                    role=role,
                    text=text,
                    timestamp=None,
                    cwd=cwd,
                )
            )
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=path,
            started_at=None,
            updated_at=updated_at,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=None,
            messages=messages,
        )


def cursor_texts_from_content(value: object, *, clean: bool = True) -> list[str]:
    if isinstance(value, str):
        text = cursor_clean_text(value) if clean else value.strip()
        return [text] if text else []
    texts: list[str] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "redacted-reasoning":
                continue
            block_text = item.get("text")
            if isinstance(block_text, str) and block_text.strip():
                texts.append(cursor_clean_text(block_text) if clean else block_text.strip())
    return texts


def cursor_clean_text(value: str) -> str:
    match = _USER_QUERY_RE.search(value)
    if match:
        return match.group(1).strip()
    return value.strip()


def _first_workspace_path(values: list[str]) -> Path | None:
    for value in values:
        match = _WORKSPACE_PATH_RE.search(value)
        if match:
            return Path(match.group(1)).expanduser()
    return None


def _read_cursor_meta(connection: sqlite3.Connection) -> dict[str, object]:
    row = connection.execute("select value from meta order by key limit 1").fetchone()
    if row is None:
        return {}
    value = row[0]
    if isinstance(value, bytes):
        raw = value.decode("utf-8")
    else:
        raw = str(value)
    raw = raw.strip()
    if raw and len(raw) % 2 == 0 and all(char in hexdigits for char in raw):
        raw = bytes.fromhex(raw).decode("utf-8")
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        return {}
    return cast("dict[str, object]", loaded)


def _json_object_from_blob(value: object) -> dict[str, object]:
    raw = bytes(value) if isinstance(value, bytes | bytearray | memoryview) else str(value).encode()
    if not raw.startswith(b"{"):
        return {}
    loaded = json.loads(raw.decode("utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return cast("dict[str, object]", loaded)


def _datetime_from_millis(value: object) -> datetime | None:
    if not isinstance(value, int | float | str | bytes | bytearray):
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


def _file_modified_at(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _cwd_from_cursor_project_path(root: Path, path: Path) -> Path | None:
    try:
        project_slug = path.relative_to(root).parts[0]
    except ValueError:
        return None
    return _path_from_cursor_project_slug(project_slug)


def _path_from_cursor_project_slug(slug: str) -> Path | None:
    parts = slug.split("-")
    if not parts or parts[0] != "Users":
        return None
    current = Path("/")
    index = 0
    while index < len(parts):
        if not current.exists():
            return None
        match = _longest_child_match(current, parts, index)
        if match is None:
            return None
        current, consumed = match
        index += consumed
    return current


def _longest_child_match(current: Path, parts: list[str], index: int) -> tuple[Path, int] | None:
    try:
        children = list(current.iterdir())
    except OSError:
        return None
    best: tuple[Path, int] | None = None
    for child in children:
        normalized = _cursor_slug_component(child.name)
        token_count = len(normalized.split("-"))
        if "-".join(parts[index : index + token_count]) == normalized:
            if best is None or token_count > best[1]:
                best = (child, token_count)
    return best


def _cursor_slug_component(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-")
