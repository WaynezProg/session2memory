"""Locate the most recent sessions of a workspace directly in each agent's own store.

Handoff has to work right after a session ends, before any import has run, so this does
not depend on the state DB. Every lookup is read-only and bounded: it uses the index the
agent already keeps (Claude's per-project directory, Codex's thread table, Grok's
per-cwd directory) and otherwise inspects only the newest few sessions.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.codex import ROLLOUT_PATTERNS, CodexAdapter
from session2memory.adapters.grok import CHAT_HISTORY, GrokAdapter
from session2memory.adapters.muse import SESSION_FILE, MuseAdapter
from session2memory.adapters.openclaw_native import OpenClawNativeAdapter
from session2memory.filtering import is_noise
from session2memory.handoff import continuation_summary, user_request
from session2memory.models import SessionRecord

DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class FoundSession:
    record: SessionRecord
    recency: float  # seconds since the epoch of the last activity


Finder = Callable[[Path, Path, int], Iterator[FoundSession]]


def _same_path(left: Path | None, right: Path) -> bool:
    return left is not None and left.resolve(strict=False) == right.resolve(strict=False)


def _newest(paths: Iterator[Path] | list[Path], limit: int) -> list[tuple[float, Path]]:
    dated: list[tuple[float, Path]] = []
    for path in paths:
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return sorted(dated, reverse=True)[:limit]


def _read_each(
    paths: list[tuple[float, Path]], reader: Callable[[Path], SessionRecord]
) -> Iterator[FoundSession]:
    for mtime, path in paths:
        try:
            yield FoundSession(reader(path), mtime)
        except (OSError, UnicodeError, ValueError):
            continue


def find_claude(root: Path, workspace: Path, limit: int) -> Iterator[FoundSession]:
    """Claude Code keeps one directory per workspace, named after the path."""
    encoded = re.sub(r"[^A-Za-z0-9]", "-", workspace.resolve(strict=False).as_posix())
    adapter = ClaudeAdapter(root)
    # ``foo-bar`` and ``foo/bar`` share a directory name, so the cwd decides.
    for found in _read_each(_newest((root / encoded).glob("*.jsonl"), limit), adapter._read_file):
        if _same_path(found.record.cwd, workspace):
            yield found


def _codex_thread_paths(root: Path, workspace: Path, limit: int) -> list[Path] | None:
    databases = sorted(root.parent.glob("state_*.sqlite"))
    if not databases:
        return None
    try:
        connection = sqlite3.connect(f"file:{databases[-1].as_posix()}?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=1")
            rows = connection.execute(
                "SELECT rollout_path FROM threads WHERE cwd=? ORDER BY updated_at DESC LIMIT ?",
                (workspace.resolve(strict=False).as_posix(), limit),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    paths: list[Path] = []
    for (raw,) in rows:
        if not isinstance(raw, str):
            continue
        # The thread table keeps the original ``.jsonl`` path after native compression.
        for candidate in (Path(raw), Path(raw + ".zst")):
            if candidate.exists():
                paths.append(candidate)
                break
    return paths


def find_codex(root: Path, workspace: Path, limit: int) -> Iterator[FoundSession]:
    adapter = CodexAdapter(root)
    indexed = _codex_thread_paths(root, workspace, limit)
    if indexed:
        candidates = _newest(indexed, limit)
    else:  # no thread table: fall back to the newest rollouts on disk
        rollouts = [path for pattern in ROLLOUT_PATTERNS for path in root.rglob(pattern)]
        candidates = _newest(rollouts, limit * 3)
    for found in _read_each(candidates, adapter.read_file):
        if _same_path(found.record.cwd, workspace):
            yield found


def find_grok(root: Path, workspace: Path, limit: int) -> Iterator[FoundSession]:
    """Grok keeps one directory per workspace, named after the URL-encoded path."""
    directory = root / quote(workspace.resolve(strict=False).as_posix(), safe="")
    adapter = GrokAdapter(root)
    yield from _read_each(_newest(directory.glob(f"*/{CHAT_HISTORY}"), limit), adapter.read_file)


def find_muse(root: Path, workspace: Path, limit: int) -> Iterator[FoundSession]:
    """Muse has no per-workspace index: inspect the newest sessions and match their cwd."""
    adapter = MuseAdapter(root)
    dated = [
        path
        for path in root.glob(f"*/*/*/*/{SESSION_FILE}")
        if not any(part.startswith(".") for part in path.relative_to(root).parts[:-1])
    ]
    for found in _read_each(_newest(dated, limit * 3), adapter.read_file):
        if _same_path(found.record.cwd, workspace):
            yield found


def find_openclaw_native(root: Path, workspace: Path, limit: int) -> Iterator[FoundSession]:
    """Look at the few most recent sessions of every agent store and match their cwd."""
    adapter = OpenClawNativeAdapter(root)
    per_agent = max(limit // 5, 2)
    for agent, database in adapter.databases():
        try:
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
            try:
                connection.execute("PRAGMA query_only=1")
                rows = connection.execute(
                    "SELECT session_id FROM session_windows ORDER BY created_at DESC LIMIT ?",
                    (per_agent,),
                ).fetchall()
            finally:
                connection.close()
            for (native_id,) in rows:
                record = adapter.read_session(database, f"{agent}/{native_id}")
                if record is None or not _same_path(record.cwd, workspace):
                    continue
                moment = record.updated_at or record.started_at
                yield FoundSession(record, moment.timestamp() if moment else 0.0)
        except (sqlite3.Error, OSError, ValueError):
            continue


FINDERS: Mapping[str, Finder] = {
    "claude": find_claude,
    "codex": find_codex,
    "grok": find_grok,
    "muse": find_muse,
    "openclaw-native": find_openclaw_native,
}
# Searched when no tool is named. ``openclaw-native`` holds agent-private sessions and is
# only searched when asked for explicitly.
DEFAULT_HANDOFF_TOOLS = ("codex", "claude", "grok", "muse")


def has_conversation(record: SessionRecord) -> bool:
    """True when the session holds a real request or reply, not only injected context."""
    for message in record.messages:
        if is_noise(message):
            continue
        if message.role == "assistant" and message.text.strip():
            return True
        if message.role == "user" and continuation_summary(message.text):
            continue
        if message.role == "user" and user_request(message.text):
            return True
    return False


def recent_sessions(
    tool: str, root: Path, workspace: Path, *, limit: int = DEFAULT_LIMIT
) -> list[FoundSession]:
    """Sessions of ``workspace`` in one tool's store, newest first."""
    finder = FINDERS.get(tool)
    if finder is None or not root.exists():
        return []
    return sorted(finder(root, workspace, limit), key=lambda found: found.recency, reverse=True)


def latest_session(
    roots: Mapping[str, Path], workspace: Path, *, limit: int = DEFAULT_LIMIT
) -> FoundSession | None:
    """The most recent session with actual conversation across the given tools."""
    best: FoundSession | None = None
    for tool, root in roots.items():
        for found in recent_sessions(tool, root, workspace, limit=limit):
            if not has_conversation(found.record):
                continue
            if best is None or found.recency > best.recency:
                best = found
            break  # sessions are newest first: the first usable one is this tool's latest
    return best


def find_session(
    tool: str, root: Path, workspace: Path, session_id: str, *, limit: int = 200
) -> SessionRecord | None:
    for found in recent_sessions(tool, root, workspace, limit=limit):
        if found.record.session_id == session_id:
            return found.record
    return None


class SessionSelectionError(LookupError):
    """``--session`` matched no session, or more than one."""


def split_session_spec(spec: str) -> tuple[str | None, str]:
    """``<tool>:<id>`` -> ``(tool, id)``; a bare id -> ``(None, id)``."""
    tool, colon, rest = spec.strip().partition(":")
    if colon and tool in FINDERS and rest:
        return tool, rest
    return None, spec.strip()


def select_session(
    roots: Mapping[str, Path], workspace: Path, spec: str, *, limit: int = 200
) -> FoundSession:
    """The session of ``workspace`` whose id equals ``spec`` or uniquely starts with it."""
    tool, wanted = split_session_spec(spec)
    available: list[FoundSession] = []
    for name, root in roots.items():
        if tool is None or name == tool:
            available.extend(recent_sessions(name, root, workspace, limit=limit))
    exact = [found for found in available if found.record.session_id == wanted]
    matches = exact or [f for f in available if f.record.session_id.startswith(wanted)]
    if len(matches) == 1:
        return matches[0]
    listing = matches if matches else available
    newest = sorted(listing, key=lambda found: found.recency, reverse=True)[:8]
    shown = ", ".join(found.record.source_key for found in newest) or "(none)"
    if matches:
        raise SessionSelectionError(f"session id {wanted!r} is ambiguous; it matches: {shown}")
    raise SessionSelectionError(
        f"no session {wanted!r} for this workspace; most recent sessions: {shown}"
    )

