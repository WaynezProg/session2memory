"""Add or remove the memory hook in an agent's hook settings.

Claude Code, Codex and Muse read a Claude-style ``hooks`` object and pass what a
``SessionStart`` hook prints on to the model. Grok shows a session-start hook's output to
the person only, so there the hook is attached to ``PostToolUse`` and adds context after
the first tool call of a session that actually ran (what a hook says before a call is
dropped when the call is refused).

The change is limited to one hook entry that this module can recognise again, every write
is preceded by a timestamped backup next to the file, and nothing is written unless
``apply`` is set.
"""

from __future__ import annotations

import json
import os
import shlex
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
    # A second entry on this event that only watches: it notes a resumed session, which
    # the repeating event's payload does not say, so that session stays silent.
    observe_event: str | None = None
    # ``claude``: a ``hooks`` object of events holding matcher groups, answered with
    # ``hookSpecificOutput``. ``antigravity``: named hooks at the top level, each event a
    # flat list of handlers, answered with ``injectSteps``. ``cursor``: a versioned file
    # whose ``hooks`` object holds a flat list of handlers per event, answered with
    # ``additional_context``.
    layout: str = "claude"

    @property
    def output(self) -> str:
        return self.layout


AGENT_HOOKS: dict[str, AgentHook] = {
    "claude": AgentHook("~/.claude/settings.json", "SessionStart", HOOK_MATCHER),
    "codex": AgentHook("~/.codex/hooks.json", "SessionStart"),
    "muse": AgentHook("~/.config/muse/settings.json", "SessionStart"),
    "grok": AgentHook(
        "~/.grok/hooks/session2memory.json",
        "PostToolUse",
        once_per_session=True,
        session_env="GROK_SESSION_ID",
        observe_event="SessionStart",
    ),
    # Antigravity fires SessionStart once per new conversation; --once also keeps a
    # conversation that is opened again from being served twice.
    "antigravity": AgentHook(
        "~/.gemini/config/hooks.json", "SessionStart", once_per_session=True, layout="antigravity"
    ),
    # Cursor calls sessionStart when a new composer conversation is created and adds the
    # answer's ``additional_context`` to that conversation's initial context.
    "cursor": AgentHook("~/.cursor/hooks.json", "sessionStart", layout="cursor"),
}
ANTIGRAVITY_HOOK_NAME = "session2memory"
CURSOR_HOOKS_VERSION = 1


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
    command = f"{base} --event {spec.event} --once{_seen_option(seen_dir)}"
    if spec.session_env is None:
        return command
    # The event fires on every tool call: answer from the shell once the session has had
    # its injection (a filled marker; an empty one is a claim still being worked on),
    # without starting Python.
    directory = seen_dir.replace("~", "$HOME", 1) if seen_dir.startswith("~") else seen_dir
    marker = f'"{directory}/{agent}-${{{spec.session_env}:-}}"'
    return f"[ -s {marker} ] && exit 0; {command}"


def _seen_option(seen_dir: str) -> str:
    return "" if seen_dir == DEFAULT_SEEN_DIR else f" --seen-dir '{seen_dir}'"


def hook_entries(
    executable: Path, agent: str, seen_dir: str = DEFAULT_SEEN_DIR
) -> dict[str, dict[str, object]]:
    """The hook group to hold under each event for this agent."""
    spec = AGENT_HOOKS[agent]

    def group(command: str) -> dict[str, object]:
        body: dict[str, object] = {
            "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}]
        }
        return body if spec.matcher is None else {"matcher": spec.matcher, **body}

    entries = {spec.event: group(hook_command(executable, agent, seen_dir))}
    if spec.observe_event is not None:
        observer = (
            f"'{executable.as_posix()}' session-start-hook --agent {agent} "
            f"--event {spec.observe_event} --observe{_seen_option(seen_dir)}"
        )
        entries[spec.observe_event] = group(observer)
    return entries


def is_our_command(command: str) -> bool:
    """Whether ``command`` starts our hook: an executable (under whatever name it was
    installed), then ``session-start-hook --agent`` as its first arguments. Text that
    merely mentions those words (an ``echo``, a comment) is someone else's."""
    # Grok's form puts a shell test in front: ``[ -s marker ] && exit 0; <command>``.
    segment = command.rsplit(";", 1)[-1].strip()
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    return len(tokens) >= 3 and tokens[1:3] == HOOK_COMMAND_MARKER.split()


def _is_our_hook(hook: object) -> bool:
    return isinstance(hook, dict) and is_our_command(str(hook.get("command", "")))


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
    exe = executable or default_executable()
    command = hook_command(exe, agent, seen_dir)
    if AGENT_HOOKS[agent].layout == "antigravity":
        return _update_named_hook(
            settings_path, command, AGENT_HOOKS[agent].event, remove=remove, apply=apply, now=now
        )
    if AGENT_HOOKS[agent].layout == "cursor":
        return _update_cursor_hooks(
            settings_path, command, AGENT_HOOKS[agent].event, remove=remove, apply=apply, now=now
        )
    entries = hook_entries(exe, agent, seen_dir)
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
    # Every event is looked at, not only today's: an entry of ours left under an event an
    # earlier version used is taken out as well.
    updated: dict[str, list[object]] = {}
    found = unchanged = False
    for event in [*hooks, *(name for name in entries if name not in hooks)]:
        groups = hooks.get(event)
        if groups is None:
            groups = []
        if not isinstance(groups, list):
            if event in entries:
                raise ValueError(f"{settings_path.as_posix()}: 'hooks.{event}' is not a list")
            continue
        others, had = _without_ours(groups)
        found = found or had
        entry = entries.get(event)
        if remove or entry is None:
            updated[event] = others
        else:
            updated[event] = [*others, entry]
    unchanged = all(updated[event] == (hooks.get(event) or []) for event in updated)
    if remove:
        action = "remove" if found else "none"
    elif unchanged:
        action = "none"
    else:
        action = "replace" if found else "add"
    if action == "none" or not apply:
        return InstallPlan(settings_path, action, command)

    backup: Path | None = None
    if settings_path.exists():
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup = settings_path.with_name(f"{settings_path.name}.bak-session2memory-{stamp}")
        shutil.copy2(settings_path, backup)
    for event, groups in updated.items():
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    _write_settings(settings_path, settings)
    return InstallPlan(settings_path, action, command, backup)


def _backup(settings_path: Path, now: datetime | None) -> Path | None:
    if not settings_path.exists():
        return None
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = settings_path.with_name(f"{settings_path.name}.bak-session2memory-{stamp}")
    shutil.copy2(settings_path, backup)
    return backup


def _named_entry_is_ours(entry: object) -> bool:
    if not isinstance(entry, dict) or not entry:
        return False
    handlers = [one for group in entry.values() if isinstance(group, list) for one in group]
    return bool(handlers) and all(_is_our_hook(one) for one in handlers)


def _update_named_hook(
    settings_path: Path,
    command: str,
    event: str,
    *,
    remove: bool,
    apply: bool,
    now: datetime | None,
) -> InstallPlan:
    """Antigravity's ``hooks.json``: our hook is one named entry beside the others."""
    settings: dict[str, object] = {}
    if settings_path.exists():
        loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{settings_path.as_posix()}: expected a JSON object")
        settings = loaded
    current = settings.get(ANTIGRAVITY_HOOK_NAME)
    if current is not None and not _named_entry_is_ours(current):
        # The name alone does not make it ours: never replace or remove it.
        raise ValueError(
            f"{settings_path.as_posix()}: the entry named {ANTIGRAVITY_HOOK_NAME} was not "
            "written by this tool; it is left as it is"
        )
    wanted = {event: [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}]}
    if remove:
        action = "remove" if current is not None else "none"
    elif current == wanted:
        action = "none"
    else:
        action = "replace" if current is not None else "add"
    if action == "none" or not apply:
        return InstallPlan(settings_path, action, command)
    backup = _backup(settings_path, now)
    if remove:
        settings.pop(ANTIGRAVITY_HOOK_NAME, None)
    else:
        settings[ANTIGRAVITY_HOOK_NAME] = wanted
    _write_settings(settings_path, settings)
    return InstallPlan(settings_path, action, command, backup)


def _update_cursor_hooks(
    settings_path: Path,
    command: str,
    event: str,
    *,
    remove: bool,
    apply: bool,
    now: datetime | None,
) -> InstallPlan:
    """Cursor's ``hooks.json``: ``{"version": 1, "hooks": {event: [handler, ...]}}``."""
    settings: dict[str, object] = {}
    if settings_path.exists():
        loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{settings_path.as_posix()}: expected a JSON object")
        settings = loaded
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{settings_path.as_posix()}: expected hooks to be an object")
    handlers = hooks.get(event, [])
    if not isinstance(handlers, list):
        raise ValueError(f"{settings_path.as_posix()}: expected hooks.{event} to be a list")
    wanted = {"command": command, "timeout": HOOK_TIMEOUT_SECONDS}
    ours = [handler for handler in handlers if _is_our_hook(handler)]
    others = [handler for handler in handlers if not _is_our_hook(handler)]
    if remove:
        action = "remove" if ours else "none"
    elif ours == [wanted]:
        action = "none"
    else:
        action = "replace" if ours else "add"
    if action == "none" or not apply:
        return InstallPlan(settings_path, action, command)
    backup = _backup(settings_path, now)
    kept = others if remove else [*others, wanted]
    hooks = dict(hooks)
    if kept:
        hooks[event] = kept
    else:
        hooks.pop(event, None)
    settings.setdefault("version", CURSOR_HOOKS_VERSION)
    settings["hooks"] = hooks
    _write_settings(settings_path, settings)
    return InstallPlan(settings_path, action, command, backup)


def _write_settings(settings_path: Path, settings: dict[str, object]) -> None:
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
