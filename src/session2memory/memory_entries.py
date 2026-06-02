from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_MEMORY_ENTRY_RE = re.compile(
    r"^- \[(?P<kind>[^\]]+)\] (?P<text>.+?) \{(?P<meta>[^}]+)\}\s*$"
)
_META_PAIR_RE = re.compile(r"(\w+)=([^\s}]+)")


@dataclass(frozen=True)
class MemoryEntry:
    kind: str
    text: str
    evidence_id: str
    review_ref: str
    tool: str
    session_id: str
    memory_id: str = ""
    supersedes: str = ""


def parse_memory_markdown(content: str) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    for line in content.splitlines():
        match = _MEMORY_ENTRY_RE.match(line.strip())
        if not match:
            continue
        meta = dict(_META_PAIR_RE.findall(match.group("meta")))
        entries.append(
            MemoryEntry(
                kind=match.group("kind"),
                text=match.group("text").strip(),
                evidence_id=meta.get("evidence_id", ""),
                review_ref=meta.get("review", ""),
                tool=meta.get("tool", "unknown"),
                session_id=meta.get("session_id", "unknown"),
                memory_id=meta.get("memory_id", ""),
                supersedes=meta.get("supersedes", ""),
            )
        )
    return entries


def load_workspace_memory_entries(*, output_dir: Path, workspace_id: str) -> list[MemoryEntry]:
    memory_path = output_dir / "memories" / f"{workspace_id}.md"
    if not memory_path.is_file():
        return []
    return parse_memory_markdown(memory_path.read_text(encoding="utf-8"))
