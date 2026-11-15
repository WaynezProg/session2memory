"""Who may read what: OpenClaw agents only see what OpenClaw agents produced."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

Reader = Literal["coding", "openclaw"]
READER_ENV = "SESSION2MEMORY_READER"


def openclaw_roots(state_dir: Path) -> tuple[Path, ...]:
    """Directories whose sessions belong to OpenClaw agents, given OpenClaw's state dir.

    These are the agents' own state, OpenClaw's built-in workspace locations
    (``workspace`` and ``workspace-<profile>`` inside the state dir), every workspace
    ``openclaw.json`` assigns an agent, and, for the split layout that keeps
    ``<root>/state`` beside ``<root>/workspaces``, that whole ``workspaces`` folder (it
    also holds the shared ``projects`` links no agent is assigned). The split layout is
    taken only when it is evident: the state dir is a folder named ``state`` that is not
    directly in the home directory, or the config puts a workspace in that folder. With
    the default ``~/.openclaw`` the sibling would be ``~/workspaces``, where coding
    projects live.
    """
    configured = _configured_workspaces(state_dir)
    roots = [state_dir / "agents", state_dir / "workspace", *_profile_workspaces(state_dir)]
    roots += configured
    sibling = state_dir.parent / "workspaces"
    split = state_dir.name == "state" and _absolute(state_dir.parent) != _absolute(Path.home())
    if split or any(within(one, (sibling,)) for one in configured):
        roots.append(sibling)
    return tuple(dict.fromkeys(roots))


def _absolute(path: Path) -> str:
    return Path(os.path.abspath(path.expanduser())).as_posix()


def _profile_workspaces(state_dir: Path) -> list[Path]:
    try:
        return sorted(one for one in state_dir.glob("workspace-*") if one.is_dir())
    except OSError:
        return []


def _configured_workspaces(state_dir: Path) -> list[Path]:
    """Workspaces ``openclaw.json`` names: the agents' default and each agent's own.

    A config that cannot be read as JSON (OpenClaw also accepts JSON5) adds nothing.
    """
    try:
        loaded = json.loads((state_dir / "openclaw.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return []
    agents = loaded.get("agents") if isinstance(loaded, dict) else None
    if not isinstance(agents, dict):
        return []
    sections: list[object] = [agents.get("defaults")]
    for key in ("entries", "list"):
        value = agents.get(key)
        if isinstance(value, dict):
            sections += list(value.values())
        elif isinstance(value, list):
            sections += value
    found: list[Path] = []
    for section in sections:
        raw = section.get("workspace") if isinstance(section, dict) else None
        if isinstance(raw, str) and raw.strip():
            path = Path(raw.strip()).expanduser()
            if path.is_absolute():
                found.append(path)
    return found


def within(path: Path, roots: tuple[Path, ...]) -> bool:
    """Whether ``path`` is under one of ``roots``, as written or after following links.

    A directory reached through a link placed inside a root (OpenClaw's shared
    ``projects/<repo>`` links) still counts: resolving it alone would lose that.
    """
    spellings = {path.resolve(strict=False).as_posix(), Path(os.path.abspath(path)).as_posix()}
    for root in roots:
        for base in {root.resolve(strict=False).as_posix(), Path(os.path.abspath(root)).as_posix()}:
            if any(one == base or one.startswith(base + "/") for one in spellings):
                return True
    return False


def forced_reader(env: Mapping[str, str], cwd: Path, state_dir: Path) -> Reader | None:
    """``openclaw`` when the calling process is an OpenClaw agent, whatever it asks for.

    An agent is recognised by an explicit ``SESSION2MEMORY_READER=openclaw``, by the
    ``OPENCLAW_SHELL`` marker of OpenClaw's exec tool, by working inside an OpenClaw
    workspace (also when it got there through a link: the shell's ``PWD`` keeps the path
    as typed), or by running with a ``CODEX_HOME`` that OpenClaw owns.
    """
    roots = openclaw_roots(state_dir)
    if env.get(READER_ENV) == "openclaw" or env.get("OPENCLAW_SHELL") or within(cwd, roots):
        return "openclaw"
    for name in ("PWD", "CODEX_HOME"):
        value = env.get(name)
        if value and within(Path(value), roots):
            return "openclaw"
    return None
