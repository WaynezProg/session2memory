from pathlib import Path

from session2memory.models import EvidencePointer, MemoryCandidate, SessionMessage, digest_text


def test_digest_text_is_stable_and_prefixed() -> None:
    assert (
        digest_text("accepted decision\n")
        == "sha256:0488779cd2495114702c2aba1e615dae014ccc33618061b2b876eef3941c8e57"
    )


def test_evidence_pointer_serializes_paths_as_strings() -> None:
    pointer = EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/session.jsonl"),
        message_start=2,
        message_end=4,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:abc",
    )

    assert pointer.to_json() == {
        "tool": "codex",
        "session_id": "s1",
        "source_path": "/tmp/session.jsonl",
        "message_start": 2,
        "message_end": 4,
        "workspace_path": "/tmp/repo",
        "digest": "sha256:abc",
    }


def test_session_message_text_is_stripped() -> None:
    pointer = EvidencePointer(
        tool="qwen",
        session_id="s2",
        source_path=Path("/tmp/qwen.jsonl"),
        message_start=1,
        message_end=1,
        workspace_path=None,
        digest="sha256:def",
    )

    message = SessionMessage(
        index=1,
        role="user",
        text="  hello  ",
        timestamp=None,
        raw_pointer=pointer,
    )

    assert message.text == "hello"


def test_memory_candidate_accepts_llm_metadata() -> None:
    evidence = EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/s.jsonl"),
        message_start=1,
        message_end=1,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:abc",
    )
    candidate = MemoryCandidate(
        kind="decision",
        text="Prefer uv over pip",
        workspace_id="repo-123",
        evidence=evidence,
        durable=False,
        extraction="llm",
        confidence=0.82,
        evidence_quote="we should use uv",
    )
    assert candidate.extraction == "llm"
    assert candidate.confidence == 0.82
