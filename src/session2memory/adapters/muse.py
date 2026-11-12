from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from session2memory.adapters.base import (
    ContentDigest,
    file_session_touches_date,
    jsonl_candidate_paths,
    make_message,
    parse_datetime,
    read_jsonl,
    skipped_file_reason,
)
from session2memory.models import SessionMessage, SessionRecord

SESSION_FILE = "session.jsonl"
FRAME_INDEX_BASE = 1000
HOME_ID = "muse@main"


class MuseAdapter:
    """Reads Muse sessions: ``<root>/YYYY/MM/DD/<session id>/session.jsonl``.

    The file is an event log. A user turn is the ``run`` / ``started`` event (``prompt``);
    an assistant turn is ``assistant_message_committed`` (``text``). Every record has its
    own ``id``, which is the message locator (``rec:<id>``). ``retained_frame`` lines wrap
    ordinary records as ``children[].record_json`` and are unwrapped.
    """

    tool = "muse"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        year, month, day = date.split("-")
        for path in jsonl_candidate_paths(
            self.root,
            date=date,
            primary_patterns=(f"{year}/{month}/{day}/*/{SESSION_FILE}",),
            modified_patterns=(f"*/*/*/*/{SESSION_FILE}",),
            exclude=lambda candidate: _in_hidden_directory(candidate, self.root),
        ):
            try:
                record = self.read_file(path)
            except (OSError, UnicodeError, ValueError) as exc:
                self.skipped.append(skipped_file_reason(self.tool, path, exc))
                continue
            if file_session_touches_date(record, date):
                yield record

    def read_file(self, path: Path) -> SessionRecord:
        session_id = path.parent.name
        cwd: Path | None = None
        started_at: datetime | None = None
        updated_at: datetime | None = None
        digest = ContentDigest()
        pending: list[tuple[int, str, str, datetime | None, str]] = []
        for line_number, line in read_jsonl(path, on_line=digest.add):
            framed = "retained_frame" in line
            for child, record in enumerate(_unwrap(line), start=1):
                # Records wrapped in one frame share a physical line; give each its own
                # index so that evidence links stay one-to-one.
                index = line_number * FRAME_INDEX_BASE + child if framed else line_number
                recorded_at = _time(record.get("recorded_at"))
                started_at = started_at or recorded_at
                updated_at = recorded_at or updated_at
                stream = record.get("stream")
                if isinstance(stream, dict) and stream.get("kind") == "session":
                    session_id = str(stream.get("id") or session_id)
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                cwd = cwd or _workspace(payload)
                turn = _turn(payload)
                record_id = record.get("id")
                if turn is None or not isinstance(record_id, str):
                    continue
                pending.append((index, turn[0], turn[1], recorded_at, f"rec:{record_id}"))
        messages: list[SessionMessage] = [
            make_message(
                tool=self.tool,
                session_id=session_id,
                source_path=path,
                line_number=line_number,
                role=role,
                text=text,
                timestamp=timestamp,
                cwd=cwd,
                locator=locator,
            )
            for line_number, role, text, timestamp, locator in pending
        ]
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

    def read_session(self, path: Path, session_id: str) -> SessionRecord | None:
        record = self.read_file(path)
        return record if record.session_id == session_id else None


def _in_hidden_directory(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return any(part.startswith(".") for part in parts[:-1])


def _unwrap(line: dict[str, object]) -> Iterator[dict[str, object]]:
    children = line.get("children")
    if "retained_frame" not in line or not isinstance(children, list):
        yield line
        return
    for child in children:
        if not isinstance(child, dict) or not isinstance(child.get("record_json"), str):
            continue
        try:
            loaded = json.loads(child["record_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            yield loaded


def _workspace(payload: dict[str, object]) -> Path | None:
    """``workspace_root`` (session metadata) or ``cwd`` (route facts), wherever it is nested."""
    for holder in (payload.get("record"), payload.get("event"), payload):
        if not isinstance(holder, dict):
            continue
        for key in ("workspace_root", "cwd"):
            value = holder.get(key)
            if isinstance(value, str) and value:
                return Path(value)
    return None


def _turn(payload: dict[str, object]) -> tuple[str, str] | None:
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    kind = event.get("kind")
    if payload.get("kind") == "run" and kind == "started":
        prompt = event.get("prompt")
        return ("user", prompt) if isinstance(prompt, str) and prompt.strip() else None
    if kind == "assistant_message_committed":
        text = event.get("text")
        return ("assistant", text) if isinstance(text, str) and text.strip() else None
    return None


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None
