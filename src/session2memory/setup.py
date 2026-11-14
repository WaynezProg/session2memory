"""One command that does what follows the install: the memory hook in every agent that is
on this machine, and the nightly run.

Everything is planned first and written only with ``apply``. A plan lists, per agent and
for the schedule, what would change and where; applying it backs up each settings file it
touches (``hook_install``) and can be undone with ``remove``.

The nightly job starts the installed ``session2memory`` itself, so no checkout is needed.
On macOS it is a launchd agent; elsewhere the plan gives the crontab line to add, since a
crontab is the user's own file to edit.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from session2memory.hook_install import AGENT_HOOKS, default_executable, update_settings

LABEL = "com.session2memory.nightly"
# The steps of the nightly run. ``compress`` is left out unless asked for: it is the one
# step that replaces files in place (old rollouts in OpenClaw agents' Codex homes).
NIGHTLY_STEPS = ("scan", "archive", "curate", "reconcile", "embed", "export")
NIGHTLY_MAX_LLM_CALLS = 40
SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")

# The folder whose presence says the agent is used on this machine.
AGENT_HOMES: dict[str, str] = {
    "claude": "~/.claude",
    "codex": "~/.codex",
    "muse": "~/.config/muse",
    "grok": "~/.grok",
    "antigravity": "~/.gemini",
    "cursor": "~/.cursor",
}

Launcher = Callable[[Sequence[str]], int]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HookStep:
    agent: str
    # add, replace, remove, none (nothing to change) or skipped (the agent is not here)
    action: str
    settings: str
    backup: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ScheduleStep:
    # launchd, cron or none (the schedule was not asked for)
    kind: str
    # add, replace, remove, none, kept (another job already runs it) or manual (a line
    # for the user to add)
    action: str
    path: str | None = None
    detail: str = ""
    command: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class SetupPlan:
    applied: bool
    removing: bool
    executable: str
    checks: list[Check]
    hooks: list[HookStep]
    schedule: ScheduleStep
    has_store: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def failed(self) -> bool:
        return any(step.error for step in self.hooks) or bool(self.schedule.error)

    @property
    def pending(self) -> bool:
        """Something would change if this plan were applied."""
        return any(step.action in {"add", "replace", "remove"} for step in self.hooks) or (
            self.schedule.action in {"add", "replace", "remove"}
        )


def agents_present() -> list[str]:
    return [agent for agent, home in AGENT_HOMES.items() if Path(home).expanduser().is_dir()]


def checks(executable: Path) -> list[Check]:
    found: list[Check] = []
    zstd = shutil.which("zstd")
    found.append(
        Check(
            "zstd",
            zstd is not None,
            zstd or "not on PATH; the archive step needs it (brew install zstd)",
        )
    )
    custom = os.environ.get("SESSION2MEMORY_AUTO_LLM_CMD")
    claude = shutil.which("claude")
    found.append(
        Check(
            "model command",
            bool(custom or claude),
            (f"SESSION2MEMORY_AUTO_LLM_CMD={custom}" if custom else claude)
            or "no `claude` on PATH; the curate and reconcile steps need it (or --llm-cmd)",
        )
    )
    inside_checkout = ".venv" in executable.parts
    found.append(
        Check(
            "command",
            executable.is_file() and not inside_checkout,
            (
                f"{executable.as_posix()} is in a checkout's virtualenv: the hook and the "
                "nightly job point at it and stop working if the checkout moves. "
                "`uv tool install` gives a path that stays."
            )
            if inside_checkout
            else executable.as_posix()
            if executable.is_file()
            else f"{executable.as_posix()} does not exist",
        )
    )
    return found


def plan_hooks(
    *, executable: Path, remove: bool, apply: bool, agents: Sequence[str] | None = None
) -> list[HookStep]:
    present = set(agents_present())
    steps: list[HookStep] = []
    for agent in agents or list(AGENT_HOOKS):
        settings = Path(AGENT_HOOKS[agent].settings).expanduser()
        if agent not in present:
            steps.append(HookStep(agent, "skipped", settings.as_posix()))
            continue
        try:
            plan = update_settings(
                settings, agent=agent, executable=executable, remove=remove, apply=apply
            )
        except (OSError, ValueError) as exc:
            # One agent's unreadable settings file must not stop the others.
            steps.append(HookStep(agent, "none", settings.as_posix(), error=str(exc)))
            continue
        steps.append(
            HookStep(
                agent,
                plan.action,
                settings.as_posix(),
                backup=plan.backup_path.as_posix() if plan.backup_path else None,
            )
        )
    return steps


def nightly_command(executable: Path, store: Path, *, compress: bool) -> list[str]:
    steps = ("scan", "compress", *NIGHTLY_STEPS[1:]) if compress else NIGHTLY_STEPS
    command = [
        executable.as_posix(), "auto", "run", "--store", store.as_posix(), "--save-report",
        "--max-llm-calls", str(NIGHTLY_MAX_LLM_CALLS), "--curate-scope", "coding",
    ]  # fmt: skip
    for step in steps:
        command += ["--step", step]
    return command


def job_path() -> str:
    """PATH for the job: where the programs a run starts are now, then the system's own.
    launchd and cron start jobs with a bare PATH."""
    found: list[str] = []
    for program in ("claude", "zstd", "uv"):
        located = shutil.which(program)
        if located and (directory := str(Path(located).parent)) not in found:
            found.append(directory)
    return ":".join([*found, *(one for one in SYSTEM_PATH if one not in found)])


def launchd_plist(command: list[str], store: Path, *, hour: int, minute: int) -> bytes:
    environment = {"HOME": str(Path.home()), "PATH": job_path()}
    for name in ("OPENCLAW_STATE_DIR", "SESSION2MEMORY_AUTO_LLM_CMD"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    logs = store / "logs"
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": command,
            "EnvironmentVariables": environment,
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "StandardOutPath": (logs / "launchd.out.log").as_posix(),
            "StandardErrorPath": (logs / "launchd.err.log").as_posix(),
        },
        sort_keys=False,
    )


def other_schedule(agents_dir: Path) -> str | None:
    """The label of another launchd job that already runs the unattended pipeline."""
    for path in sorted(agents_dir.glob("*.plist")):
        try:
            job = plistlib.loads(path.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        if not isinstance(job, dict) or job.get("Label") == LABEL:
            continue
        arguments = " ".join(str(one) for one in job.get("ProgramArguments") or [])
        if "nightly-auto.sh" in arguments or (
            "session2memory" in arguments and " auto " in f" {arguments} "
        ):
            return str(job.get("Label") or path.stem)
    return None


def _launch(arguments: Sequence[str]) -> int:
    return subprocess.run(list(arguments), capture_output=True, check=False).returncode


def plan_schedule(
    *,
    executable: Path,
    store: Path,
    hour: int,
    minute: int,
    compress: bool,
    remove: bool,
    apply: bool,
    platform: str = sys.platform,
    launch: Launcher = _launch,
) -> ScheduleStep:
    command = nightly_command(executable, store, compress=compress)
    when = f"every day at {hour:02d}:{minute:02d}"
    if platform != "darwin":
        if remove:
            return ScheduleStep("cron", "none", detail="remove the line from your crontab")
        line = (
            f"{minute} {hour} * * * PATH={shlex.quote(job_path())} {shlex.join(command)} "
            ">/dev/null 2>&1"
        )
        return ScheduleStep(
            "cron", "manual", detail=f"add this line with `crontab -e` ({when}):\n{line}",
            command=command,
        )  # fmt: skip
    agents_dir = Path("~/Library/LaunchAgents").expanduser()
    path = agents_dir / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    if remove:
        if not path.exists():
            return ScheduleStep("launchd", "none", path.as_posix(), "no job installed")
        if apply:
            launch(["launchctl", "bootout", f"{domain}/{LABEL}"])
            path.unlink()
        return ScheduleStep("launchd", "remove", path.as_posix(), when)
    other = other_schedule(agents_dir)
    if other is not None and not path.exists():
        return ScheduleStep(
            "launchd", "kept", None,
            f"already scheduled by the job {other}; a second one is not added",
        )  # fmt: skip
    wanted = launchd_plist(command, store, hour=hour, minute=minute)
    if path.exists() and path.read_bytes() == wanted:
        return ScheduleStep("launchd", "none", path.as_posix(), when, command)
    action = "replace" if path.exists() else "add"
    if not apply:
        return ScheduleStep("launchd", action, path.as_posix(), when, command)
    agents_dir.mkdir(parents=True, exist_ok=True)
    (store / "logs").mkdir(parents=True, exist_ok=True)
    path.write_bytes(wanted)
    launch(["launchctl", "bootout", f"{domain}/{LABEL}"])  # an older copy, if loaded
    if launch(["launchctl", "bootstrap", domain, path.as_posix()]) != 0:
        return ScheduleStep(
            "launchd", action, path.as_posix(), when, command,
            error="the file was written but launchd did not load it; "
            f"try `launchctl bootstrap {domain} {shlex.quote(path.as_posix())}`",
        )  # fmt: skip
    return ScheduleStep("launchd", action, path.as_posix(), when, command)


def run_setup(
    *,
    store: Path,
    apply: bool = False,
    remove: bool = False,
    hooks: bool = True,
    schedule: bool = True,
    hour: int = 5,
    minute: int = 30,
    compress: bool = False,
    executable: Path | None = None,
    platform: str = sys.platform,
    launch: Launcher = _launch,
) -> SetupPlan:
    exe = executable or default_executable()
    store = store.expanduser()
    return SetupPlan(
        applied=apply,
        removing=remove,
        executable=exe.as_posix(),
        checks=checks(exe),
        hooks=plan_hooks(executable=exe, remove=remove, apply=apply) if hooks else [],
        schedule=plan_schedule(
            executable=exe, store=store, hour=hour, minute=minute, compress=compress,
            remove=remove, apply=apply, platform=platform, launch=launch,
        )
        if schedule
        else ScheduleStep("none", "none"),
        has_store=(store / "session2memory.db").is_file(),
    )  # fmt: skip


def render(plan: SetupPlan) -> str:
    """The plan for a person: what was checked, and what changes where."""
    done = plan.applied
    lines = [
        "session2memory setup"
        + (" --remove" if plan.removing else "")
        + ("" if done else "  (preview: nothing is changed without --apply)"),
        "",
        "Checks",
    ]
    for check in plan.checks:
        lines.append(f"  {'ok  ' if check.ok else 'note'}  {check.name:<14} {check.detail}")
    if plan.hooks:
        lines += ["", "Memory hook (gives new agent sessions the memory of their workspace)"]
        words = {
            "add": "added" if done else "add",
            "replace": "updated" if done else "update",
            "remove": "removed" if done else "remove",
            "none": "nothing to remove" if plan.removing else "already installed",
            "skipped": "not on this machine",
        }
        for step in plan.hooks:
            text = f"  {step.agent:<12} {words.get(step.action, step.action):<20}"
            if step.error:
                text = f"  {step.agent:<12} {'failed':<20} {step.error}"
            elif step.action != "skipped":
                text += f" {step.settings}"
                if step.backup:
                    text += f"  (backup: {step.backup})"
            lines.append(text.rstrip())
    job = plan.schedule
    if job.kind != "none":
        lines += ["", "Nightly run"]
        words = {
            "add": "installed" if done else "install",
            "replace": "updated" if done else "update",
            "remove": "removed" if done else "remove",
            "none": "nothing to remove" if plan.removing else "already installed",
            "kept": "kept",
            "manual": "by hand",
        }
        head = f"  {words.get(job.action, job.action)}"
        if job.path:
            head += f"  {job.path}"
        lines.append(head)
        for one in job.detail.splitlines():
            lines.append(f"    {one}")
        if job.command and job.kind == "launchd":
            lines.append(f"    runs: {shlex.join(job.command)}")
        if job.error:
            lines.append(f"    failed: {job.error}")
    lines.append("")
    if not done and plan.pending:
        lines.append("Run it again with --apply to make these changes.")
    elif not done:
        lines.append("Nothing to change.")
    elif not plan.removing and not plan.has_store:
        lines.append(
            "Next: `session2memory auto run --dry-run` shows what a run would pick up; "
            "`session2memory auto run` does it."
        )
    else:
        lines.append("Done.")
    return "\n".join(lines)
