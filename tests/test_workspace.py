import subprocess
from pathlib import Path

from session2memory.models import SessionRecord
from session2memory.workspace import resolve_workspace


def make_record(cwd: Path, tool_workspace_id: str | None = None) -> SessionRecord:
    return SessionRecord(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/session.jsonl"),
        started_at=None,
        updated_at=None,
        cwd=cwd,
        repo_root=None,
        tool_workspace_id=tool_workspace_id,
        messages=[],
    )


def test_nested_cwd_groups_to_git_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "packages" / "api"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    workspace = resolve_workspace(make_record(nested))

    assert workspace.canonical_path == repo.resolve()
    assert workspace.repo_root == repo.resolve()
    assert workspace.workspace_id.startswith("repo-")


def test_non_git_cwd_groups_to_canonical_cwd(tmp_path: Path) -> None:
    folder = tmp_path / "notes"
    folder.mkdir()

    workspace = resolve_workspace(make_record(folder, "tool-ws"))

    assert workspace.canonical_path == folder.resolve()
    assert workspace.repo_root is None
    assert workspace.tool_workspace_id == "tool-ws"
