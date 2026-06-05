import json
from pathlib import Path

from session2memory.solidify import solidify_distill


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_solidify_fixture(distill_dir: Path) -> None:
    write_jsonl(
        distill_dir / "evidence_index.jsonl",
        [
            {
                "evidence_id": "e1",
                "source_path": "/tmp/raw/session.jsonl",
                "source_type": "session_message",
                "timestamp": "2026-05-22T09:00:00+00:00",
                "linked_session_id": "s1",
                "confidence": 1.0,
                "source_available": False,
                "source_unavailable_reason": "source_path_not_found",
            }
        ],
    )
    write_jsonl(
        distill_dir / "merged_candidates.jsonl",
        [
            {
                "candidate_id": "dc-memory",
                "candidate_type": "memory_candidate",
                "claim": "Keep raw source paths in evidence files only.",
                "evidence_ids": ["e1"],
                "reuse_scope": "workspace",
                "risk_level": "medium",
                "status": "proposed",
                "merged_from": [],
                "blocked_by": [],
                "validation_outcome": "pass",
                "validation_score": 90,
                "hard_gate_failures": [],
            },
            {
                "candidate_id": "dc-rule",
                "candidate_type": "rule_candidate",
                "claim": "Do not overwrite AGENTS.md from solidify.",
                "evidence_ids": ["e1"],
                "reuse_scope": "repo",
                "risk_level": "high",
                "status": "proposed",
                "merged_from": [],
                "blocked_by": [],
                "validation_outcome": "needs_review",
                "validation_score": 60,
                "hard_gate_failures": [],
            },
            {
                "candidate_id": "dc-validator",
                "candidate_type": "validator_candidate",
                "claim": "Block assistant-only real completion claims.",
                "evidence_ids": ["e1"],
                "reuse_scope": "workspace",
                "risk_level": "low",
                "status": "blocked",
                "merged_from": [],
                "blocked_by": ["e1"],
                "validation_outcome": "blocked",
                "validation_score": 0,
                "hard_gate_failures": ["assistant_only_claim"],
            },
        ],
    )
    write_jsonl(
        distill_dir / "validation.jsonl",
        [
            {
                "candidate_id": "dc-memory",
                "validation_outcome": "pass",
                "validation_score": 90,
                "hard_gate_failures": [],
                "blocked_by": [],
                "merged_from": [],
            },
            {
                "candidate_id": "dc-rule",
                "validation_outcome": "needs_review",
                "validation_score": 60,
                "hard_gate_failures": [],
                "blocked_by": [],
                "merged_from": [],
            },
            {
                "candidate_id": "dc-validator",
                "validation_outcome": "blocked",
                "validation_score": 0,
                "hard_gate_failures": ["assistant_only_claim"],
                "blocked_by": ["e1"],
                "merged_from": [],
            },
        ],
    )


def test_solidify_emits_reviewable_jsonl_and_markdown_by_candidate_type(tmp_path: Path) -> None:
    distill_dir = tmp_path / "session-memory" / "distill" / "2026-05-22"
    write_solidify_fixture(distill_dir)

    result = solidify_distill(distill_dir)

    solidified_dir = distill_dir / "solidified"
    rows = read_jsonl(solidified_dir / "solidified.jsonl")
    memory_md = (solidified_dir / "memory_candidates.md").read_text(encoding="utf-8")
    rule_md = (solidified_dir / "rule_candidates.md").read_text(encoding="utf-8")
    validator_md = (solidified_dir / "validator_candidates.md").read_text(encoding="utf-8")
    manifest = json.loads((solidified_dir / "manifest.json").read_text(encoding="utf-8"))

    assert result.solidified == 3
    assert [row["status"] for row in rows] == [
        "ready_for_review",
        "needs_human_review",
        "blocked",
    ]
    assert rows[0]["suggested_review_action"] == "create_pending_review_row"
    assert "Keep raw source paths in evidence files only." in memory_md
    assert "Do not overwrite AGENTS.md from solidify." in rule_md
    assert "assistant_only_claim" in validator_md
    assert "/tmp/raw/session.jsonl" not in memory_md
    assert manifest["counts"]["solidified"] == 3


def test_solidify_never_writes_review_or_memories(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    distill_dir = output / "distill" / "2026-05-22"
    write_solidify_fixture(distill_dir)

    solidify_distill(distill_dir)

    assert not (output / "review").exists()
    assert not (output / "memories").exists()


def test_solidify_preserves_blocked_rows_with_block_reasons(tmp_path: Path) -> None:
    distill_dir = tmp_path / "session-memory" / "distill" / "2026-05-22"
    write_solidify_fixture(distill_dir)

    solidify_distill(distill_dir)

    rows = read_jsonl(distill_dir / "solidified" / "solidified.jsonl")
    blocked = [row for row in rows if row["status"] == "blocked"]
    assert blocked == [
        {
            "solidified_id": blocked[0]["solidified_id"],
            "candidate_id": "dc-validator",
            "candidate_type": "validator_candidate",
            "claim": "Block assistant-only real completion claims.",
            "reuse_scope": "workspace",
            "risk_level": "low",
            "status": "blocked",
            "validation_outcome": "blocked",
            "validation_score": 0,
            "evidence_ids": ["e1"],
            "merged_from": [],
            "blocked_by": ["e1"],
            "hard_gate_failures": ["assistant_only_claim"],
            "suggested_review_action": "none",
        }
    ]
