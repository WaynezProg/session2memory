"""Incremental scan: register every recently active session and keep a compact digest.

Unlike the date-driven ``import``, the scan looks at what changed since it last ran, over
every agent's own store, and records each session once in a registry that persists across
days. The digest (the user's requests and the reply each one ended with) is what the
curation step reads later; raw transcripts are never copied.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from session2memory.adapters.antigravity import TRANSCRIPT_GLOB, AntigravityAdapter
from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.codex import ROLLOUT_PATTERNS, CodexAdapter
from session2memory.adapters.grok import CHAT_HISTORY, GrokAdapter
from session2memory.adapters.muse import SESSION_FILE, MuseAdapter
from session2memory.adapters.openclaw_native import OpenClawNativeAdapter
from session2memory.auto.scope import forced_reader, openclaw_roots, within
from session2memory.auto.secrets import scrub_secrets
from session2memory.auto.store import AutoStore, Scope
from session2memory.filtering import is_noise
from session2memory.handoff import _clip, continuation_summary, is_substantive, user_request
from session2memory.models import SessionRecord, SourceHome
from session2memory.session_start import RolloutSummary, read_rollout_summary
from session2memory.state.sources import SourceRegistry

DEFAULT_MAX_FILE_BYTES = 300_000_000
DIGEST_CHARS = 3000
REQUEST_CHARS = 400
REPLY_CHARS = 500


@dataclass(frozen=True)
class ScanSources:
    """Where each agent keeps its sessions. ``None`` disables a source."""

    codex_homes: tuple[SourceHome, ...] = ()
    claude_root: Path | None = None
    grok_root: Path | None = None
    muse_root: Path | None = None
    antigravity_root: Path | None = None
    openclaw_agents_root: Path | None = None
    codex_memory_root: Path | None = None
    # Sessions started in these directories belong to OpenClaw agents.
    openclaw_roots: tuple[Path, ...] = ()


@dataclass
class ScanReport:
    files_seen: int = 0
    files_skipped_large: int = 0
    read_errors: int = 0
    sessions_new: int = 0
    sessions_updated: int = 0
    sessions_unchanged: int = 0
    native_summaries_adopted: int = 0
    by_tool: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


def scope_for(record: SessionRecord, openclaw_roots: tuple[Path, ...]) -> Scope:
    """``openclaw`` for OpenClaw agents' own sessions, ``coding`` for everything else."""
    if (record.agent or "").startswith("openclaw/"):
        return "openclaw"
    if record.cwd is not None and within(record.cwd, openclaw_roots):
        return "openclaw"
    return "coding"


def build_digest(record: SessionRecord, *, max_chars: int = DIGEST_CHARS) -> str:
    """Requests and the reply each ended with; first turn plus as many recent ones as fit."""
    turns: list[tuple[str, str]] = []
    carried: str | None = None
    request: str | None = None
    reply = ""
    after_ack = False
    for message in record.messages:
        if is_noise(message):
            continue
        if message.role == "user":
            summary = continuation_summary(message.text)
            if summary:
                carried = summary
                continue
            text = user_request(message.text)
            if not text:
                continue
            if not is_substantive(text):
                # "Commit", "ok": the reply to it must not replace the reply to the
                # request before it.
                after_ack = True
                continue
            if request is not None:
                turns.append((request, reply))
            request, reply, after_ack = text, "", False
        elif message.role == "assistant" and message.text.strip() and request is not None:
            if not (after_ack and reply):
                reply = message.text
    if request is not None:
        turns.append((request, reply))
    if not turns:
        return ""

    def render(turn: tuple[str, str]) -> str:
        user, assistant = turn
        # Scrub before clipping, so a cut can never strand part of a credential.
        reply_text = _clip(scrub_secrets(assistant), REPLY_CHARS)
        answer = f"\nA: {reply_text}" if assistant.strip() else ""
        return f"U: {_clip(scrub_secrets(user), REQUEST_CHARS)}{answer}"

    head = render(turns[0])
    blocks = [head]
    if carried:
        blocks.insert(0, f"(接續摘要) {_clip(scrub_secrets(carried), 600)}")
    room = max_chars - sum(len(block) + 1 for block in blocks)
    tail: list[str] = []
    # When turns have to be left out, the note saying so counts against the cap as well.
    for reserve in (0, len(f"(中間略過 {len(turns)} 輪)") + 1):
        budget, tail = room - reserve, []
        for turn in reversed(turns[1:]):
            block = render(turn)
            if len(block) + 1 > budget:
                break
            tail.append(block)
            budget -= len(block) + 1
        if len(tail) == len(turns) - 1:
            break
    omitted = len(turns) - 1 - len(tail)
    if omitted > 0:
        blocks.append(f"(中間略過 {omitted} 輪)")
    return "\n".join([*blocks, *reversed(tail)])


def _recent(paths: Iterator[Path], since: float, max_bytes: int, report: ScanReport) -> list[Path]:
    selected: list[Path] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < since:
            continue
        report.files_seen += 1
        if stat.st_size > max_bytes:
            report.files_skipped_large += 1
            continue
        selected.append(path)
    return sorted(selected)


def iter_records(
    sources: ScanSources, *, since: datetime, max_bytes: int, report: ScanReport
) -> Iterator[SessionRecord]:
    """Every session touched since ``since``, across all configured stores."""
    cutoff = since.timestamp()

    def read_all(
        paths: list[Path], reader: Callable[[Path], SessionRecord]
    ) -> Iterator[SessionRecord]:
        for path in paths:
            try:
                yield reader(path)
            except (OSError, UnicodeError, ValueError):
                report.read_errors += 1

    for home in sources.codex_homes:
        if not home.root.is_dir():
            continue
        adapter = CodexAdapter(home.root, homes=[home])
        rollouts = (path for pattern in ROLLOUT_PATTERNS for path in home.root.rglob(pattern))
        yield from read_all(
            _recent(rollouts, cutoff, max_bytes, report),
            lambda path, a=adapter, h=home: a.read_file(path, home=h),  # type: ignore[misc]
        )
    if sources.claude_root is not None and sources.claude_root.is_dir():
        claude = ClaudeAdapter(sources.claude_root)
        files = sources.claude_root.glob("*/*.jsonl")  # subagent transcripts live deeper
        yield from read_all(_recent(files, cutoff, max_bytes, report), claude._read_file)
    if sources.grok_root is not None and sources.grok_root.is_dir():
        grok = GrokAdapter(sources.grok_root)
        files = sources.grok_root.glob(f"*/*/{CHAT_HISTORY}")
        yield from read_all(_recent(files, cutoff, max_bytes, report), grok.read_file)
    if sources.muse_root is not None and sources.muse_root.is_dir():
        muse = MuseAdapter(sources.muse_root)
        root = sources.muse_root
        files = (
            path
            for path in root.glob(f"*/*/*/*/{SESSION_FILE}")
            if not any(part.startswith(".") for part in path.relative_to(root).parts[:-1])
        )
        yield from read_all(_recent(files, cutoff, max_bytes, report), muse.read_file)
    if sources.antigravity_root is not None and sources.antigravity_root.is_dir():
        antigravity = AntigravityAdapter(sources.antigravity_root)
        files = sources.antigravity_root.glob(TRANSCRIPT_GLOB)
        yield from read_all(_recent(files, cutoff, max_bytes, report), antigravity.read_file)
    if sources.openclaw_agents_root is not None and sources.openclaw_agents_root.is_dir():
        native = OpenClawNativeAdapter(sources.openclaw_agents_root)
        for agent, database in native.databases():
            try:
                connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
                try:
                    connection.execute("PRAGMA query_only=1")
                    rows = connection.execute(
                        "SELECT session_id FROM session_windows "
                        "WHERE COALESCE(transcript_updated_at, created_at) >= ?",
                        (int(cutoff * 1000),),
                    ).fetchall()
                finally:
                    connection.close()
            except sqlite3.Error:
                report.read_errors += 1
                continue
            for (native_id,) in rows:
                report.files_seen += 1
                try:
                    record = native.read_session(database, f"{agent}/{native_id}")
                except (sqlite3.Error, OSError, ValueError):
                    report.read_errors += 1
                    continue
                if record is not None:
                    yield record


def _summaries_by_thread(memory_root: Path | None) -> dict[str, RolloutSummary]:
    if memory_root is None:
        return {}
    found: dict[str, RolloutSummary] = {}
    for path in (memory_root / "rollout_summaries").glob("*.md"):
        summary = read_rollout_summary(path)
        if summary is not None:
            found[summary.thread_id] = summary
    return found


def scan(
    registry: SourceRegistry,
    store: AutoStore,
    sources: ScanSources,
    *,
    since: datetime,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> ScanReport:
    """Register sessions changed since ``since`` and refresh their digests."""
    report = ScanReport()
    for home in sources.codex_homes:
        registry.register_home(home)
    native = _summaries_by_thread(sources.codex_memory_root)
    for record in iter_records(sources, since=since, max_bytes=max_bytes, report=report):
        if not record.content_digest:
            continue
        project = record.cwd.resolve(strict=False).as_posix() if record.cwd else None
        observation = registry.observe(record, workspace_id=project)
        known = store.get_session(record.source_key)
        if observation.reason == "first-seen":
            report.sessions_new += 1
        elif observation.has_new_content:
            report.sessions_updated += 1
        elif known is not None:
            report.sessions_unchanged += 1
            # Same content at another location: nothing to re-read, but a coding location
            # still makes this a coding session.
            if scope_for(record, sources.openclaw_roots) == "coding":
                store.widen_session_to_coding(record.source_key)
            continue
        report.by_tool[record.tool] = report.by_tool.get(record.tool, 0) + 1
        try:
            mtime: float | None = record.source_path.stat().st_mtime
        except OSError:
            mtime = None
        # A sqlite store's file time says nothing about one session inside it.
        file_time = mtime if record.source_path.suffix not in {".sqlite", ".db"} else None
        updated = record.updated_at if record.updated_at and record.updated_at.tzinfo else None
        started = record.started_at if record.started_at and record.started_at.tzinfo else None
        if updated is not None:
            last_activity: str | None = updated.astimezone(UTC).isoformat(timespec="seconds")
        elif file_time is not None:
            last_activity = datetime.fromtimestamp(file_time, UTC).isoformat(timespec="seconds")
        elif started is not None:
            last_activity = started.astimezone(UTC).isoformat(timespec="seconds")
        else:
            last_activity = None
        codex_summary = native.get(record.session_id) if record.tool == "codex" else None
        if codex_summary is not None and not (
            codex_summary.updated_at is not None
            and mtime is not None
            and codex_summary.updated_at.timestamp() >= mtime - 600
        ):
            codex_summary = None  # written before the session's latest activity
        scope = scope_for(record, sources.openclaw_roots)
        store.upsert_session(
            source_key=record.source_key,
            tool=record.tool,
            session_id=record.session_id,
            agent=record.agent,
            scope=scope,
            project=project,
            title=codex_summary.title if codex_summary else None,
            digest=build_digest(record),
            version_no=observation.version_no,
            last_activity=last_activity,
            native_summary=_clip(codex_summary.body, 1500) if codex_summary else None,
        )
        if scope == "coding":
            # Whichever path brought the session here, memory it already fed follows it.
            store.widen_session_to_coding(record.source_key)
    # Codex writes its own summary some time after a session ends, when the transcript no
    # longer changes: adopt those too, whether or not the session was read this run.
    for thread_id, summary in native.items():
        known = store.get_session(f"codex:{thread_id}")
        if known is None or summary.updated_at is None:
            continue
        stamp = summary.updated_at.astimezone(UTC).isoformat(timespec="seconds")
        if known.last_activity and stamp < known.last_activity[:19] + "+00:00":
            continue  # written before the session's latest activity
        if store.apply_native_summary(
            known.source_key, title=summary.title, summary=_clip(summary.body, 1500)
        ):
            report.native_summaries_adopted += 1
    return report


__all__ = ["ScanReport", "ScanSources", "build_digest", "forced_reader", "openclaw_roots", "scan"]
