"""Who may read what: OpenClaw agents only see what OpenClaw agents produced."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

Reader = Literal["coding", "openclaw"]
READER_ENV = "SESSION2MEMORY_READER"


def openclaw_roots(state_dir: Path) -> tuple[Path, ...]:
    """Directories whose sessions belong to OpenClaw agents, given OpenClaw's state dir."""
    return (state_dir.parent / "workspaces", state_dir / "agents")


def within(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve(strict=False).as_posix()
    for root in roots:
        base = root.resolve(strict=False).as_posix()
        if resolved == base or resolved.startswith(base + "/"):
            return True
    return False


def forced_reader(env: Mapping[str, str], cwd: Path, state_dir: Path) -> Reader | None:
    """``openclaw`` when the calling process is an OpenClaw agent, whatever it asks for.

    An agent is recognised by an explicit ``SESSION2MEMORY_READER=openclaw``, by working
    inside an OpenClaw workspace, or by running with a ``CODEX_HOME`` that OpenClaw owns.
    """
    roots = openclaw_roots(state_dir)
    if env.get(READER_ENV) == "openclaw" or within(cwd, roots):
        return "openclaw"
    codex_home = env.get("CODEX_HOME")
    if codex_home and within(Path(codex_home), roots):
        return "openclaw"
    return None
