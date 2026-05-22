import subprocess
from pathlib import Path

from session2memory.models import SessionRecord
from session2memory.workspace import resolve_workspace


def make_record(
    cwd: Path | None,
    tool_workspace_id: str | None = None,
    source_path: Path = Path("/tmp/session.jsonl"),
) -> SessionRecord:
    return SessionRecord(
        tool="codex",
        session_id="s1",
        source_path=source_path,
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


def test_missing_cwd_preserves_opened_identity_without_git(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing = tmp_path / "deleted" / "workspace"
    source_parent = tmp_path / "sessions"
    source_parent.mkdir()

    def fail_git(*args: object, **kwargs: object) -> None:
        raise AssertionError("git should not be called for missing cwd")

    monkeypatch.setattr("session2memory.workspace.subprocess.run", fail_git)

    workspace = resolve_workspace(
        make_record(missing, source_path=source_parent / "session.jsonl")
    )

    assert workspace.canonical_path == missing.resolve(strict=False)
    assert workspace.repo_root is None
    assert workspace.opened_cwd == missing.resolve(strict=False)


def test_missing_cwd_keeps_distinct_workspaces_for_same_source_parent(tmp_path: Path) -> None:
    source_parent = tmp_path / "sessions"
    source_parent.mkdir()
    first_missing = tmp_path / "deleted" / "alpha"
    second_missing = tmp_path / "deleted" / "beta"

    first = resolve_workspace(
        make_record(first_missing, source_path=source_parent / "first.jsonl")
    )
    second = resolve_workspace(
        make_record(second_missing, source_path=source_parent / "second.jsonl")
    )

    assert first.canonical_path == first_missing.resolve(strict=False)
    assert second.canonical_path == second_missing.resolve(strict=False)
    assert first.workspace_id != second.workspace_id


def test_none_cwd_falls_back_to_source_parent_with_no_opened_cwd(tmp_path: Path) -> None:
    source_parent = tmp_path / "sessions"
    source_parent.mkdir()

    workspace = resolve_workspace(
        make_record(None, source_path=source_parent / "session.jsonl")
    )

    assert workspace.canonical_path == source_parent.resolve()
    assert workspace.repo_root is None
    assert workspace.opened_cwd is None
