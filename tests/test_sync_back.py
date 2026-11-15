import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory import sync_back
from session2memory.cli import app
from session2memory.memory_entries import parse_memory_markdown
from session2memory.state.store import StateStore
from session2memory.sync_back import (
    MARKER_END,
    MARKER_START,
    SyncError,
    merge_marked_section,
    sync_backup_dir,
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


def test_sync_openclaw_target_defaults_to_the_home_state_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)

    result = sync_workspace_memory(
        output_dir=output, workspace=workspace, targets=("openclaw",), dry_run=True
    )

    assert result.writes[0].path == Path("~/.openclaw/memory").expanduser() / "repo-123.md"


def test_sync_openclaw_target_follows_the_state_dir_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)
    state_dir = tmp_path / "isolated-state"
    monkeypatch.setenv("OPENCLAW_STATE_DIR", state_dir.as_posix())

    result = sync_workspace_memory(
        output_dir=output, workspace=workspace, targets=("openclaw",), dry_run=True
    )

    assert result.writes[0].path == state_dir / "memory" / "repo-123.md"


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


def test_sync_updates_marker_block_with_backslash_body(tmp_path: Path) -> None:
    existing = merge_marked_section(
        "# Repo rules\n",
        section_heading="## Session memory (session2memory)",
        section_intro="intro",
        body="old body",
    )
    body = r"- [constraint] use regex \d+ and group \1 to escape \\ here"
    updated = merge_marked_section(
        existing,
        section_heading="## Session memory (session2memory)",
        section_intro="intro",
        body=body,
    )
    assert body in updated
    assert "old body" not in updated
    assert MARKER_START in updated


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


def test_sync_refuses_a_workspace_the_manifest_does_not_know(tmp_path: Path) -> None:
    known = tmp_path / "repo-a"
    known.mkdir()
    other = tmp_path / "repo-b"
    other.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, known)  # memories/ holds exactly one file, repo-a's

    with pytest.raises(SyncError, match="Could not map workspace"):
        sync_workspace_memory(output_dir=output, workspace=other)

    assert not (other / "CLAUDE.md").exists() and not (other / "AGENTS.md").exists()


def test_sync_refuses_a_lone_memory_file_without_a_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)
    (output / "manifest.json").unlink()

    with pytest.raises(SyncError, match="Could not map workspace"):
        sync_workspace_memory(output_dir=output, workspace=workspace)


def test_sync_finds_a_workspace_the_latest_import_did_not_touch(tmp_path: Path) -> None:
    from session2memory.workspace import workspace_id_for

    earlier = tmp_path / "repo-a"
    earlier.mkdir()
    latest = tmp_path / "repo-b"
    latest.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, latest)  # the manifest only knows repo-b
    derived = workspace_id_for(earlier.resolve())
    text = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    (output / "memories" / f"{derived}.md").write_text(
        text.replace("repo-123", derived), encoding="utf-8"
    )

    result = sync_workspace_memory(output_dir=output, workspace=earlier)

    assert result.workspace_id == derived
    assert result.writes and all(w.path.is_relative_to(earlier) for w in result.writes)
    assert not (latest / "CLAUDE.md").exists()


def _backups(prefix: str) -> list[Path]:
    root = sync_backup_dir()
    return sorted(root.glob(f"{prefix}.*.bak-session2memory-*")) if root.is_dir() else []


def test_sync_leaves_the_original_intact_when_the_replace_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)
    claude = workspace / "CLAUDE.md"
    claude.write_text("# Hand-written rules\n", encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(sync_back.os, "replace", boom)
    with pytest.raises(OSError, match="No space left"):
        sync_workspace_memory(output_dir=output, workspace=workspace, targets=("claude",))

    assert claude.read_text(encoding="utf-8") == "# Hand-written rules\n"
    assert sorted(p.name for p in workspace.iterdir()) == ["CLAUDE.md"]


def test_sync_leaves_the_original_intact_when_the_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)
    agents = workspace / "AGENTS.md"
    agents.write_text("# Agents\n", encoding="utf-8")

    def boom(_fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(sync_back.os, "fsync", boom)
    with pytest.raises(OSError, match="disk full"):
        sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))

    assert agents.read_text(encoding="utf-8") == "# Agents\n"
    assert sorted(p.name for p in workspace.iterdir()) == ["AGENTS.md"]


def test_sync_backs_up_a_changed_file_once_and_keeps_its_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)
    claude = workspace / "CLAUDE.md"
    claude.write_text("# Hand-written rules\n", encoding="utf-8")
    claude.chmod(0o640)

    first = sync_workspace_memory(output_dir=output, workspace=workspace, targets=("claude",))
    second = sync_workspace_memory(output_dir=output, workspace=workspace, targets=("claude",))

    backups = _backups("CLAUDE.md")
    assert len(backups) == 1
    assert first.writes[0].backup == backups[0]
    assert backups[0].read_text(encoding="utf-8") == "# Hand-written rules\n"
    assert not backups[0].is_relative_to(workspace)
    assert second.writes[0].changed is False
    assert second.writes[0].backup is None
    assert stat.S_IMODE(claude.stat().st_mode) == 0o640
    assert MARKER_START in claude.read_text(encoding="utf-8")


def test_sync_does_not_back_up_a_file_it_creates(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)

    result = sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))

    assert result.writes[0].created is True
    assert result.writes[0].backup is None
    assert _backups("AGENTS.md") == []


def test_sync_writes_through_a_symlinked_destination(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)
    shared = tmp_path / "shared" / "AGENTS.md"
    shared.parent.mkdir()
    shared.write_text("# Shared agent rules\n", encoding="utf-8")
    (workspace / "AGENTS.md").symlink_to(shared)

    sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))

    link = workspace / "AGENTS.md"
    assert link.is_symlink()
    assert link.resolve() == shared.resolve()
    merged = shared.read_text(encoding="utf-8")
    assert merged.startswith("# Shared agent rules\n")
    assert MARKER_START in merged
    assert _backups("AGENTS.md")[0].read_text(encoding="utf-8") == "# Shared agent rules\n"


def test_sync_redacts_every_shared_secret_kind(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    output = tmp_path / "session-memory"
    _write_memory_fixture(output, workspace)
    secrets = [
        "github_pat_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
        "gho_" + "A1b2C3d4E5f6G7h8I9j0K1",
        "xoxb-" + "1234567890-abcdefghij",
        "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p",
        "Bearer " + "eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop",
    ]
    memory = output / "memories" / "repo-123.md"
    memory.write_text(
        memory.read_text(encoding="utf-8")
        + "".join(
            f"- [pitfall] Leaked {secret} in a log. "
            "{workspace_id=repo-123 memory_kind=pitfall tool=codex "
            f"session_id=s{index} evidence_id=e00001{index} lines=1-1 "
            f"review=2026-05-22/p{index}}}\n"
            for index, secret in enumerate(secrets)
        ),
        encoding="utf-8",
    )

    sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))

    agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    for secret in secrets:
        assert secret not in agents
    assert agents.count("[REDACTED:token]") == len(secrets)
