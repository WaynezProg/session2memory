from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from string import hexdigits
from typing import cast

from session2memory.adapters.base import (
    ContentDigest,
    file_session_touches_date,
    jsonl_candidate_paths,
    make_message,
    read_jsonl,
    skipped_file_reason,
)
from session2memory.models import SessionMessage, SessionRecord

_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_WORKSPACE_PATH_RE = re.compile(r"^Workspace Path:\s*(.+?)\s*$", re.MULTILINE)
STORE_GLOB = "*/*/store.db"
TRANSCRIPT_GLOBS = ("*/*/*.jsonl", "*/agent-transcripts/*/*.jsonl")
HOME_ID = "cursor@main"
CLI_HOME_ID = "cursor-cli@main"


class CursorAdapter:
    """Reads Cursor chat stores: ``<root>/<workspace hash>/<session id>/store.db``.

    Every store holds one session. Blobs are addressed by their id (``blob:<id>``), which
    does not depend on where the store lies; the digest covers every blob in store order.
    """

    tool = "cursor"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        if not self.root.exists():
            return
        for path in sorted(self.root.glob(STORE_GLOB)):
            try:
                record = self.read_file(path)
            except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if file_session_touches_date(record, date):
                yield record

    def read_session(self, path: Path, session_id: str) -> SessionRecord | None:
        record = self.read_file(path)
        return record if record.session_id == session_id else None

    def read_file(self, path: Path) -> SessionRecord:
        # Read-only: the store belongs to Cursor, which may have it open.
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            connection.execute("PRAGMA query_only=1")
            meta = _read_cursor_meta(connection)
            session_id = str(meta.get("agentId") or path.parent.name)
            started_at = _datetime_from_millis(meta.get("createdAt"))
            updated_at = _file_modified_at(path)
            cwd: Path | None = None
            messages: list[SessionMessage] = []
            digest = ContentDigest()
            rows = connection.execute("select rowid, id, data from blobs order by rowid")
            for rowid, blob_id, data in rows:
                digest.add(f"{blob_id}:{_blob_hash(data)}")
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
                        locator=f"blob:{blob_id}",
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
                home_id=HOME_ID,
                agent=self.tool,
                content_digest=digest.value(),
                record_count=digest.record_count,
            )
        finally:
            connection.close()


class CursorCliAdapter:
    """Reads Cursor agent transcripts (JSONL). Records carry no id, so a message is
    addressed by its position in the file (``msg:<n>``)."""

    tool = "cursor-cli"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        for path in self._candidate_paths(date):
            try:
                record = self.read_file(path)
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
            modified_patterns=TRANSCRIPT_GLOBS,
        )

    def read_session(self, path: Path, session_id: str) -> SessionRecord | None:
        record = self.read_file(path)
        return record if record.session_id == session_id else None

    def read_file(self, path: Path) -> SessionRecord:
        session_id = path.stem
        cwd = _cwd_from_cursor_project_path(self.root, path)
        updated_at = _file_modified_at(path)
        messages: list[SessionMessage] = []
        digest = ContentDigest()
        position = -1
        for line_number, event in read_jsonl(path, on_line=digest.add):
            position += 1
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
                    locator=f"msg:{position}",
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
            home_id=CLI_HOME_ID,
            agent=self.tool,
            content_digest=digest.value(),
            record_count=digest.record_count,
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


def _blob_hash(value: object) -> str:
    raw = bytes(value) if isinstance(value, bytes | bytearray | memoryview) else str(value).encode()
    return sha256(raw).hexdigest()


def session_title(path: Path) -> str | None:
    """The name Cursor gave the chat in ``store.db``, if any."""
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            name = _read_cursor_meta(connection).get("name")
        finally:
            connection.close()
    except (sqlite3.Error, OSError, UnicodeError, ValueError):
        return None
    return name.strip() if isinstance(name, str) and name.strip() else None


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
