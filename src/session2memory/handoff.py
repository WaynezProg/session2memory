"""Manual cross-agent handoff packet. Rule-based: no LLM, no hooks, read-only.

A packet describes the most recent session of a workspace so another agent can continue
it. It is built from what the source agent already recorded (title, summary where one
exists) plus the structure of the conversation itself: the first goal, the last few
requests and the last assistant reply. Every quoted item carries an evidence reference
(``<tool>:<session id>#<locator>``) that ``handoff-show`` resolves back to the full
message, so the packet can stay short.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from session2memory.extraction import extract_candidates
from session2memory.filtering import is_noise
from session2memory.models import SessionMessage, SessionRecord
from session2memory.state.sources import message_locator
from session2memory.workspace import resolve_workspace

DEFAULT_MAX_CHARS = 4000
MIN_MAX_CHARS = 600
ESSENTIAL = 80  # goal, latest request, last reply: shortened, not dropped
MIN_ITEM_CHARS = 24
# Wrappers whose *content* is the user's request; any other leading <tag>…</tag> block is
# harness-injected context (reminders, environment, notifications) and is skipped.
REQUEST_TAGS = ("user_query", "pasted_content")
INJECTED_TAGS = ("heartbeat", "skill")
# A compacted session restarts with its own summary as a user message.
CONTINUATION_PREFIXES = ("This session is being continued from a previous conversation",)
_TRAILING_BLOCK = re.compile(
    r"<([A-Za-z_][\w:\-]*)(?:\s[^>]*)?>(?:(?!<\1[\s>]).)*?</\1(?:\s[^>]*)?>\s*\Z", flags=re.DOTALL
)
_REQUEST_HEADING = re.compile(r"^#{1,3} My request:[ \t]*$", flags=re.MULTILINE)
_OPEN_TAG = re.compile(r"<([A-Za-z_][\w:\-]*)(?:\s[^>]*)?>")
KIND_LABELS = {
    "decision": "決定",
    "constraint": "限制",
    "pitfall": "坑",
    "completed": "完成",
    "verification": "驗證",
}


@dataclass
class _Line:
    priority: int
    prefix: str
    text: str
    suffix: str = ""

    def render(self) -> str:
        return f"{self.prefix}{self.text}{self.suffix}"


@dataclass(frozen=True)
class NativeSummary:
    """What the source agent itself recorded about the session."""

    title: str | None = None
    summary: str | None = None
    last_turn: str | None = None
    origin: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.title or self.summary or self.last_turn)


SummaryReader = Callable[[SessionRecord], NativeSummary]


def user_request(text: str) -> str | None:
    """The part of a user message the user actually wrote, or ``None`` if it is all wrapper."""
    remaining = text.strip()
    # Codex prefixes attached context ("## Referenced ChatGPT conversation: …") and then
    # marks where the user's own words start.
    marked = _REQUEST_HEADING.split(remaining, maxsplit=1)
    if len(marked) == 2 and marked[1].strip():
        remaining = marked[1].strip()
    for tag in REQUEST_TAGS:
        # Some harnesses repeat the attributes in the closing tag (``</tag id="…">``).
        match = re.search(
            rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}(?:\s[^>]*)?>", remaining, flags=re.DOTALL
        )
        if match and match.group(1).strip():
            # Keep going: injected blocks can sit inside the request wrapper too.
            remaining = match.group(1).strip()
            break
    while True:
        match = _OPEN_TAG.match(remaining)
        if match is None:
            break
        closing = re.compile(rf"</{re.escape(match.group(1))}(?:\s[^>]*)?>")
        end = closing.search(remaining, match.end())
        if end is None:
            break
        remaining = remaining[end.end() :].lstrip()
    # Harnesses also append context after the user's words (``… <runtime_info>…</runtime_info>``).
    while True:
        tail = _TRAILING_BLOCK.search(remaining)
        if tail is None or tail.group(1) in REQUEST_TAGS or not _is_injected_tag(tail.group(1)):
            break
        remaining = remaining[: tail.start()].rstrip()
    return remaining or None


def _is_injected_tag(name: str) -> bool:
    """Harness tags use snake_case or kebab-case; plain HTML/XML the user typed does not."""
    return "_" in name or "-" in name or name in INJECTED_TAGS


def continuation_summary(text: str) -> str | None:
    """The carried-over summary of a compacted session, which arrives as a user message."""
    stripped = text.strip()
    return stripped if stripped.startswith(CONTINUATION_PREFIXES) else None


def _clip(text: str, limit: int) -> str:
    flattened = " ".join(text.split())
    if len(flattened) <= limit:
        return flattened
    return flattened[: max(limit - 1, 0)] + "…"


def _reference(record: SessionRecord, message: SessionMessage) -> str:
    return f"{record.source_key}#{message_locator(message)}"


def build_packet(
    record: SessionRecord,
    native: NativeSummary | None = None,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    recent_requests: int = 3,
) -> str:
    """Render the packet, never longer than ``max_chars`` characters."""
    if max_chars < MIN_MAX_CHARS:
        raise ValueError(f"max_chars must be at least {MIN_MAX_CHARS}")
    native = native or NativeSummary()
    requests: list[tuple[SessionMessage, str]] = []
    replies: list[SessionMessage] = []
    carried_over: str | None = None
    for message in record.messages:
        if is_noise(message):
            continue
        if message.role == "user":
            carried = continuation_summary(message.text)
            if carried:
                carried_over = carried
                continue
            request = user_request(message.text)
            if request:
                requests.append((message, request))
        elif message.role == "assistant" and message.text.strip():
            replies.append(message)

    updated = record.updated_at or record.started_at
    header = [
        "# Handoff",
        f"workspace: {record.cwd.as_posix() if record.cwd else '(unknown)'}",
        f"from: {record.tool} session {record.session_id}"
        + (f" (agent {record.agent})" if record.agent and record.agent != record.tool else "")
        + (f", last activity {updated.isoformat(timespec='seconds')}" if updated else ""),
        "note: 規則產生，未經模型摘要。需要原文時用 evidence 參照回查，不要整段重讀 Session。",
    ]
    # Optional lines are dropped first when over budget; essential ones are shortened.
    body: list[_Line] = []
    if native.title:
        body.append(_Line(70, "標題: ", _clip(native.title, 80)))
    if native.summary:
        origin = f"（{native.origin}）" if native.origin else ""
        body.append(_Line(60, f"原生摘要{origin}: ", _clip(native.summary, max_chars * 3 // 10)))
    elif carried_over:
        body.append(_Line(60, "原生摘要（接續摘要）: ", _clip(carried_over, max_chars * 3 // 10)))
    if native.last_turn:
        body.append(_Line(55, "上一輪摘要: ", _clip(native.last_turn, 240)))
    if requests:
        first, text = requests[0]
        ref = f" [{_reference(record, first)}]"
        body.append(_Line(100, "目標: ", _clip(text, max_chars * 15 // 100), ref))
    tail = requests[1:][-recent_requests:]
    for position, (message, text) in enumerate(tail):
        priority = ESSENTIAL if position == len(tail) - 1 else 40 + position
        ref = f" [{_reference(record, message)}]"
        body.append(_Line(priority, "最近的要求: ", _clip(text, max_chars * 8 // 100), ref))
    if replies:
        last = replies[-1]
        ref = f" [{_reference(record, last)}]"
        body.append(_Line(85, "最後的回覆: ", _clip(last.text, max_chars * 25 // 100), ref))
    by_index = {message.index: message for message in record.messages}
    for candidate in extract_candidates(record, resolve_workspace(record))[-8:]:
        source = by_index.get(candidate.evidence.message_start)
        ref = f" [{_reference(record, source)}]" if source is not None else ""
        label = KIND_LABELS.get(candidate.kind, candidate.kind)
        body.append(_Line(30, f"{label}: ", _clip(candidate.text, 200), ref))
    if not requests and not replies and native.is_empty and not carried_over:
        body.append(_Line(100, "", "（這個 Session 沒有可用的對話內容）"))

    def render() -> str:
        return "\n".join([*header, *(line.render() for line in body)])

    while len(render()) > max_chars and any(line.priority < ESSENTIAL for line in body):
        del body[min(range(len(body)), key=lambda i: body[i].priority)]
    while (overflow := len(render()) - max_chars) > 0:
        longest = max(body, key=lambda line: len(line.text), default=None)
        if longest is None or len(longest.text) <= MIN_ITEM_CHARS:
            break
        longest.text = _clip(longest.text, max(len(longest.text) - overflow, MIN_ITEM_CHARS))
    while len(render()) > max_chars and len(body) > 1:
        del body[min(range(len(body)), key=lambda i: body[i].priority)]
    packet = render()
    return packet if len(packet) <= max_chars else packet[: max_chars - 1] + "…"


# ---- native summaries ------------------------------------------------------------------


def _clean(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def grok_summary(record: SessionRecord) -> NativeSummary:
    try:
        loaded = json.loads((record.source_path.parent / "summary.json").read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return NativeSummary()
    if not isinstance(loaded, dict):
        return NativeSummary()
    title = _clean(loaded.get("generated_title"))
    summary = _clean(loaded.get("session_summary"))
    return NativeSummary(
        title=title or summary,
        summary=summary if summary != title else None,  # Grok usually repeats the title here
        last_turn=_clean(loaded.get("last_turn_summary")),
        origin="grok summary.json",
    )


def codex_summary(record: SessionRecord) -> NativeSummary:
    """Thread name/title from the ``state_*.sqlite`` of the CODEX_HOME holding the rollout."""
    for directory in list(record.source_path.parents)[:6]:
        databases = sorted(directory.glob("state_*.sqlite"))
        if not databases:
            continue
        try:
            connection = sqlite3.connect(f"file:{databases[-1].as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return NativeSummary()
        try:
            connection.execute("PRAGMA query_only=1")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")}
            wanted = [c for c in ("name", "title", "first_user_message") if c in columns]
            if not wanted:
                return NativeSummary()
            row = connection.execute(
                f"SELECT {', '.join(wanted)} FROM threads WHERE id=?", (record.session_id,)
            ).fetchone()
        except sqlite3.Error:
            return NativeSummary()
        finally:
            connection.close()
        if row is None:
            return NativeSummary()
        values = dict(zip(wanted, row, strict=True))
        title = _clean(values.get("title"))
        first = _clean(values.get("first_user_message"))
        if title and first and first.startswith(title):
            title = None  # Codex stores the first user message as the title: not a summary
        return NativeSummary(title=_clean(values.get("name")) or title, origin="codex threads")
    return NativeSummary()


def claude_summary(record: SessionRecord) -> NativeSummary:
    """Title records and, when the session was compacted, Claude Code's own summary."""
    from session2memory.adapters.base import read_jsonl, text_from_content

    title: str | None = None
    summary: str | None = None
    try:
        for _, event in read_jsonl(record.source_path):
            kind = event.get("type")
            if kind == "custom-title":
                title = _clean(event.get("customTitle")) or title
            elif kind == "ai-title":
                title = title or _clean(event.get("aiTitle"))
            elif kind == "user" and event.get("isCompactSummary"):
                message = event.get("message")
                if isinstance(message, dict):
                    summary = _clean(text_from_content(message.get("content"))) or summary
    except (OSError, UnicodeError, ValueError):
        return NativeSummary()
    return NativeSummary(title=title, summary=summary, origin="claude compact summary")


def muse_summary(record: SessionRecord) -> NativeSummary:
    from session2memory.adapters.base import read_jsonl

    name: str | None = None
    try:
        for _, event in read_jsonl(record.source_path):
            if event.get("payload_type") == "session.name.changed":
                payload = event.get("payload")
                if isinstance(payload, dict):
                    name = _clean(payload.get("new_name")) or name
    except (OSError, UnicodeError, ValueError):
        return NativeSummary()
    return NativeSummary(title=name, origin="muse session name")


SUMMARY_READERS: Mapping[str, SummaryReader] = {
    "grok": grok_summary,
    "codex": codex_summary,
    "claude": claude_summary,
    "claude-desktop": claude_summary,
    "muse": muse_summary,
}


def native_summary(record: SessionRecord) -> NativeSummary:
    reader = SUMMARY_READERS.get(record.tool)
    return reader(record) if reader is not None else NativeSummary()


def find_message(record: SessionRecord, locator: str) -> SessionMessage | None:
    for message in record.messages:
        if message_locator(message) == locator:
            return message
    return None


def parse_reference(reference: str) -> tuple[str, str, str]:
    """``<tool>:<session id>#<locator>`` -> ``(tool, session id, locator)``."""
    key, separator, locator = reference.strip().strip("[]").partition("#")
    tool, colon, session_id = key.partition(":")
    if not (separator and colon and tool and session_id and locator):
        raise ValueError("reference must look like <tool>:<session id>#<locator>")
    return tool, session_id, locator
