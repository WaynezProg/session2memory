import json
from pathlib import Path

import pytest

from session2memory.memory_lifecycle import MemoryLifecycleError, revoke_memory, supersede_memory
from session2memory.state.store import StateStore
from session2memory.sync_back import sync_workspace_memory


def test_revoke_memory_requires_state_db(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    with pytest.raises(MemoryLifecycleError):
        revoke_memory(output_dir=output, memory_entry_id="m_test")


def test_revoke_memory_updates_status(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    store = StateStore.open(output / "session2memory.db")
    entry_id = store.insert_memory_entry(
        workspace_id="repo-123",
        candidate_id="c_test",
        kind="decision",
        text="hello",
        evidence_id="e_test",
        review_ref="2026-05-22/p_test",
    )
    store.close()
    result = revoke_memory(output_dir=output, memory_entry_id=entry_id)
    assert result.status == "revoked"


def test_revoke_memory_exports_active_entries_and_resyncs_previous_targets(
    tmp_path: Path,
) -> None:
    output = tmp_path / "session-memory"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    first_id, second_id = _seed_two_memory_entries(output=output, workspace=workspace)
    sync_workspace_memory(output_dir=output, workspace=workspace, targets=("agents",))

    revoke_memory(output_dir=output, memory_entry_id=first_id)

    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert "first durable memory" not in memory
    assert "first durable memory" not in agents
    assert "second durable memory" in memory
    assert "second durable memory" in agents
    assert second_id in memory
    assert manifest["counts"]["durable_memories"] == 1


def test_supersede_memory_exports_new_entry_with_supersedes_metadata(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    old_id, new_id = _seed_two_memory_entries(output=output, workspace=workspace)

    supersede_memory(output_dir=output, old_id=old_id, new_id=new_id)

    memory = (output / "memories" / "repo-123.md").read_text(encoding="utf-8")
    assert "first durable memory" not in memory
    assert "second durable memory" in memory
    assert f"supersedes={old_id}" in memory


def _seed_two_memory_entries(*, output: Path, workspace: Path) -> tuple[str, str]:
    (output / "memories").mkdir(parents=True)
    output.mkdir(exist_ok=True)
    (output / "manifest.json").write_text(
        (
            '{"workspaces":{"repo-123":{'
            f'"canonical_path":"{workspace.as_posix()}",'
            f'"repo_root":"{workspace.as_posix()}",'
            f'"opened_cwd":"{workspace.as_posix()}"'
            '}},"counts":{"durable_memories":2}}'
        ),
        encoding="utf-8",
    )
    store = StateStore.open(output / "session2memory.db")
    first_id = store.insert_memory_entry(
        workspace_id="repo-123",
        candidate_id="c_first",
        kind="decision",
        text="first durable memory",
        evidence_id="e_first",
        review_ref="2026-05-22/p_first",
        tool="codex",
        session_id="s1",
        message_start=1,
        message_end=1,
    )
    second_id = store.insert_memory_entry(
        workspace_id="repo-123",
        candidate_id="c_second",
        kind="decision",
        text="second durable memory",
        evidence_id="e_second",
        review_ref="2026-05-22/p_second",
        tool="codex",
        session_id="s2",
        message_start=2,
        message_end=2,
    )
    store.export_memory_entries(output_dir=output)
    store.close()
    return first_id, second_id
