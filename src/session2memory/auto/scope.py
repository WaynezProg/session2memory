"""Who may read what: OpenClaw agents only see what OpenClaw agents produced."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

Reader = Literal["coding", "openclaw"]
READER_ENV = "SESSION2MEMORY_READER"


def openclaw_roots(state_dir: Path) -> tuple[Path, ...]:
    """Directories whose sessions belong to OpenClaw agents, given OpenClaw's state dir."""
    return (state_dir.parent / "workspaces", state_dir / "agents")


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
