from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

AgentMetadata = dict[str, str]

DEFAULT_AGENT_METADATA_FILE: Final = Path("~/.local/share/session2memory/active-agent.json")
DEFAULT_AGENT_METADATA_LOG_FILE: Final = Path(
    "~/.local/share/session2memory/agent-events.jsonl"
)
AGENT_METADATA_KEYS: Final = (
    "agent_runtime",
    "agent_id",
    "provider",
    "model",
    "entrypoint",
    "profile",
    "source_app",
)


@dataclass(frozen=True)
class AgentMetadataEvent:
    timestamp: datetime
    metadata: AgentMetadata


@dataclass(frozen=True)
class AgentMetadataResolver:
    events: tuple[AgentMetadataEvent, ...]
    fallback: AgentMetadata | None = None

    def metadata_for(self, timestamp: datetime | None) -> AgentMetadata | None:
        if self.events:
            if timestamp is None:
                return None
            matched: AgentMetadata | None = None
            candidate_seconds = _timestamp_seconds(timestamp)
            for event in self.events:
                if _timestamp_seconds(event.timestamp) <= candidate_seconds:
                    matched = event.metadata
                else:
                    break
            return dict(matched) if matched else None
        return dict(self.fallback) if self.fallback else None


def load_agent_metadata(path: Path) -> tuple[AgentMetadata | None, str | None]:
    marker_path = path.expanduser()
    if not marker_path.exists():
        return None, None
    try:
        loaded = json.loads(marker_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"invalid agent metadata JSON: {marker_path.as_posix()}: {exc.msg}"
    except (OSError, UnicodeError) as exc:
        return None, f"unreadable agent metadata file: {marker_path.as_posix()}: {exc}"
    if not isinstance(loaded, dict):
        return None, f"invalid agent metadata JSON: {marker_path.as_posix()}: expected object"
    return sanitize_agent_metadata(loaded), None


def load_agent_metadata_resolver(
    *,
    marker_path: Path,
    log_path: Path,
) -> tuple[AgentMetadataResolver | None, list[str]]:
    events, warnings = load_agent_metadata_events(log_path)
    marker, marker_warning = load_agent_metadata(marker_path)
    if marker_warning:
        warnings.append(marker_warning)
    if not events and not marker:
        return None, warnings
    return AgentMetadataResolver(events=tuple(events), fallback=marker), warnings


def load_agent_metadata_events(path: Path) -> tuple[list[AgentMetadataEvent], list[str]]:
    log_path = path.expanduser()
    if not log_path.exists():
        return [], []
    events: list[AgentMetadataEvent] = []
    warnings: list[str] = []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [], [f"unreadable agent metadata log: {log_path.as_posix()}: {exc}"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(
                f"invalid agent metadata log JSON: {log_path.as_posix()}:{line_number}: {exc.msg}"
            )
            continue
        if not isinstance(loaded, dict):
            warnings.append(
                "invalid agent metadata log JSON: "
                f"{log_path.as_posix()}:{line_number}: expected object"
            )
            continue
        timestamp = _parse_timestamp(loaded.get("timestamp"))
        metadata = sanitize_agent_metadata(loaded)
        if timestamp is None or metadata is None:
            warnings.append(
                f"invalid agent metadata log row: {log_path.as_posix()}:{line_number}"
            )
            continue
        events.append(AgentMetadataEvent(timestamp=timestamp, metadata=metadata))
    return sorted(events, key=lambda event: _timestamp_seconds(event.timestamp)), warnings


def sanitize_agent_metadata(raw: Mapping[str, object]) -> AgentMetadata | None:
    metadata = {
        key: value
        for key in AGENT_METADATA_KEYS
        if isinstance((value := raw.get(key)), str) and value
    }
    return metadata or None


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _timestamp_seconds(value: datetime) -> float:
    return value.timestamp()
