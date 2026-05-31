from session2memory.review_conflicts import find_conflicts


def test_find_conflicts_same_text_different_evidence() -> None:
    rows = [
        {
            "id": "r1",
            "workspace_id": "w",
            "kind": "decision",
            "text": "Same",
            "evidence_id": "e1",
            "durable_suggestion": True,
        },
        {
            "id": "r2",
            "workspace_id": "w",
            "kind": "decision",
            "text": "Same",
            "evidence_id": "e2",
            "durable_suggestion": True,
        },
    ]
    groups = find_conflicts(rows)
    assert len(groups) == 1
    assert set(groups[0].review_ids) == {"r1", "r2"}
