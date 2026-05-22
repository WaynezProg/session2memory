from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from session2memory.models import EvidencePointer, Role, SessionMessage, SessionRecord, digest_text


class SessionAdapter(Protocol):
    tool: str

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        raise NotImplementedError


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, object]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if stripped:
                try:
                    loaded = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{path.as_posix()}:{line_number}: malformed JSON: {exc.msg}"
                    ) from exc
                if not isinstance(loaded, dict):
                    raise ValueError(
                        f"{path.as_posix()}:{line_number}: expected JSON object"
                    )
                yield line_number, cast("dict[str, object]", loaded)


def skipped_file_reason(tool: str, path: Path, exc: Exception) -> str:
    return f"{tool}: skipped {path.as_posix()}: {exc}"


def normalize_role(role: str) -> Role:
    if role in {"user", "assistant", "tool", "system"}:
        return cast("Role", role)
    return "unknown"


def make_message(
    *,
    tool: str,
    session_id: str,
    source_path: Path,
    line_number: int,
    role: str,
    text: str,
    timestamp: datetime | None,
    cwd: Path | None,
) -> SessionMessage:
    pointer = EvidencePointer(
        tool=tool,
        session_id=session_id,
        source_path=source_path,
        message_start=line_number,
        message_end=line_number,
        workspace_path=cwd,
        digest=digest_text(text),
    )
    return SessionMessage(
        index=line_number,
        role=normalize_role(role),
        text=text,
        timestamp=timestamp,
        raw_pointer=pointer,
    )
