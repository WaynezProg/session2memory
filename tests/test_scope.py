"""Which directories belong to OpenClaw agents, for each way OpenClaw is laid out."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session2memory.auto.scope import forced_reader, openclaw_roots, within


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", home.as_posix())
    return home


def test_the_default_state_dir_does_not_claim_workspaces_in_the_home_dir(home: Path) -> None:
    state = home / ".openclaw"
    (state / "workspace-work").mkdir(parents=True)
    project = home / "workspaces" / "my-app"
    project.mkdir(parents=True)
    roots = openclaw_roots(state)

    assert not within(project, roots)
    assert forced_reader({}, project, state) is None
    assert forced_reader({"PWD": project.as_posix()}, project, state) is None
    # OpenClaw's own workspace locations and agent state still count.
    assert within(state / "workspace" / "notes", roots)
    assert within(state / "workspace-work" / "notes", roots)
    assert within(state / "agents" / "main" / "agent", roots)


def test_a_split_layout_claims_its_workspaces_folder(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "openclaw2"
    shared = root / "workspaces" / "shared" / "projects"
    shared.mkdir(parents=True)

    assert within(shared / "repo", openclaw_roots(root / "state"))
    # A folder named state directly in the home directory is not that layout.
    assert not within(home / "workspaces" / "my-app", openclaw_roots(home / "state"))


def test_workspaces_named_in_the_openclaw_config_count(home: Path, tmp_path: Path) -> None:
    state = tmp_path / "oc" / ".openclaw"
    state.mkdir(parents=True)
    elsewhere = tmp_path / "agents-home" / "kurisu"
    sibling_main = tmp_path / "oc" / "workspaces" / "personal" / "main"
    config = {
        "agents": {
            "defaults": {"workspace": sibling_main.as_posix()},
            "entries": {"kurisu": {"workspace": elsewhere.as_posix()}},
            "list": [{"id": "rel", "workspace": "relative/path"}],
        }
    }
    (state / "openclaw.json").write_text(json.dumps(config), encoding="utf-8")
    roots = openclaw_roots(state)

    assert within(elsewhere / "repo", roots)
    assert within(sibling_main, roots)
    # The config put a workspace in the sibling folder: the shared links there count too.
    assert within(tmp_path / "oc" / "workspaces" / "shared" / "projects" / "repo", roots)
    assert not within(tmp_path / "agents-home" / "other", roots)


def test_a_config_that_is_not_json_adds_nothing(home: Path) -> None:
    state = home / ".openclaw"
    state.mkdir()
    (state / "openclaw.json").write_text("{ // json5\n agents: {} }", encoding="utf-8")

    assert not within(home / "workspaces" / "my-app", openclaw_roots(state))
