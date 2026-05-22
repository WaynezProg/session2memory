from pathlib import Path

from typer.testing import CliRunner

from session2memory.cli import app


def assert_summary(output: str, expected: str) -> None:
    assert output == expected + "\n"


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
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=0 written=0 candidates=0")
    assert not output_dir.exists()


def test_import_writes_memory_output_from_codex_fixture(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "codex",
            "--source-root",
            "codex=tests/fixtures/codex",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=1 written=1 candidates=0")
    assert (output_dir / "daily" / "2026-05-22.md").exists()
    assert (output_dir / "evidence" / "index.jsonl").exists()


def test_dry_run_scans_codex_fixture_without_writing_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "codex",
            "--source-root",
            "codex=tests/fixtures/codex",
            "--output",
            str(output_dir),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=1 written=0 candidates=0")
    assert not output_dir.exists()


def test_import_rejects_unsupported_tool(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "cursor",
            "--output",
            str(tmp_path / "memory"),
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported tool: cursor" in result.output


def test_import_rejects_bad_date_with_cli_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "bad",
            "--tool",
            "codex",
            "--source-root",
            "codex=tests/fixtures/codex",
            "--output",
            str(tmp_path / "memory"),
        ],
    )

    assert result.exit_code != 0
    assert "Invalid value" in result.output
    assert "YYYY-MM-DD" in result.output
    assert "Traceback" not in result.output


def test_import_dedupes_duplicate_tool_selection(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "codex",
            "--tool",
            "codex",
            "--source-root",
            "codex=tests/fixtures/codex",
            "--output",
            str(output_dir),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=1 written=0 candidates=0")


def test_source_root_without_tool_selects_override_tools_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--source-root",
            "codex=tests/fixtures/codex",
            "--output",
            str(output_dir),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=1 written=0 candidates=0")


def test_import_workspace_mismatch_filters_codex_fixture(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "codex",
            "--workspace",
            str(tmp_path / "other-workspace"),
            "--source-root",
            "codex=tests/fixtures/codex",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=0 written=1 candidates=0")
