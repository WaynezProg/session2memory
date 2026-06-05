import json
from pathlib import Path

from typer.testing import CliRunner

from session2memory.cli import app

runner = CliRunner()


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_pipeline_fixture(output: Path) -> None:
    raw_path = output / "raw.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text('{"role":"user","content":"Decision: keep safe gates"}\n', encoding="utf-8")
    write_jsonl(
        output / "evidence" / "index.jsonl",
        [
            {
                "evidence_id": "e1",
                "source_path": raw_path.as_posix(),
                "source_type": "session_message",
                "timestamp": "2026-05-22T09:00:00+00:00",
                "linked_session_id": "s1",
                "confidence": 1.0,
                "tool": "codex",
                "session_id": "s1",
                "actor_roles": ["user"],
                "evidence_mode": "real",
            }
        ],
    )
    write_jsonl(
        output / "review" / "2026-05-22.jsonl",
        [
            {
                "id": "r1",
                "status": "approved",
                "kind": "decision",
                "text": "Keep promote behind approved durable gates.",
                "workspace_id": "repo-123",
                "evidence_id": "e1",
                "durable_suggestion": True,
                "review_note": "approved",
                "extraction": "marker",
                "source": {"tool": "codex", "session_id": "s1"},
            }
        ],
    )


def test_distill_validate_solidify_cli_pipeline(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_pipeline_fixture(output)

    distill_result = runner.invoke(
        app,
        ["distill", "--date", "2026-05-22", "--output", str(output)],
    )
    validate_result = runner.invoke(
        app,
        ["validate", "--distill", str(output / "distill" / "2026-05-22")],
    )
    solidify_result = runner.invoke(
        app,
        ["solidify", "--distill", str(output / "distill" / "2026-05-22")],
    )

    assert distill_result.exit_code == 0
    assert "approved_reviews=1 candidates=1" in distill_result.output
    assert validate_result.exit_code == 0
    assert "validated=1" in validate_result.output
    assert solidify_result.exit_code == 0
    assert "solidified=1" in solidify_result.output
    assert (output / "distill" / "2026-05-22" / "solidified" / "solidified.jsonl").exists()


def test_distill_cli_uses_default_output_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "out" / "session-memory"
    write_pipeline_fixture(output)

    result = runner.invoke(app, ["distill", "--date", "2026-05-22"])

    assert result.exit_code == 0
    assert (output / "distill" / "2026-05-22" / "candidates.jsonl").exists()


def test_commands_do_not_change_existing_promote_behavior(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    write_pipeline_fixture(output)
    review_path = output / "review" / "2026-05-22.jsonl"
    row = json.loads(review_path.read_text(encoding="utf-8"))
    row["durable_suggestion"] = False
    review_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    (output / "daily").mkdir(parents=True)
    (output / "daily" / "2026-05-22.md").write_text("# 2026-05-22\n", encoding="utf-8")
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "workspaces": {
                    "repo-123": {
                        "canonical_path": "/tmp/repo",
                        "repo_root": "/tmp/repo",
                        "opened_cwd": "/tmp/repo",
                        "tool_workspace_id": None,
                    }
                },
                "output_files": [
                    "daily/2026-05-22.md",
                    "evidence/index.jsonl",
                    "manifest.json",
                    "review/2026-05-22.jsonl",
                ],
                "counts": {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["promote", "--date", "2026-05-22", "--output", str(output)])

    assert result.exit_code == 0
    assert "promoted=0" in result.output
    assert not list((output / "memories").glob("*.md"))
