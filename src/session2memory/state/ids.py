from __future__ import annotations

from hashlib import sha256


def evidence_id_for(
    *,
    tool: str,
    session_id: str,
    start: int,
    end: int,
    digest: str,
) -> str:
    raw = "\0".join((tool, session_id, str(start), str(end), digest))
    return "e_" + sha256(raw.encode("utf-8")).hexdigest()[:12]


def candidate_id_for(
    *,
    workspace_id: str,
    kind: str,
    text_normalized: str,
    message_digest: str,
) -> str:
    raw = "\0".join((workspace_id, kind, text_normalized, message_digest))
    return "c_" + sha256(raw.encode("utf-8")).hexdigest()[:12]


def review_id_for(*, candidate_id: str) -> str:
    return "r_" + sha256(candidate_id.encode("utf-8")).hexdigest()[:16]


def memory_entry_id_for(*, workspace_id: str, evidence_id: str, review_ref: str) -> str:
    raw = "\0".join((workspace_id, evidence_id, review_ref))
    return "m_" + sha256(raw.encode("utf-8")).hexdigest()[:12]
