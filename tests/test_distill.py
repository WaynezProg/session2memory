import json
from pathlib import Path

import pytest

from session2memory.distill import DistillError, distill_reviews


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_review_fixture(output: Path) -> None:
    source_path = output / "raw-session.jsonl"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text('{"role":"user","content":"keep this"}\n', encoding="utf-8")
    write_jsonl(
        output / "evidence" / "index.jsonl",
        [
            {
                "id": "e-approved-a",
                "evidence_id": "e-approved-a",
                "kind": "decision",
                "workspace_id": "repo-123",
                "source_path": source_path.as_posix(),
                "source_type": "session_message",
                "timestamp": "2026-05-22T09:00:00+00:00",
                "message_start": 2,
                "message_end": 2,
                "tool": "codex",
                "session_id": "s-approved-a",
                "workspace_path": "/tmp/repo",
                "digest": "sha256:aaa",
                "durable": True,
                "actor_roles": ["user", "assistant"],
                "evidence_mode": "real",
            },
            {
                "id": "e-approved-b",
                "evidence_id": "e-approved-b",
                "kind": "verification",
                "workspace_id": "repo-123",
                "source_path": "/tmp/missing-session.jsonl",
                "source_type": "test_result",
                "timestamp": "2026-05-22T10:00:00+00:00",
                "message_start": 4,
                "message_end": 5,
                "tool": "codex",
                "session_id": "s-approved-b",
                "workspace_path": "/tmp/repo",
                "digest": "sha256:bbb",
                "durable": False,
                "actor_roles": ["tool"],
                "evidence_mode": "real",
            },
            {
                "id": "e-pending",
                "evidence_id": "e-pending",
                "source_path": source_path.as_posix(),
                "source_type": "session_message",
                "timestamp": "2026-05-22T11:00:00+00:00",
                "tool": "codex",
                "session_id": "s-pending",
            },
        ],
    )
    write_jsonl(
        output / "review" / "2026-05-22.jsonl",
        [
            {
                "id": "r-approved-a",
                "status": "approved",
                "kind": "decision",
                "text": "Promote only approved durable review rows.",
                "workspace_id": "repo-123",
                "evidence_id": "e-approved-a",
                "durable_suggestion": True,
                "review_note": "keep",
                "extraction": "marker",
                "source": {"tool": "codex", "session_id": "s-approved-a"},
            },
            {
                "id": "r-approved-b",
                "status": "approved",
                "kind": "verification",
                "text": "Run pytest before claiming the distill pipeline is done.",
                "workspace_id": "repo-123",
                "evidence_id": "e-approved-b",
                "durable_suggestion": False,
                "review_note": "daily-only but useful as validator",
                "extraction": "marker",
                "source": {"tool": "codex", "session_id": "s-approved-b"},
            },
            {
                "id": "r-pending",
                "status": "pending",
                "kind": "decision",
                "text": "Pending rows stay out.",
                "workspace_id": "repo-123",
                "evidence_id": "e-pending",
                "durable_suggestion": True,
                "review_note": "",
                "extraction": "marker",
            },
            {
                "id": "r-rejected",
                "status": "rejected",
                "kind": "decision",
                "text": "Rejected rows stay out.",
                "workspace_id": "repo-123",
                "evidence_id": "e-pending",
                "durable_suggestion": True,
                "review_note": "",
                "extraction": "marker",
            },
            {
                "id": "r-promoted",
                "status": "promoted",
                "kind": "decision",
                "text": "Already promoted rows stay out.",
                "workspace_id": "repo-123",
                "evidence_id": "e-pending",
                "durable_suggestion": True,
                "review_note": "",
                "extraction": "marker",
            },
        ],
    )
    (output / "manifest.json").write_text(
        json.dumps({"generator": "session2memory"}, sort_keys=True),
        encoding="utf-8",
    )


def test_distill_reads_only_approved_review_rows(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_review_fixture(output)

    result = distill_reviews(output_dir=output, date="2026-05-22")

    candidates = read_jsonl(output / "distill" / "2026-05-22" / "candidates.jsonl")
    evidence = read_jsonl(output / "distill" / "2026-05-22" / "evidence_index.jsonl")
    manifest = json.loads(
        (output / "distill" / "2026-05-22" / "manifest.json").read_text(encoding="utf-8")
    )

    assert result.review_rows == 5
    assert result.approved_reviews == 2
    assert result.candidates == 2
    assert [row["source_review_ids"] for row in candidates] == [
        ["r-approved-a"],
        ["r-approved-b"],
    ]
    assert [row["candidate_type"] for row in candidates] == [
        "memory_candidate",
        "validator_candidate",
    ]
    assert candidates[1]["risk_level"] == "low"
    assert candidates[1]["claim_mode"] == "plan_or_validation"
    assert evidence[0]["source_path"].endswith("raw-session.jsonl")
    assert evidence[0]["source_type"] == "session_message"
    assert evidence[0]["timestamp"] == "2026-05-22T09:00:00+00:00"
    assert evidence[0]["linked_session_id"] == "s-approved-a"
    assert evidence[0]["confidence"] == 1.0
    assert evidence[1]["source_available"] is False
    assert evidence[1]["source_unavailable_reason"] == "source_path_not_found"
    assert manifest["counts"] == {
        "review_rows": 5,
        "approved_reviews": 2,
        "evidence_records": 2,
        "candidates": 2,
    }
    assert not (output / "memories").exists()


def test_distill_missing_review_file_writes_empty_manifest(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"

    result = distill_reviews(output_dir=output, date="2026-05-22")

    distill_dir = output / "distill" / "2026-05-22"
    assert result.review_rows == 0
    assert result.approved_reviews == 0
    assert read_jsonl(distill_dir / "candidates.jsonl") == []
    manifest = json.loads((distill_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["candidates"] == 0


def test_distill_invalid_review_jsonl_exits_without_partial_output(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    review_path = output / "review" / "2026-05-22.jsonl"
    review_path.parent.mkdir(parents=True)
    review_path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(DistillError):
        distill_reviews(output_dir=output, date="2026-05-22")

    assert not (output / "distill" / "2026-05-22" / "candidates.jsonl").exists()
