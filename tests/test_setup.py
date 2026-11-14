"""`session2memory setup`: the hook in every agent on the machine, and the nightly job."""

from __future__ import annotations

import json
import plistlib
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory import cli
from session2memory import setup as s2m_setup
from session2memory.cli import app
from session2memory.setup import LABEL, run_setup

EXE = Path("/opt/s2m/bin/session2memory")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty home folder with Claude Code and Cursor on the machine, no other agent."""
    monkeypatch.setenv("HOME", tmp_path.as_posix())
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    monkeypatch.delenv("SESSION2MEMORY_AUTO_LLM_CMD", raising=False)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()
    return tmp_path


class Launchd:
    """Stands in for launchctl and records what it was asked."""

    def __init__(self, bootstrap: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.bootstrap = bootstrap

    def __call__(self, arguments: object) -> int:
        call = [str(one) for one in arguments]  # type: ignore[attr-defined]
        self.calls.append(call)
        return self.bootstrap if call[1] == "bootstrap" else 0


def plan_for(home: Path, launch: Launchd, **options: object) -> s2m_setup.SetupPlan:
    return run_setup(
        store=home / "store",
        executable=EXE,
        platform="darwin",
        launch=launch,
        **options,  # type: ignore[arg-type]
    )


def files(home: Path) -> set[str]:
    return {path.relative_to(home).as_posix() for path in home.rglob("*") if path.is_file()}


def test_the_preview_changes_nothing(home: Path) -> None:
    launch = Launchd()

    plan = plan_for(home, launch)

    assert files(home) == set() and launch.calls == []
    assert {step.agent: step.action for step in plan.hooks} == {
        "claude": "add", "cursor": "add", "codex": "skipped", "muse": "skipped",
        "grok": "skipped", "antigravity": "skipped",
    }  # fmt: skip
    assert (plan.schedule.kind, plan.schedule.action) == ("launchd", "add")
    assert plan.pending and not plan.applied and not plan.failed


def test_applying_installs_the_hooks_and_the_job_and_again_changes_nothing(home: Path) -> None:
    launch = Launchd()

    plan = plan_for(home, launch, apply=True)

    job_file = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    assert files(home) == {
        ".claude/settings.json", ".cursor/hooks.json", f"Library/LaunchAgents/{LABEL}.plist"
    }  # fmt: skip
    assert "session-start-hook --agent claude" in (home / ".claude/settings.json").read_text()
    job = plistlib.loads(job_file.read_bytes())
    command = job["ProgramArguments"]
    assert command[:4] == [EXE.as_posix(), "auto", "run", "--store"]
    assert "--save-report" in command and command[command.index("--curate-scope") + 1] == "coding"
    steps = [command[i + 1] for i, one in enumerate(command) if one == "--step"]
    assert steps == ["scan", "archive", "curate", "reconcile", "embed", "export"]
    assert job["StartCalendarInterval"] == {"Hour": 5, "Minute": 30}
    assert job["EnvironmentVariables"]["HOME"] == home.as_posix()
    assert "/usr/bin" in job["EnvironmentVariables"]["PATH"].split(":")
    assert [call[1] for call in launch.calls] == ["bootout", "bootstrap"]
    assert launch.calls[1][-1] == job_file.as_posix()
    assert plan.applied and not plan.failed

    before = {name: (home / name).read_bytes() for name in files(home)}
    launch.calls.clear()
    again = plan_for(home, launch, apply=True)

    assert not again.pending and launch.calls == []
    assert {name: (home / name).read_bytes() for name in files(home)} == before


def test_removing_takes_both_out_again(home: Path) -> None:
    theirs = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "./mine.sh"}]}]}}
    (home / ".claude/settings.json").write_text(json.dumps(theirs), encoding="utf-8")
    launch = Launchd()
    plan_for(home, launch, apply=True)
    launch.calls.clear()

    preview = plan_for(home, launch, remove=True)
    assert (home / "Library/LaunchAgents" / f"{LABEL}.plist").exists(), "a preview removes nothing"
    plan = plan_for(home, launch, remove=True, apply=True)

    assert preview.schedule.action == plan.schedule.action == "remove"
    assert {step.agent: step.action for step in plan.hooks if step.action != "skipped"} == {
        "claude": "remove", "cursor": "remove",
    }  # fmt: skip
    assert not (home / "Library/LaunchAgents" / f"{LABEL}.plist").exists()
    assert json.loads((home / ".claude/settings.json").read_text()) == theirs, "their hook stays"
    assert [call[1] for call in launch.calls] == ["bootout"]
    assert not plan_for(home, launch, remove=True).pending


def test_a_job_that_already_runs_the_pipeline_is_not_doubled(home: Path) -> None:
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.me.memory.plist").write_bytes(
        plistlib.dumps(
            {"Label": "com.me.memory", "ProgramArguments": ["/bin/bash", "/x/nightly-auto.sh"]}
        )
    )
    (agents / "com.me.other.plist").write_bytes(
        plistlib.dumps({"Label": "com.me.other", "ProgramArguments": ["/bin/true"]})
    )
    (agents / "broken.plist").write_text("not a property list", encoding="utf-8")
    launch = Launchd()

    plan = plan_for(home, launch, apply=True)

    assert plan.schedule.action == "kept" and "com.me.memory" in plan.schedule.detail
    assert not (agents / f"{LABEL}.plist").exists() and launch.calls == []
    assert {step.action for step in plan.hooks if step.agent in {"claude", "cursor"}} == {"add"}


def test_options_change_the_job(home: Path) -> None:
    launch = Launchd()
    plan_for(home, launch, apply=True)

    plan = plan_for(home, launch, apply=True, hour=3, minute=5, compress=True, hooks=False)

    job = plistlib.loads((home / "Library/LaunchAgents" / f"{LABEL}.plist").read_bytes())
    command = job["ProgramArguments"]
    assert plan.schedule.action == "replace" and plan.hooks == []
    assert job["StartCalendarInterval"] == {"Hour": 3, "Minute": 5}
    assert [command[i + 1] for i, one in enumerate(command) if one == "--step"][:2] == [
        "scan", "compress",
    ]  # fmt: skip
    assert plan_for(home, launch, schedule=False).schedule.kind == "none"


def test_a_job_launchd_will_not_load_is_reported(home: Path) -> None:
    plan = plan_for(home, Launchd(bootstrap=5), apply=True, hooks=False)

    assert plan.failed and "launchctl bootstrap" in (plan.schedule.error or "")


def test_one_agents_broken_settings_do_not_stop_the_others(home: Path) -> None:
    (home / ".claude/settings.json").write_text("{ not json", encoding="utf-8")

    plan = plan_for(home, Launchd(), apply=True, schedule=False)

    by_agent = {step.agent: step for step in plan.hooks}
    assert by_agent["claude"].error and by_agent["cursor"].action == "add"
    assert (home / ".cursor/hooks.json").exists()
    assert (home / ".claude/settings.json").read_text() == "{ not json", "left as it was"
    assert plan.failed


def test_elsewhere_than_macos_the_crontab_line_is_given_not_installed(home: Path) -> None:
    launch = Launchd()

    plan = run_setup(
        store=home / "store", executable=EXE, platform="linux", launch=launch, apply=True,
        hooks=False,
    )  # fmt: skip

    assert (plan.schedule.kind, plan.schedule.action) == ("cron", "manual")
    assert "30 5 * * * PATH=" in plan.schedule.detail and "auto run" in plan.schedule.detail
    assert files(home) == set() and launch.calls == []


def test_the_command_shows_the_plan_and_fails_only_when_something_failed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(s2m_setup.sys, "platform", "linux")
    runner = CliRunner()

    preview = runner.invoke(app, ["setup", "--store", (home / "store").as_posix()])
    as_json = runner.invoke(app, ["setup", "--json", "--store", (home / "store").as_posix()])

    assert preview.exit_code == 0 and "nothing is changed without --apply" in preview.output
    assert "claude" in preview.output and "not on this machine" in preview.output
    assert "Run it again with --apply" in preview.output
    assert json.loads(as_json.output)["hooks"][0]["agent"] == "claude"
    assert files(home) == set()

    (home / ".claude/settings.json").write_text("{ not json", encoding="utf-8")
    assert runner.invoke(app, ["setup", "--apply", "--no-schedule"]).exit_code == 1


def test_a_scheduled_run_keeps_its_report_and_only_the_newest(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    for index in range(cli.REPORTS_KEPT + 3):
        old = logs / f"run-old-{index:03d}.json"
        old.write_text("{}", encoding="utf-8")
        stamp = 1_000_000 + index
        import os

        os.utime(old, (stamp, stamp))

    path = cli._save_report(
        tmp_path, {"scan": {"files_seen": 1}}, now=datetime(2026, 11, 14, 5, 30)
    )

    kept = sorted(one.name for one in logs.glob("run-*.json"))
    assert path.name == "run-20261114-053000.json" and path.name in kept
    assert json.loads(path.read_text())["scan"]["files_seen"] == 1
    assert len(kept) == cli.REPORTS_KEPT
    assert "run-old-000.json" not in kept and "run-old-062.json" in kept


def test_archiving_without_claude_code_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from session2memory.auto import files
    from session2memory.auto.run import DB_NAME
    from session2memory.auto.store import AutoStore

    now = datetime(2026, 11, 14, 5, 30)
    store = AutoStore.open(tmp_path / DB_NAME)
    try:
        quiet = files.archive_claude(
            store, tmp_path / "no-claude", tmp_path / "archive", now=now, min_age_days=10
        )
        (tmp_path / "projects").mkdir()
        monkeypatch.setattr(files.shutil, "which", lambda name: None)
        without_zstd = files.archive_claude(
            store, tmp_path / "projects", tmp_path / "archive", now=now, min_age_days=10
        )
    finally:
        store.close()

    assert quiet.errors == [] and quiet.done == 0
    assert without_zstd.errors == ["zstd executable not found; nothing was archived"]
