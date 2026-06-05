import json
from pathlib import Path

from session2memory.validate import validate_distill


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_distill(
    distill_dir: Path,
    *,
    evidence: list[dict[str, object]],
    candidates: list[dict[str, object]],
) -> None:
    write_jsonl(distill_dir / "evidence_index.jsonl", evidence)
    write_jsonl(distill_dir / "candidates.jsonl", candidates)


def evidence_row(
    evidence_id: str,
    *,
    source_type: str = "session_message",
    actor_roles: list[str] | None = None,
    evidence_mode: str = "real",
    timestamp: str = "2026-05-22T09:00:00+00:00",
    source_path: str | None = None,
    source_available: bool = True,
    source_unavailable_reason: str | None = None,
    contradicts_candidate_ids: list[str] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "evidence_id": evidence_id,
        "source_path": source_path or Path(__file__).as_posix(),
        "source_type": source_type,
        "timestamp": timestamp,
        "linked_session_id": f"s-{evidence_id}",
        "confidence": 1.0,
        "source_available": source_available,
        "source_unavailable_reason": source_unavailable_reason,
        "actor_roles": actor_roles or ["user"],
        "evidence_mode": evidence_mode,
    }
    if contradicts_candidate_ids is not None:
        row["contradicts_candidate_ids"] = contradicts_candidate_ids
    return row


def candidate(
    candidate_id: str,
    *,
    claim: str = "Promote only approved durable review rows.",
    evidence_ids: list[str] | None = None,
    candidate_type: str = "memory_candidate",
    reuse_scope: str = "workspace",
    risk_level: str = "medium",
    claim_mode: str = "real_completion",
    source_review_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "claim": claim,
        "evidence_ids": evidence_ids if evidence_ids is not None else ["e1"],
        "reuse_scope": reuse_scope,
        "risk_level": risk_level,
        "status": "proposed",
        "workspace_id": "repo-123",
        "claim_mode": claim_mode,
        "source_review_ids": source_review_ids or ["r1"],
    }


def test_validate_blocks_candidate_without_evidence_ids(tmp_path: Path) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[evidence_row("e1")],
        candidates=[candidate("dc1", evidence_ids=[])],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] == "blocked"
    assert "missing_evidence_ids" in validation[0]["hard_gate_failures"]


def test_validate_blocks_assistant_only_claim(tmp_path: Path) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[evidence_row("e1", source_type="assistant_summary", actor_roles=["assistant"])],
        candidates=[candidate("dc1")],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] == "blocked"
    assert "assistant_only_claim" in validation[0]["hard_gate_failures"]


def test_validate_blocks_existing_source_path_that_is_missing(tmp_path: Path) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[
            evidence_row(
                "e1",
                source_available=True,
                source_path="/tmp/session2memory-definitely-missing-source.jsonl",
            )
        ],
        candidates=[candidate("dc1")],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] != "pass"
    assert "missing_source_path" in validation[0]["hard_gate_failures"]


def test_validate_blocks_real_completion_supported_only_by_mock_or_dry_run(
    tmp_path: Path,
) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[
            evidence_row("e1", evidence_mode="mock"),
            evidence_row("e2", evidence_mode="dry_run"),
        ],
        candidates=[candidate("dc1", evidence_ids=["e1", "e2"])],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] == "blocked"
    assert "mock_or_dry_run_real_completion" in validation[0]["hard_gate_failures"]


def test_validate_merges_duplicate_candidates_into_one_canonical_row(tmp_path: Path) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[evidence_row("e1"), evidence_row("e2")],
        candidates=[
            candidate("dc1", evidence_ids=["e1"], claim="Keep raw source paths in evidence only."),
            candidate("dc2", evidence_ids=["e2"], claim="Keep raw source paths in evidence only."),
        ],
    )

    result = validate_distill(distill_dir)

    merged = read_jsonl(distill_dir / "merged_candidates.jsonl")
    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert result.merged_candidates == 1
    assert len(merged) == 1
    assert merged[0]["candidate_id"] == "dc1"
    assert merged[0]["evidence_ids"] == ["e1", "e2"]
    assert merged[0]["merged_from"] == ["dc2"]
    assert len(validation) == 1


def test_validate_blocks_newer_contradictory_evidence(tmp_path: Path) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[
            evidence_row("e1", timestamp="2026-05-22T09:00:00+00:00"),
            evidence_row(
                "e2",
                source_type="user_correction",
                timestamp="2026-05-22T10:00:00+00:00",
                contradicts_candidate_ids=["dc1"],
            ),
        ],
        candidates=[candidate("dc1", evidence_ids=["e1", "e2"])],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] == "blocked"
    assert validation[0]["blocked_by"] == ["e2"]
    assert "newer_contradictory_evidence" in validation[0]["hard_gate_failures"]


def test_validate_blocks_newer_global_user_correction_not_in_candidate_evidence(
    tmp_path: Path,
) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[
            evidence_row("e1", timestamp="2026-05-22T09:00:00+00:00"),
            evidence_row(
                "e2",
                source_type="user_correction",
                timestamp="2026-05-22T10:00:00+00:00",
                contradicts_candidate_ids=["dc1"],
            ),
        ],
        candidates=[candidate("dc1", evidence_ids=["e1"])],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] == "blocked"
    assert validation[0]["blocked_by"] == ["e2"]
    assert "newer_contradictory_evidence" in validation[0]["hard_gate_failures"]


def test_validate_uses_timezone_aware_timestamps_for_newer_contradiction(
    tmp_path: Path,
) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[
            evidence_row("e1", timestamp="2026-06-05T12:30:00+08:00"),
            evidence_row(
                "e2",
                source_type="file_snapshot",
                timestamp="2026-06-05T05:00:00Z",
                contradicts_candidate_ids=["dc1"],
            ),
        ],
        candidates=[candidate("dc1", evidence_ids=["e1"])],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] == "blocked"
    assert validation[0]["blocked_by"] == ["e2"]
    assert "newer_contradictory_evidence" in validation[0]["hard_gate_failures"]


def test_validate_blocks_invalid_timestamp_on_candidate_evidence(tmp_path: Path) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[evidence_row("e1", timestamp="not-a-timestamp")],
        candidates=[candidate("dc1", evidence_ids=["e1"])],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] == "blocked"
    assert validation[0]["blocked_by"] == ["e1"]
    assert "invalid_timestamp" in validation[0]["hard_gate_failures"]


def test_validate_user_correction_outweighs_assistant_summary(tmp_path: Path) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[
            evidence_row("e1", source_type="assistant_summary", actor_roles=["assistant"]),
            evidence_row("e2", source_type="user_correction", actor_roles=["user"]),
        ],
        candidates=[candidate("dc1", evidence_ids=["e1", "e2"])],
    )

    validate_distill(distill_dir)

    validation = read_jsonl(distill_dir / "validation.jsonl")
    assert validation[0]["validation_outcome"] == "pass"
    assert validation[0]["validation_score"] >= 80


def test_validate_scores_pass_needs_review_and_blocked_boundaries(tmp_path: Path) -> None:
    distill_dir = tmp_path / "distill" / "2026-05-22"
    write_distill(
        distill_dir,
        evidence=[evidence_row("e1"), evidence_row("e2", actor_roles=["unknown"])],
        candidates=[
            candidate("dc-pass", evidence_ids=["e1"], source_review_ids=["r1"]),
            candidate(
                "dc-review",
                claim="Needs human review with only weak evidence.",
                evidence_ids=["e2"],
                source_review_ids=[],
                risk_level="medium",
            ),
            candidate("dc-blocked", claim="Blocked because it has no evidence.", evidence_ids=[]),
        ],
    )

    validate_distill(distill_dir)

    validation = {row["candidate_id"]: row for row in read_jsonl(distill_dir / "validation.jsonl")}
    assert validation["dc-pass"]["validation_outcome"] == "pass"
    assert validation["dc-review"]["validation_outcome"] == "needs_review"
    assert validation["dc-blocked"]["validation_outcome"] == "blocked"
