import sqlite3
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


def test_two_candidates_share_evidence(tmp_path: Path) -> None:
    store = StateStore.open(tmp_path / "session2memory.db")
    evidence = _evidence()
    decision = MemoryCandidate(
        kind="decision",
        text="Use evidence-backed memory compiler.",
        workspace_id="repo-123",
        evidence=evidence,
        durable=True,
    )
    completed = MemoryCandidate(
        kind="completed",
        text="Shipped the compiler.",
        workspace_id="repo-123",
        evidence=evidence,
        durable=False,
        extraction="llm",
        confidence=0.9,
    )
    first = store.upsert_candidate(import_date="2026-05-22", candidate=decision)
    second = store.upsert_candidate(import_date="2026-05-22", candidate=completed)
    assert first.evidence_id == second.evidence_id
    assert first.candidate_id != second.candidate_id
    assert len(store.list_candidates_for_date("2026-05-22")) == 2
    store.close()


def test_open_migrates_legacy_unique_evidence_index(tmp_path: Path) -> None:
    db_path = tmp_path / "session2memory.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE candidates (
            candidate_id TEXT PRIMARY KEY,
            import_date TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            text_normalized TEXT NOT NULL,
            extraction TEXT NOT NULL DEFAULT 'marker',
            confidence REAL,
            evidence_quote TEXT,
            durable INTEGER NOT NULL,
            evidence_id TEXT NOT NULL UNIQUE,
            review_id TEXT NOT NULL UNIQUE,
            review_status TEXT NOT NULL DEFAULT 'pending',
            review_note TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL,
            session_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            message_start INTEGER NOT NULL,
            message_end INTEGER NOT NULL,
            message_digest TEXT NOT NULL,
            workspace_path TEXT
        );
        """
    )
    connection.commit()
    connection.close()

    store = StateStore.open(db_path)
    evidence = _evidence()
    store.upsert_candidate(
        import_date="2026-05-22",
        candidate=MemoryCandidate(
            kind="decision",
            text="Use evidence-backed memory compiler.",
            workspace_id="repo-123",
            evidence=evidence,
            durable=True,
        ),
    )
    store.upsert_candidate(
        import_date="2026-05-22",
        candidate=MemoryCandidate(
            kind="completed",
            text="Shipped the compiler.",
            workspace_id="repo-123",
            evidence=evidence,
            durable=False,
        ),
    )
    assert len(store.list_candidates_for_date("2026-05-22")) == 2
    store.close()


def _evidence() -> EvidencePointer:
    return EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/s.jsonl"),
        message_start=2,
        message_end=2,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:abc",
    )


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
