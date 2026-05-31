import json
from pathlib import Path

from session2memory.review_bulk import BulkFilter, bulk_update_reviews


def _write_review(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_bulk_approve_pending_only(tmp_path: Path) -> None:
    review_path = tmp_path / "review" / "2026-05-22.jsonl"
    _write_review(
        review_path,
        [
            {
                "id": "r1",
                "status": "pending",
                "kind": "decision",
                "text": "A",
                "workspace_id": "w",
                "evidence_id": "e1",
                "durable_suggestion": True,
            },
            {
                "id": "r2",
                "status": "promoted",
                "kind": "decision",
                "text": "B",
                "workspace_id": "w",
                "evidence_id": "e2",
                "durable_suggestion": True,
            },
        ],
    )
    result = bulk_update_reviews(
        output_dir=tmp_path,
        date="2026-05-22",
        target_status="approved",
        filters=BulkFilter(status="pending"),
        durable=None,
        note=None,
        dry_run=False,
    )
    assert result.matched == 1
    assert result.updated == 1
    rows = [json.loads(line) for line in review_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["status"] == "approved"
    assert rows[1]["status"] == "promoted"
