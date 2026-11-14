import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from session2memory.cli import _default_source_roots, app


def assert_summary(output: str, expected: str) -> None:
    assert output == expected + "\n"


def active_agent_metadata(provider: str = "glm", model: str = "glm-5.1") -> dict[str, str]:
    return {
        "agent_runtime": "claude-code",
        "agent_id": f"claude-{provider}-vscode",
        "provider": provider,
        "model": model,
        "entrypoint": f"claude-{provider}-vscode",
        "source_app": "vscode-extension",
    }


def write_active_agent_marker(home: Path, payload: Mapping[str, object]) -> Path:
    marker_path = home / ".local" / "share" / "session2memory" / "active-agent.json"
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text(json.dumps(payload), encoding="utf-8")
    return marker_path


def write_agent_event_log(home: Path, rows: list[dict[str, object]]) -> Path:
    log_path = home / ".local" / "share" / "session2memory" / "agent-events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return log_path


def read_one_jsonl(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8").strip()))


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


def test_import_reads_default_active_agent_metadata(tmp_path: Path) -> None:
    home = tmp_path / "home"
    metadata = active_agent_metadata()
    write_active_agent_marker(
        home,
        {
            **metadata,
            "api_key": "secret-should-not-be-stored",
            "unknown": "drop-me",
        },
    )
    output_dir = tmp_path / "memory"

    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "claude",
            "--source-root",
            "claude=tests/fixtures/claude",
            "--output",
            str(output_dir),
        ],
        env={"HOME": str(home)},
    )

    assert result.exit_code == 0
    review_row = read_one_jsonl(output_dir / "review" / "2026-05-22.jsonl")
    evidence_row = read_one_jsonl(output_dir / "evidence" / "index.jsonl")
    assert review_row["agent_metadata"] == metadata
    assert evidence_row["agent_metadata"] == metadata
    serialized = json.dumps(review_row, sort_keys=True)
    assert "api_key" not in serialized
    assert "secret-should-not-be-stored" not in serialized
    assert "unknown" not in serialized


def test_import_matches_agent_event_log_by_session_time_not_active_marker(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    write_active_agent_marker(home, active_agent_metadata(provider="minimax", model="MiniMax-M3"))
    glm_metadata = active_agent_metadata(provider="glm", model="glm-5.1:cloud")
    qwen_metadata = active_agent_metadata(provider="qwen", model="qwen3.6-plus")
    write_agent_event_log(
        home,
        [
            {"timestamp": "2026-05-22T09:00:00+00:00", **glm_metadata},
            {"timestamp": "2026-05-22T11:00:00+00:00", **qwen_metadata},
        ],
    )
    source_root = tmp_path / "claude"
    source_root.mkdir()
    first = source_root / "project-a" / "first.jsonl"
    second = source_root / "project-b" / "second.jsonl"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text(
        json.dumps(
            {
                "sessionId": "claude-glm-session",
                "timestamp": "2026-05-22T09:05:00+00:00",
                "type": "user",
                "cwd": "/tmp/repo",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Decision: first session."}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                "sessionId": "claude-qwen-session",
                "timestamp": "2026-05-22T11:05:00+00:00",
                "type": "user",
                "cwd": "/tmp/repo",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Decision: second session."}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "memory"

    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "claude",
            "--source-root",
            f"claude={source_root}",
            "--output",
            str(output_dir),
        ],
        env={"HOME": str(home)},
    )

    assert result.exit_code == 0
    review_rows = [
        json.loads(line)
        for line in (output_dir / "review" / "2026-05-22.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    metadata_by_text = {row["text"]: row["agent_metadata"] for row in review_rows}
    assert metadata_by_text == {
        "first session.": glm_metadata,
        "second session.": qwen_metadata,
    }


def test_import_accepts_agent_metadata_file_override(tmp_path: Path) -> None:
    home = tmp_path / "home"
    marker_path = tmp_path / "qwen-agent.json"
    metadata = active_agent_metadata(provider="qwen", model="qwen3-coder-plus")
    marker_path.write_text(json.dumps(metadata), encoding="utf-8")
    output_dir = tmp_path / "memory"

    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "claude",
            "--source-root",
            "claude=tests/fixtures/claude",
            "--output",
            str(output_dir),
            "--agent-metadata-file",
            str(marker_path),
        ],
        env={"HOME": str(home)},
    )

    assert result.exit_code == 0
    review_row = read_one_jsonl(output_dir / "review" / "2026-05-22.jsonl")
    assert review_row["agent_metadata"] == metadata


def test_import_missing_agent_metadata_marker_does_not_fail(tmp_path: Path) -> None:
    home = tmp_path / "home"
    output_dir = tmp_path / "memory"

    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "claude",
            "--source-root",
            "claude=tests/fixtures/claude",
            "--output",
            str(output_dir),
        ],
        env={"HOME": str(home)},
    )

    assert result.exit_code == 0
    review_row = read_one_jsonl(output_dir / "review" / "2026-05-22.jsonl")
    assert "agent_metadata" not in review_row


def test_import_invalid_agent_metadata_marker_warns_but_does_not_fail(tmp_path: Path) -> None:
    home = tmp_path / "home"
    marker_path = home / ".local" / "share" / "session2memory" / "active-agent.json"
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text("{not json", encoding="utf-8")
    output_dir = tmp_path / "memory"

    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "claude",
            "--source-root",
            "claude=tests/fixtures/claude",
            "--output",
            str(output_dir),
        ],
        env={"HOME": str(home)},
    )

    assert result.exit_code == 0
    assert "warning: invalid agent metadata JSON" in result.output + result.stderr
    review_row = read_one_jsonl(output_dir / "review" / "2026-05-22.jsonl")
    assert "agent_metadata" not in review_row


def test_import_does_not_infer_provider_from_transcript_text(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source_root = tmp_path / "claude"
    transcript = source_root / "project" / "claude.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps(
            {
                "sessionId": "claude-provider-text",
                "timestamp": "2026-05-22T03:00:00.000Z",
                "type": "user",
                "cwd": "/tmp/repo",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Decision: provider glm model glm-5.1 "
                                "stays in transcript text."
                            ),
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "memory"

    result = CliRunner().invoke(
        app,
        [
            "import",
            "--date",
            "2026-05-22",
            "--tool",
            "claude",
            "--source-root",
            f"claude={source_root}",
            "--output",
            str(output_dir),
        ],
        env={"HOME": str(home)},
    )

    assert result.exit_code == 0
    review_row = read_one_jsonl(output_dir / "review" / "2026-05-22.jsonl")
    evidence_row = read_one_jsonl(output_dir / "evidence" / "index.jsonl")
    assert "agent_metadata" not in review_row
    assert "agent_metadata" not in evidence_row


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


def test_openclaw_roots_default_to_the_home_state_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)

    roots = _default_source_roots()

    assert roots["openclaw"] == Path("~/.openclaw/logs").expanduser()
    assert roots["openclaw-native"] == Path("~/.openclaw/agents").expanduser()


def test_openclaw_roots_follow_the_state_dir_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setenv("OPENCLAW_STATE_DIR", state_dir.as_posix())

    roots = _default_source_roots()

    assert roots["openclaw"] == state_dir / "logs"
    assert roots["openclaw-native"] == state_dir / "agents"
