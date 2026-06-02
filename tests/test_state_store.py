from pathlib import Path

from session2memory.models import EvidencePointer, MemoryCandidate
from session2memory.state.ids import evidence_id_for
from session2memory.state.store import StateStore


def test_state_store_upsert_source_file(tmp_path: Path) -> None:
    store = StateStore.open(tmp_path / "session2memory.db")
    changed = store.upsert_source_file(
        tool="codex",
        path="/tmp/s.jsonl",
        digest="sha256:abc",
        mtime_ns=1,
    )
    assert changed is True
    row = store.get_source_file(tool="codex", path="/tmp/s.jsonl")
    assert row is not None
    assert row["digest"] == "sha256:abc"
    unchanged = store.upsert_source_file(
        tool="codex",
        path="/tmp/s.jsonl",
        digest="sha256:abc",
        mtime_ns=1,
    )
    assert unchanged is False
    store.close()


def test_evidence_id_stable() -> None:
    first = evidence_id_for(tool="codex", session_id="s", start=1, end=2, digest="sha256:x")
    second = evidence_id_for(tool="codex", session_id="s", start=1, end=2, digest="sha256:x")
    assert first == second
    assert first.startswith("e_")


def test_upsert_candidate_keeps_stable_ids(tmp_path: Path) -> None:
    store = StateStore.open(tmp_path / "session2memory.db")
    candidate = _candidate()
    first = store.upsert_candidate(import_date="2026-05-22", candidate=candidate)
    second = store.upsert_candidate(import_date="2026-05-22", candidate=candidate)
    assert first.evidence_id == second.evidence_id
    assert first.candidate_id == second.candidate_id
    store.close()


def _candidate() -> MemoryCandidate:
    evidence = EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/s.jsonl"),
        message_start=2,
        message_end=2,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:abc",
    )
    return MemoryCandidate(
        kind="decision",
        text="Use evidence-backed memory compiler.",
        workspace_id="repo-123",
        evidence=evidence,
        durable=True,
    )
