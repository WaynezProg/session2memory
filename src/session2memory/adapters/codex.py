from __future__ import annotations

from collections.abc import Iterator, Sequence
from hashlib import sha256
from pathlib import Path

from session2memory.adapters.base import (
    file_session_touches_date,
    jsonl_candidate_paths,
    jsonl_stem,
    make_message,
    parse_datetime,
    read_jsonl,
    skipped_file_reason,
)
from session2memory.models import SessionMessage, SessionRecord, SourceHome

MAIN_HOME_ID = "codex@main"
ROLLOUT_PATTERNS = ("*.jsonl", "*.jsonl.zst")


class CodexAdapter:
    """Reads Codex rollouts (plain or natively zstd-compressed) from one or more CODEX_HOMEs.

    ``root`` is the main ``sessions`` directory. ``homes`` adds further homes (for example
    the per-agent ``codex-home`` directories OpenClaw keeps); when given it replaces the
    implicit main home, so pass the main home explicitly if it should be scanned too.
    """

    tool = "codex"

    def __init__(self, root: Path, homes: Sequence[SourceHome] | None = None) -> None:
        self.root = root
        self.homes: tuple[SourceHome, ...] = (
            tuple(homes)
            if homes is not None
            else (SourceHome(home_id=MAIN_HOME_ID, tool=self.tool, agent="codex", root=root),)
        )
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        for home in self.homes:
            for path in self._candidate_paths(home.root, date):
                try:
                    record = self.read_file(path, home=home)
                except (OSError, UnicodeError, ValueError) as exc:
                    self.skipped.append(skipped_file_reason(self.tool, path, exc))
                    continue
                if file_session_touches_date(record, date):
                    yield record

    def _candidate_paths(self, root: Path, date: str) -> list[Path]:
        year, month, day = date.split("-")
        return jsonl_candidate_paths(
            root,
            date=date,
            primary_patterns=tuple(f"{year}/{month}/{day}/{p}" for p in ROLLOUT_PATTERNS),
            modified_patterns=tuple(f"**/{p}" for p in ROLLOUT_PATTERNS),
        )

    def read_file(self, path: Path, *, home: SourceHome | None = None) -> SessionRecord:
        session_id = jsonl_stem(path)
        cwd: Path | None = None
        started_at = None
        updated_at = None
        messages: list[SessionMessage] = []
        content = sha256()
        record_count = 0

        def on_line(line: str) -> None:
            nonlocal record_count
            record_count += 1
            content.update(line.encode("utf-8"))
            content.update(b"\n")

        pending: list[tuple[int, str, str, str | None]] = []
        for line_number, event in read_jsonl(path, on_line=on_line):
            if event.get("type") == "session_meta":
                payload = event.get("payload")
                if isinstance(payload, dict):
                    session_id = str(payload.get("id") or session_id)
                    cwd_value = payload.get("cwd")
                    cwd = Path(str(cwd_value)) if cwd_value else cwd
                    timestamp = payload.get("timestamp")
                    started_at = parse_datetime(str(timestamp)) if timestamp else started_at
            if event.get("type") != "response_item":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            content_blocks = payload.get("content")
            texts: list[str] = []
            if isinstance(content_blocks, list):
                for block in content_blocks:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str):
                            texts.append(text)
            if texts:
                ordinal = event.get("ordinal")
                locator = f"ord:{ordinal}" if isinstance(ordinal, int) else None
                pending.append(
                    (line_number, str(payload.get("role") or "unknown"), "\n".join(texts), locator)
                )
        # Messages are built after the scan so every pointer carries the final session id
        # and cwd even when session_meta is not the first record.
        for line_number, role, text, locator in pending:
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
                    locator=locator,
                )
            )
        if messages:
            updated_at = messages[-1].timestamp
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
            home_id=home.home_id if home else None,
            agent=home.agent if home else None,
            content_digest="sha256:" + content.hexdigest(),
            record_count=record_count,
        )


def discover_codex_homes(
    main_root: Path, *, openclaw_state_dir: Path | None = None
) -> list[SourceHome]:
    """Main Codex home plus every ``<state>/agents/<agent>/agent/codex-home/sessions``."""
    homes = [SourceHome(home_id=MAIN_HOME_ID, tool="codex", agent="codex", root=main_root)]
    if openclaw_state_dir is None:
        return homes
    agents_dir = openclaw_state_dir / "agents"
    if not agents_dir.is_dir():
        return homes
    for agent_dir in sorted(agents_dir.iterdir()):
        sessions = agent_dir / "agent" / "codex-home" / "sessions"
        if sessions.is_dir():
            name = agent_dir.name
            homes.append(
                SourceHome(
                    home_id=f"codex@openclaw/{name}",
                    tool="codex",
                    agent=f"openclaw/{name}",
                    root=sessions,
                )
            )
    return homes
