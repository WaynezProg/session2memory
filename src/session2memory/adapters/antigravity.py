from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from session2memory.adapters.base import (
    ContentDigest,
    file_session_touches_date,
    make_message,
    parse_datetime,
    read_jsonl,
    skipped_file_reason,
)
from session2memory.models import SessionMessage, SessionRecord

TRANSCRIPT = "transcript_full.jsonl"
TRANSCRIPT_GLOB = f"brain/*/.system_generated/logs/{TRANSCRIPT}"
SUMMARIES_DB = "conversation_summaries.db"
HOME_ID = "antigravity@cli"
# Rows of the transcript that are conversation; tool output (GENERIC) and errors are not.
ROLES = {"USER_INPUT": "user", "PLANNER_RESPONSE": "assistant"}
_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


class AntigravityAdapter:
    """Reads Antigravity CLI conversations, read-only.

    ``root`` is the CLI's data directory (``~/.gemini/antigravity-cli``). Each conversation
    has a plain transcript at ``brain/<conversation id>/.system_generated/logs/
    transcript_full.jsonl`` next to the protobuf store the CLI itself works from; the
    workspace and title come from ``conversation_summaries.db``. A message is addressed by
    the step it belongs to (``step:<n>``), which the CLI keeps stable.
    """

    tool = "antigravity"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        if not self.root.is_dir():
            return
        for path in sorted(self.root.glob(TRANSCRIPT_GLOB)):
            try:
                record = self.read_file(path)
            except (OSError, UnicodeError, ValueError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if file_session_touches_date(record, date):
                yield record

    def read_file(self, path: Path) -> SessionRecord:
        session_id = conversation_id(path)
        info = conversation_info(data_root(path), session_id)
        cwd = info.workspace
        digest = ContentDigest()
        messages: list[SessionMessage] = []
        first: datetime | None = None
        last: datetime | None = None
        for line_number, row in read_jsonl(path, on_line=digest.add):
            moment = _time(row.get("created_at"))
            if moment is not None:
                first = first or moment
                last = moment
            role = ROLES.get(str(row.get("type")))
            content = row.get("content")
            if role is None or not isinstance(content, str):
                continue
            text = _request(content) if role == "user" else content.strip()
            if not text:
                continue
            step = row.get("step_index")
            messages.append(
                make_message(
                    tool=self.tool,
                    session_id=session_id,
                    source_path=path,
                    line_number=line_number,
                    role=role,
                    text=text,
                    timestamp=moment,
                    cwd=cwd,
                    locator=f"step:{step if step is not None else line_number}",
                )
            )
        return SessionRecord(
            tool=self.tool,
            session_id=session_id,
            source_path=path,
            started_at=first,
            updated_at=info.modified or last or first,
            cwd=cwd,
            repo_root=None,
            tool_workspace_id=None,
            messages=messages,
            home_id=HOME_ID,
            agent=self.tool,
            content_digest=digest.value(),
            record_count=digest.record_count,
        )

    def read_session(self, path: Path, session_id: str) -> SessionRecord | None:
        record = self.read_file(path)
        return record if record.session_id == session_id else None


class ConversationInfo:
    def __init__(
        self,
        workspace: Path | None = None,
        title: str | None = None,
        modified: datetime | None = None,
    ) -> None:
        self.workspace = workspace
        self.title = title
        self.modified = modified


def conversation_id(transcript: Path) -> str:
    """``brain/<id>/.system_generated/logs/transcript_full.jsonl`` → ``<id>``."""
    return transcript.parents[2].name


def data_root(transcript: Path) -> Path:
    return transcript.parents[4]


def transcript_path(root: Path, session_id: str) -> Path:
    return root / "brain" / session_id / ".system_generated" / "logs" / TRANSCRIPT


def _connect(root: Path) -> sqlite3.Connection | None:
    database = root / SUMMARIES_DB
    if not database.is_file():
        return None
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2)
    connection.execute("PRAGMA query_only=1")
    return connection


def _workspace(raw: object) -> Path | None:
    """The first local folder of ``workspace_uris`` (a JSON list of ``file://`` URIs)."""
    try:
        uris = json.loads(str(raw)) if raw else []
    except json.JSONDecodeError:
        return None
    for uri in uris if isinstance(uris, list) else []:
        parsed = urlparse(str(uri))
        if parsed.scheme == "file" and parsed.path:
            return Path(unquote(parsed.path))
    return None


def conversation_info(root: Path, session_id: str) -> ConversationInfo:
    """Workspace, title and last change of a conversation; empty when the index lacks it."""
    try:
        connection = _connect(root)
        if connection is None:
            return ConversationInfo()
        try:
            row = connection.execute(
                "SELECT workspace_uris, title, last_modified_time FROM conversation_summaries "
                "WHERE conversation_id=?",
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return ConversationInfo()
    if row is None:
        return ConversationInfo()
    title = str(row[1]).strip() if row[1] else None
    return ConversationInfo(_workspace(row[0]), title or None, _time(row[2]))


def conversations_of(root: Path, workspace: Path, limit: int = 5) -> list[str]:
    """Conversation ids started in ``workspace``, most recently changed first."""
    wanted = workspace.resolve(strict=False)
    try:
        connection = _connect(root)
        if connection is None:
            return []
        try:
            rows = connection.execute(
                "SELECT conversation_id, workspace_uris FROM conversation_summaries "
                "ORDER BY last_modified_time DESC"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    found: list[str] = []
    for identifier, uris in rows:
        folder = _workspace(uris)
        if folder is not None and folder.resolve(strict=False) == wanted:
            found.append(str(identifier))
            if len(found) >= limit:
                break
    return found


def _request(content: str) -> str:
    """What the person typed: the CLI wraps it and appends its own metadata blocks."""
    match = _REQUEST.search(content)
    return (match.group(1) if match else content).strip()


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_datetime(value.replace(" ", "T", 1))
    except ValueError:
        return None
