"""Add or remove the memory hook in an agent's hook settings.

Claude Code, Codex and Muse read a Claude-style ``hooks`` object and pass what a
``SessionStart`` hook prints on to the model. Grok shows a session-start hook's output to
the person only, so there the hook is attached to ``PreToolUse`` and adds context on the
first tool call of a session.

The change is limited to one hook entry that this module can recognise again, every write
is preceded by a timestamped backup next to the file, and nothing is written unless
``apply`` is set.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HOOK_COMMAND_MARKER = "session-start-hook --agent"
HOOK_MATCHER = "startup|clear"
HOOK_TIMEOUT_SECONDS = 5
DEFAULT_SEEN_DIR = "~/.local/share/session2memory/hook-seen"


@dataclass(frozen=True)
class AgentHook:
    """Where and how one agent takes the hook."""

    settings: str  # default settings file
    event: str
    matcher: str | None = None
    once_per_session: bool = False  # the event repeats; inject on its first firing only
    session_env: str | None = None  # environment variable holding the session id


AGENT_HOOKS: dict[str, AgentHook] = {
    "claude": AgentHook("~/.claude/settings.json", "SessionStart", HOOK_MATCHER),
    "codex": AgentHook("~/.codex/hooks.json", "SessionStart"),
    "muse": AgentHook("~/.config/muse/settings.json", "SessionStart"),
    "grok": AgentHook(
        "~/.grok/hooks/session2memory.json",
        "PreToolUse",
        once_per_session=True,
        session_env="GROK_SESSION_ID",
    ),
}


@dataclass(frozen=True)
class InstallPlan:
    settings_path: Path
    action: str  # "add", "replace", "remove" or "none"
    command: str
    backup_path: Path | None = None


def default_executable() -> Path:
    """The ``session2memory`` entry point beside the running interpreter."""
    # normpath, not resolve(): the interpreter is a symlink out of the virtualenv.
    return Path(os.path.normpath(Path(sys.executable).absolute().parent / "session2memory"))


def hook_command(executable: Path, agent: str, seen_dir: str = DEFAULT_SEEN_DIR) -> str:
    base = f"'{executable.as_posix()}' session-start-hook --agent {agent}"
    spec = AGENT_HOOKS[agent]
    if not spec.once_per_session:
        return base
    command = f"{base} --event {spec.event} --once"
    if seen_dir != DEFAULT_SEEN_DIR:
        command += f" --seen-dir '{seen_dir}'"
    if spec.session_env is None:
        return command
    # The event fires on every tool call: answer from the shell once the session has had
    # its injection (a filled marker; an empty one is a claim still being worked on),
    # without starting Python.
    directory = seen_dir.replace("~", "$HOME", 1) if seen_dir.startswith("~") else seen_dir
    marker = f'"{directory}/{agent}-${{{spec.session_env}:-}}"'
    return f"[ -s {marker} ] && exit 0; {command}"


def _is_our_hook(hook: object) -> bool:
    if not isinstance(hook, dict):
        return False
    command = str(hook.get("command", ""))
    return HOOK_COMMAND_MARKER in command and "session2memory" in command


def _without_ours(groups: list[object]) -> tuple[list[object], bool]:
    """Every group with our hook taken out; a group that only held ours disappears."""
    kept: list[object] = []
    found = False
    for group in groups:
        if not isinstance(group, dict):
            kept.append(group)
            continue
        hooks = group.get("hooks")
        if not isinstance(hooks, list) or not any(_is_our_hook(hook) for hook in hooks):
            kept.append(group)
            continue
        found = True
        others = [hook for hook in hooks if not _is_our_hook(hook)]
        if others:
            kept.append({**group, "hooks": others})
    return kept, found


def update_settings(
    settings_path: Path,
    *,
    agent: str = "claude",
    executable: Path | None = None,
    seen_dir: str = DEFAULT_SEEN_DIR,
    remove: bool = False,
    apply: bool = False,
    now: datetime | None = None,
) -> InstallPlan:
    """Plan (and with ``apply`` perform) the change. Other hooks are left exactly as they are."""
    if agent not in AGENT_HOOKS:
        raise ValueError(f"unknown agent {agent!r}; expected one of {', '.join(AGENT_HOOKS)}")
    spec = AGENT_HOOKS[agent]
    event = spec.event
    command = hook_command(executable or default_executable(), agent, seen_dir)
    settings: dict[str, object] = {}
    if settings_path.exists():
        loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{settings_path.as_posix()}: expected a JSON object")
        settings = loaded
    hooks = settings.get("hooks")
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, dict):
        raise ValueError(f"{settings_path.as_posix()}: 'hooks' is not an object")
    groups = hooks.get(event)
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        raise ValueError(f"{settings_path.as_posix()}: 'hooks.{event}' is not a list")
    entry: dict[str, object] = {
        "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}],
    }
    if spec.matcher is not None:
        entry = {"matcher": spec.matcher, **entry}
    others, found = _without_ours(groups)
    if remove:
        action = "remove" if found else "none"
        updated = others
    elif entry in groups and [*others, entry] == groups:
        action, updated = "none", groups
    else:
        action = "replace" if found else "add"
        updated = [*others, entry]
    if action == "none" or not apply:
        return InstallPlan(settings_path, action, command)

    backup: Path | None = None
    if settings_path.exists():
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup = settings_path.with_name(f"{settings_path.name}.bak-session2memory-{stamp}")
        shutil.copy2(settings_path, backup)
    if updated:
        hooks[event] = updated
    else:
        hooks.pop(event, None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    # Settings can hold credentials: the rewritten file keeps the original's mode and is
    # never readable by others on the way.
    mode = stat.S_IMODE(settings_path.stat().st_mode) if settings_path.exists() else 0o600
    temporary = settings_path.with_name(settings_path.name + ".session2memory-tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, mode)
    temporary.replace(settings_path)
    return InstallPlan(settings_path, action, command, backup)
