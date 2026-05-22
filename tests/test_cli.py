from pathlib import Path

from typer.testing import CliRunner

from session2memory.cli import app


def test_import_requires_date_and_output() -> None:
    result = CliRunner().invoke(app, ["import"])

    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_dry_run_with_empty_source_roots_reports_zero_sessions(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--output",
            str(output_dir),
            "--source-root",
            "codex=missing",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "sessions=0" in result.output
    assert "written=0" in result.output
    assert not output_dir.exists()
