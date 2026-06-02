from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

Role = Literal["user", "assistant", "tool", "system", "unknown"]
MemoryKind = Literal["decision", "completed", "pitfall", "constraint", "verification", "daily"]
ExtractionSource = Literal["marker", "llm"]


def digest_text(text: str) -> str:
    return "sha256:" + sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidencePointer:
    tool: str
    session_id: str
    source_path: Path
    message_start: int
    message_end: int
    workspace_path: Path | None
    digest: str

    def to_json(self) -> dict[str, str | int | None]:
        return {
            "tool": self.tool,
            "session_id": self.session_id,
            "source_path": self.source_path.as_posix(),
            "message_start": self.message_start,
            "message_end": self.message_end,
            "workspace_path": self.workspace_path.as_posix() if self.workspace_path else None,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class SessionMessage:
    index: int
    role: Role
    text: str
    timestamp: datetime | None
    raw_pointer: EvidencePointer

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", self.text.strip())


@dataclass(frozen=True)
class SessionRecord:
    tool: str
    session_id: str
    source_path: Path
    started_at: datetime | None
    updated_at: datetime | None
    cwd: Path | None
    repo_root: Path | None
    tool_workspace_id: str | None
    messages: list[SessionMessage]


@dataclass(frozen=True)
class WorkspaceIdentity:
    workspace_id: str
    canonical_path: Path
    repo_root: Path | None
    opened_cwd: Path | None
    tool_workspace_id: str | None


@dataclass(frozen=True)
class MemoryCandidate:
    kind: MemoryKind
    text: str
    workspace_id: str
    evidence: EvidencePointer
    durable: bool
    extraction: ExtractionSource = "marker"
    confidence: float | None = None
    evidence_quote: str | None = None
