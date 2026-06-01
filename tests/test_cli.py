import json
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
    assert (output_dir / "review" / "2026-05-22.jsonl").exists()
    assert not list((output_dir / "memories").glob("*.md"))


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


def test_import_accepts_cursor_tool_with_missing_root(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-cursor"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "cursor",
            "--source-root",
            f"cursor={missing_root}",
            "--output",
            str(tmp_path / "memory"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=0 written=0 candidates=0")


def test_import_writes_claude_desktop_output_with_separate_tool_label(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "Claude"
    transcript_path = (
        source_root
        / "local-agent-mode-sessions"
        / "account-1"
        / "project-1"
        / "local_session"
        / ".claude"
        / "projects"
        / "-tmp-repo"
        / "desktop-cli-session.jsonl"
    )
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sessionId": "desktop-cli-session",
                        "timestamp": "2026-05-22T01:00:00Z",
                        "type": "user",
                        "cwd": "/tmp/repo",
                        "message": {
                            "role": "user",
                            "content": "Decision: Desktop sessions stay separate from Claude Code.",
                        },
                    }
                ),
                json.dumps(
                    {
                        "sessionId": "desktop-cli-session",
                        "timestamp": "2026-05-22T01:01:00Z",
                        "type": "assistant",
                        "cwd": "/tmp/repo",
                        "message": {
                            "role": "assistant",
                            "content": "Done: Desktop evidence uses its own tool label.",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    subagent_path = transcript_path.parent / "subagents" / "agent.jsonl"
    subagent_path.parent.mkdir()
    subagent_path.write_text(transcript_path.read_text(encoding="utf-8"), encoding="utf-8")
    audit_path = (
        source_root
        / "local-agent-mode-sessions"
        / "account-1"
        / "project-1"
        / "local_session"
        / "audit.jsonl"
    )
    audit_path.write_text(transcript_path.read_text(encoding="utf-8"), encoding="utf-8")
    output_dir = tmp_path / "memory"

    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "claude-desktop",
            "--source-root",
            f"claude-desktop={source_root}",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=1 written=1 candidates=2")
    daily = (output_dir / "daily" / "2026-05-22.md").read_text(encoding="utf-8")
    assert "tool=claude-desktop" in daily
    evidence = (output_dir / "evidence" / "index.jsonl").read_text(encoding="utf-8")
    assert '"tool": "claude-desktop"' in evidence
    assert "subagents/agent.jsonl" not in evidence
    assert "audit.jsonl" not in evidence


def test_import_reads_claude_desktop_metadata_sessions(tmp_path: Path) -> None:
    source_root = tmp_path / "Claude"
    metadata_path = (
        source_root
        / "claude-code-sessions"
        / "account-1"
        / "project-1"
        / "local_metadata-session.json"
    )
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps(
            {
                "sessionId": "local_metadata-session",
                "createdAt": 1775284519699,
                "lastActivityAt": 1779498000000,
                "cwd": "/tmp/repo",
                "title": "HKS session memory ranking and synthesis",
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "memory"

    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-23",
            "--tool",
            "claude-desktop",
            "--source-root",
            f"claude-desktop={source_root}",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-23 tools=1 sessions=1 written=1 candidates=1")
    daily = (output_dir / "daily" / "2026-05-23.md").read_text(encoding="utf-8")
    assert "HKS session memory ranking and synthesis" in daily
    evidence = (output_dir / "evidence" / "index.jsonl").read_text(encoding="utf-8")
    assert '"tool": "claude-desktop"' in evidence
    assert "local_metadata-session.json" in evidence


def test_import_rejects_unsupported_tool(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "unknown-vibe",
            "--output",
            str(tmp_path / "memory"),
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported tool: unknown-vibe" in result.output


def test_discover_reports_source_roots(tmp_path: Path) -> None:
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()
    result = CliRunner().invoke(
        app,
        [
            "discover",
            "--source-root",
            f"cursor={cursor_root}",
            "--source-root",
            f"cursor-cli={tmp_path / 'missing-cursor-cli'}",
        ],
    )

    assert result.exit_code == 0
    assert f"cursor source=found root={cursor_root}" in result.output
    assert "cursor-cli source=missing" in result.output


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


def test_import_writes_missing_source_root_reason_to_manifest(tmp_path: Path) -> None:
    output_dir = tmp_path / "memory"
    missing_root = tmp_path / "missing-codex"
    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "codex",
            "--source-root",
            f"codex={missing_root}",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert_summary(result.output, "date=2026-05-22 tools=1 sessions=0 written=1 candidates=0")
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert any(missing_root.as_posix() in reason for reason in manifest["skipped"])
    assert any("missing source root" in reason for reason in manifest["skipped"])
