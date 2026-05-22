from __future__ import annotations

import re

from session2memory.filtering import is_noise
from session2memory.models import MemoryCandidate, MemoryKind, SessionRecord, WorkspaceIdentity

RULES: tuple[tuple[MemoryKind, str, bool], ...] = (
    ("decision", r"^\s*(?:決定|Decision)[:：]\s*(.+)", True),
    ("completed", r"^\s*(?:完成|Done)[:：]\s*(.+)", True),
    ("pitfall", r"^\s*(?:坑|Pitfall)[:：]\s*(.+)", True),
    ("constraint", r"^\s*(?:限制|Constraint)[:：]\s*(.+)", True),
    ("verification", r"^\s*(?:驗證|Verification)[:：]\s*(.+)", True),
)

NEGATION_MARKERS = (
    "不是決定",
    "不要記",
    "先別記",
)


def extract_candidates(
    record: SessionRecord, workspace: WorkspaceIdentity
) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for message in record.messages:
        if is_noise(message):
            continue
        if _contains_negation(message.text):
            continue
        for kind, pattern, durable in RULES:
            match = re.search(pattern, message.text, flags=re.MULTILINE)
            if not match:
                continue
            text = match.group(1).strip()
            if not text:
                continue
            candidates.append(
                MemoryCandidate(
                    kind=kind,
                    text=text,
                    workspace_id=workspace.workspace_id,
                    evidence=message.raw_pointer,
                    durable=durable,
                )
            )
            break
    return candidates


def _contains_negation(text: str) -> bool:
    return any(marker in text for marker in NEGATION_MARKERS)
