"""`session2memory setup`: the hook in every agent on the machine, and the nightly job."""

from __future__ import annotations

import json
import os
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
    """Stands in for launchctl: remembers whether the job is loaded and what it was asked."""

    def __init__(self, bootstrap: int = 0, bootout: int = 0, loaded: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.bootstrap = bootstrap
        self.bootout = bootout
        self.loaded = loaded

    def __call__(self, arguments: object) -> int:
        call = [str(one) for one in arguments]  # type: ignore[attr-defined]
        self.calls.append(call)
        if call[1] == "print":
            return 0 if self.loaded else 113
        if call[1] == "bootstrap":
            self.loaded = self.loaded or self.bootstrap == 0
            return self.bootstrap
        if self.bootout == 0:
            self.loaded = False
        return self.bootout

    @property
    def changes(self) -> list[str]:
        """What was asked of launchd beyond looking."""
        return [call[1] for call in self.calls if call[1] != "print"]


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
    assert launch.changes == ["bootstrap"], "nothing was loaded before, so nothing to unload"
    assert launch.calls[-1][-1] == job_file.as_posix()
    assert plan.applied and not plan.failed

    before = {name: (home / name).read_bytes() for name in files(home)}
    launch.calls.clear()
    again = plan_for(home, launch, apply=True)

    assert not again.pending and launch.changes == []
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
    assert launch.changes == ["bootout"]
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
    assert launch.changes[-2:] == ["bootout", "bootstrap"], "the loaded copy is replaced"
    assert job["StartCalendarInterval"] == {"Hour": 3, "Minute": 5}
    assert [command[i + 1] for i, one in enumerate(command) if one == "--step"][:2] == [
        "scan", "compress",
    ]  # fmt: skip
    assert plan_for(home, launch, schedule=False).schedule.kind == "none"


def test_a_job_launchd_will_not_load_is_reported_and_tried_again(home: Path) -> None:
    launch = Launchd(bootstrap=5)
    (home / "store").mkdir()
    (home / "store" / "session2memory.db").write_bytes(b"")

    plan = plan_for(home, launch, apply=True, hooks=False)

    assert plan.failed and "launchctl bootstrap" in (plan.schedule.error or "")
    assert "Finished with errors" in s2m_setup.render(plan) and "Done." not in s2m_setup.render(
        plan
    )

    # The file is there but nothing is loaded: the next run does not call that "installed".
    launch.bootstrap = 0
    launch.calls.clear()
    preview = plan_for(home, launch, hooks=False)
    again = plan_for(home, launch, apply=True, hooks=False)

    assert preview.pending and "has not loaded it" in preview.schedule.detail
    assert launch.changes == ["bootstrap"] and not again.failed
    assert not plan_for(home, launch, hooks=False).pending


def test_a_job_launchd_will_not_unload_keeps_its_file(home: Path) -> None:
    launch = Launchd()
    plan_for(home, launch, apply=True, hooks=False)
    launch.bootout = 5

    plan = plan_for(home, launch, remove=True, apply=True, hooks=False)

    assert plan.failed and "did not unload" in (plan.schedule.error or "")
    assert (home / "Library/LaunchAgents" / f"{LABEL}.plist").exists()

    # Not loaded (unloaded by hand since): the file just goes.
    launch.loaded = False
    launch.calls.clear()
    assert not plan_for(home, launch, remove=True, apply=True, hooks=False).failed
    assert launch.changes == [] and not (home / "Library/LaunchAgents" / f"{LABEL}.plist").exists()


def test_a_file_of_the_users_own_under_our_name_is_never_touched(home: Path) -> None:
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    mine = plistlib.dumps({"Label": LABEL, "ProgramArguments": ["/usr/bin/true"]})
    (agents / f"{LABEL}.plist").write_bytes(mine)
    launch = Launchd()

    for options in ({"apply": True}, {"remove": True, "apply": True}):
        plan = plan_for(home, launch, hooks=False, **options)
        assert plan.schedule.action == "kept" and "not written by setup" in plan.schedule.detail
        assert not plan.pending
    assert (agents / f"{LABEL}.plist").read_bytes() == mine and launch.changes == []


def test_which_jobs_count_as_running_the_pipeline() -> None:
    def job(*arguments: str) -> dict[str, object]:
        return {"Label": "x", "ProgramArguments": list(arguments)}

    runs = s2m_setup.runs_the_pipeline
    assert runs(job("/bin/bash", "/repo/scripts/nightly-auto.sh", "--step", "scan"))
    assert runs(job("/opt/bin/session2memory", "auto", "run", "--json"))
    assert runs(job("/bin/bash", "-c", "exec /opt/bin/session2memory 'auto' run"))
    assert runs(job("/bin/zsh", "-lc", "cd /repo && uv run session2memory auto run"))
    assert not runs(job("/opt/bin/session2memory", "auto", "status"))
    assert not runs(job("/opt/session2memory/helper.py", "auto"))
    assert not runs(job("/bin/bash", "-c", "exec /repo/scripts/daily-session-memory-to-hks.sh"))
    assert not runs({"Label": "x"}) and not runs("not a job")


def test_the_job_keeps_what_the_run_needs_from_the_environment(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = home / "my tools"
    tools.mkdir()
    curator = tools / "my-curator"
    curator.write_text("#!/bin/sh\n", encoding="utf-8")
    curator.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools.as_posix()}:/usr/bin:/bin")
    monkeypatch.setenv("SESSION2MEMORY_AUTO_LLM_CMD", "my-curator --fast")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", "/srv/openclaw")
    monkeypatch.chdir(home)

    plan = run_setup(
        store=Path("relative store"), executable=EXE, platform="darwin", launch=Launchd(),
        apply=True, hooks=False,
    )  # fmt: skip
    job = plistlib.loads((home / "Library/LaunchAgents" / f"{LABEL}.plist").read_bytes())
    environment = job["EnvironmentVariables"]

    stored = plan.schedule.command[plan.schedule.command.index("--store") + 1]
    assert Path(stored).is_absolute() and stored.endswith("/relative store")
    assert job["StandardOutPath"].startswith(stored) and Path(stored, "logs").is_dir()
    assert environment["PATH"].split(":")[0] == tools.as_posix()
    assert environment["SESSION2MEMORY_AUTO_LLM_CMD"] == "my-curator --fast"
    assert environment["OPENCLAW_STATE_DIR"] == "/srv/openclaw"


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
    assert (home / "store" / "logs").is_dir(), "the line appends to a log there"


def test_the_crontab_line_survives_a_percent_sign_and_keeps_its_output(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", "/srv/open claw")
    store = home / "100% store"
    command = s2m_setup.nightly_command(EXE, store, compress=False)

    line = s2m_setup.cron_line(command, store, hour=4, minute=7)

    assert line.startswith("7 4 * * * PATH=")
    assert "100\\% store" in line and "%" not in line.replace("\\%", "")
    assert "OPENCLAW_STATE_DIR='/srv/open claw'" in line and "HOME=" not in line
    assert line.endswith("cron.log' 2>&1") and "/dev/null" not in line


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
        old = logs / f"run-20261001-{index:06d}.json"
        old.write_text("{}", encoding="utf-8")
        os.utime(old, (1_000_000 + index, 1_000_000 + index))
    by_hand = logs / "run-first-manual.json"
    by_hand.write_text("{}", encoding="utf-8")
    os.utime(by_hand, (1, 1))
    when = datetime(2026, 11, 14, 5, 30)

    path = cli._save_report(tmp_path, {"scan": {"files_seen": 1}}, now=when)
    second = cli._save_report(tmp_path, {"scan": {"files_seen": 2}}, now=when)

    kept = sorted(one.name for one in logs.glob("run-2*.json"))
    assert (path.name, second.name) == ("run-20261114-053000.json", "run-20261114-053000-1.json")
    assert json.loads(path.read_text())["scan"]["files_seen"] == 1, "not overwritten"
    assert len(kept) == cli.REPORTS_KEPT and {path.name, second.name} <= set(kept)
    assert "run-20261001-000000.json" not in kept and "run-20261001-000062.json" in kept
    assert by_hand.exists(), "a report saved under another name is not ours to remove"


def test_a_saved_report_still_prints_json_when_asked(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = home / "store"
    arguments = ["auto", "run", "--store", store.as_posix(), "--step", "scan", "--save-report"]

    plain = CliRunner().invoke(app, arguments)
    as_json = CliRunner().invoke(app, [*arguments, "--json"])
    rehearsal = CliRunner().invoke(app, [*arguments, "--dry-run"])

    assert plain.output.startswith("report=") and plain.output.count("\n") == 1
    assert json.loads(as_json.output)["dry_run"] is False
    assert "report=" not in rehearsal.output and len(list((store / "logs").glob("*.json"))) == 2


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


def test_only_a_command_that_starts_our_hook_is_taken_for_ours(tmp_path: Path) -> None:
    from session2memory.hook_install import is_our_command, update_settings

    assert is_our_command("'/opt/s2m/bin/session2memory' session-start-hook --agent claude")
    assert is_our_command(
        '[ -s "$HOME/seen/grok-${GROK_SESSION_ID:-}" ] && exit 0; '
        "'/x/session2memory' session-start-hook --agent grok --event PostToolUse --once"
    )
    assert not is_our_command("echo 'session2memory session-start-hook --agent claude'")
    assert not is_our_command("./notify.sh  # after session2memory session-start-hook --agent")
    assert not is_our_command("'unterminated")

    # Antigravity: an entry that only has our name is not ours to replace or remove.
    settings = tmp_path / "hooks.json"
    theirs = {"session2memory": {"Stop": [{"type": "command", "command": "/bin/echo hello"}]}}
    settings.write_text(json.dumps(theirs), encoding="utf-8")
    for remove in (False, True):
        with pytest.raises(ValueError, match="not written by this tool"):
            update_settings(
                settings, agent="antigravity", executable=EXE, remove=remove, apply=True
            )
    assert json.loads(settings.read_text()) == theirs
