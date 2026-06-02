import json
from pathlib import Path

from session2memory.review_web import ReviewWebConfig, handle_review_action, render_review_page


def _write_review_fixture(output: Path) -> None:
    (output / "review").mkdir(parents=True)
    (output / "evidence").mkdir(parents=True)
    (output / "review" / "2026-05-22.jsonl").write_text(
        json.dumps(
            {
                "id": "r000001",
                "status": "pending",
                "kind": "decision",
                "text": "Use evidence-backed memory compiler.",
                "workspace_id": "repo-123",
                "evidence_id": "e000001",
                "source": {
                    "tool": "codex",
                    "session_id": "s1",
                    "message_start": 2,
                    "message_end": 2,
                },
                "durable_suggestion": True,
                "review_note": "",
                "extraction": "marker",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "evidence" / "index.jsonl").write_text(
        json.dumps(
            {
                "id": "e000001",
                "evidence_id": "e000001",
                "tool": "codex",
                "session_id": "s1",
                "source_path": "/tmp/missing.jsonl",
                "message_start": 2,
                "message_end": 2,
                "digest": "sha256:abc",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_render_review_page_lists_rows_and_actions(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    _write_review_fixture(output)

    html = render_review_page(ReviewWebConfig(output_dir=output, date="2026-05-22"))

    assert "Use evidence-backed memory compiler." in html
    assert 'name="action" value="approve"' in html
    assert 'name="review_id" value="r000001"' in html


def test_handle_review_action_approves_row(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    _write_review_fixture(output)

    result = handle_review_action(
        ReviewWebConfig(output_dir=output, date="2026-05-22"),
        {"action": ["approve"], "review_id": ["r000001"], "note": ["web reviewed"]},
    )

    assert result.status_code == 303
    row = json.loads((output / "review" / "2026-05-22.jsonl").read_text(encoding="utf-8"))
    assert row["status"] == "approved"
    assert row["review_note"] == "web reviewed"
