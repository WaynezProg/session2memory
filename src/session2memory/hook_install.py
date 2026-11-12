"""Add or remove the SessionStart hook in a Claude Code ``settings.json``.

The change is limited to one hook entry that this module can recognise again, every write
is preceded by a timestamped backup next to the file, and nothing is written unless
``apply`` is set.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HOOK_COMMAND_MARKER = "session-start-hook --agent"
HOOK_MATCHER = "startup|clear"
HOOK_TIMEOUT_SECONDS = 5


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


def hook_command(executable: Path, agent: str) -> str:
    return f"'{executable.as_posix()}' session-start-hook --agent {agent}"


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
    remove: bool = False,
    apply: bool = False,
    now: datetime | None = None,
) -> InstallPlan:
    """Plan (and with ``apply`` perform) the change. Other hooks are left exactly as they are."""
    command = hook_command(executable or default_executable(), agent)
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
    groups = hooks.get("SessionStart")
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        raise ValueError(f"{settings_path.as_posix()}: 'hooks.SessionStart' is not a list")
    entry = {
        "matcher": HOOK_MATCHER,
        "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}],
    }
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
        hooks["SessionStart"] = updated
    else:
        hooks.pop("SessionStart", None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings_path.with_name(settings_path.name + ".session2memory-tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", "utf-8")
    temporary.replace(settings_path)
    return InstallPlan(settings_path, action, command, backup)
