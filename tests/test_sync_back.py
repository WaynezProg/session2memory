import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory.cli import app
from session2memory.memory_entries import parse_memory_markdown
from session2memory.state.store import StateStore
from session2memory.sync_back import (
    MARKER_END,
    MARKER_START,
    SyncError,
    merge_marked_section,
    sync_workspace_memory,
)


def _write_memory_fixture(output: Path, workspace: Path) -> None:
    (output / "memories").mkdir(parents=True)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "workspaces": {
                    "repo-123": {
                        "canonical_path": workspace.as_posix(),
                        "repo_root": workspace.as_posix(),
                        "opened_cwd": workspace.as_posix(),
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (output / "memories" / "repo-123.md").write_text(
        "\n".join(
            (
                "---",
                "hks_type: workspace_memory",
                "workspace_id: repo-123",
                "---",
                "# repo-123",
                "",
                "- [decision] Use evidence-backed memory compiler. "
                "{workspace_id=repo-123 memory_kind=decision tool=codex "
                "session_id=s1 evidence_id=e000001 lines=2-2 review=2026-05-22/pabc}",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def test_parse_memory_markdown_extracts_entries() -> None:
    entries = parse_memory_markdown(
        "- [pitfall] Never ingest raw sessions. "
        "{workspace_id=repo memory_kind=pitfall tool=cursor session_id=s2 "
        "evidence_id=e000002 lines=3-3 review=2026-05-22/pdef}"
    )
    assert len(entries) == 1
    assert entries[0].kind == "pitfall"
    assert entries[0].evidence_id == "e000002"


def test_sync_writes_agents_and_claude_marked_sections(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)

    result = sync_workspace_memory(
        output_dir=output,
        workspace=workspace,
        targets=("agents", "claude"),
        dry_run=False,
    )

    assert result.workspace_id == "repo-123"
    agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    claude = (workspace / "CLAUDE.md").read_text(encoding="utf-8")
    assert MARKER_START in agents
    assert "Use evidence-backed memory compiler." in agents
    assert "e000001" in agents
    assert MARKER_START in claude
    assert "/tmp/raw" not in agents


def test_sync_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)

    sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))
    first = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    second_result = sync_workspace_memory(
        output_dir=output, workspace=workspace, targets=("agents",)
    )
    second = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert first == second
    assert second_result.writes[0].changed is False


def test_sync_updates_existing_marker_block(tmp_path: Path) -> None:
    existing = merge_marked_section(
        "# Repo rules\n",
        section_heading="## Session memory (session2memory)",
        section_intro="intro",
        body="old body",
    )
    updated = merge_marked_section(
        existing,
        section_heading="## Session memory (session2memory)",
        section_intro="intro",
        body="new body",
    )
    assert "new body" in updated
    assert "old body" not in updated
    assert MARKER_START in updated
    assert MARKER_END in updated


def test_sync_requires_promoted_memory(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    output.mkdir()
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "workspaces": {
                    "repo-123": {"canonical_path": workspace.as_posix()},
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SyncError, match="No promoted memory"):
        sync_workspace_memory(output_dir=output, workspace=workspace)


def test_sync_since_last_sync_skips_unchanged_body(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)

    sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))
    second = sync_workspace_memory(
        output_dir=output,
        workspace=workspace,
        targets=("agents",),
        since_last_sync=True,
    )
    assert second.writes[0].changed is False


def test_sync_records_hash_when_destination_is_already_current(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)

    sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))
    StateStore.open(output / "session2memory.db").close()

    result = sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))

    store = StateStore.open(output / "session2memory.db")
    assert result.writes[0].changed is False
    assert (
        store.get_sync_hash(
            workspace_id="repo-123",
            target="agents",
            dest_path=(workspace / "AGENTS.md").as_posix(),
        )
        is not None
    )
    store.close()


def test_sync_cli_dry_run_reports_planned_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)

    result = CliRunner().invoke(
        app,
        [
            "sync",
            "--workspace",
            str(workspace),
            "--output",
            str(output),
            "--target",
            "cursor",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "would_write=created" in result.output
    assert "session2memory-memory.mdc" in result.output
    assert not (workspace / ".cursor" / "rules" / "session2memory-memory.mdc").exists()
