from pathlib import Path

import pytest

from session2memory.review_ui import ReviewAppConfig, build_review_app

pytest.importorskip("textual")


def test_build_review_app_has_list_screen(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    (output / "review").mkdir(parents=True)
    (output / "review" / "2026-05-22.jsonl").write_text(
        '{"id": "r000001", "status": "pending", "kind": "decision", '
        '"text": "Use uv", "workspace_id": "repo-123", "evidence_id": "e000001", '
        '"source": {"tool": "codex", "session_id": "s1", "message_start": 1, '
        '"message_end": 1}, "durable_suggestion": true, "review_note": "", '
        '"extraction": "marker"}\n',
        encoding="utf-8",
    )
    app = build_review_app(ReviewAppConfig(output_dir=output, date="2026-05-22"))
    assert app is not None
