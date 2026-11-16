"""Settings every invocation shares, read from one file.

The search settings (which embedding model, which services) have to be the same for the
nightly job that makes the vectors and for every agent that searches them, and those run
in different environments: launchd, an agent's shell, the console app. Rather than set the
variables in each, they are written once in ``~/.config/session2memory/env`` and read here
at start. A variable already set in the environment wins.

The file may hold a key, so it is read only when nobody else can: a file readable by group
or others is ignored, with a note.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import MutableMapping
from pathlib import Path

ENV_FILE_ENV = "SESSION2MEMORY_ENV_FILE"
DEFAULT_ENV_FILE = "~/.config/session2memory/env"
PREFIX = "SESSION2MEMORY_"

_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def env_file(environ: MutableMapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    return Path(source.get(ENV_FILE_ENV) or DEFAULT_ENV_FILE).expanduser()


def parse_env(text: str) -> dict[str, str]:
    """``NAME=value`` lines (``export`` and quotes allowed) for this program's variables."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if match is None or not match.group(1).startswith(PREFIX):
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def load_env_file(environ: MutableMapping[str, str] | None = None) -> str | None:
    """Add the file's variables to ``environ``; returns a note when the file was not used."""
    target = os.environ if environ is None else environ
    path = env_file(target)
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return f"{path} is readable by others and was ignored (chmod 600 it)"
    try:
        values = parse_env(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return f"{path} could not be read ({type(exc).__name__})"
    for name, value in values.items():
        if name != ENV_FILE_ENV:
            target.setdefault(name, value)
    return None


__all__ = ["DEFAULT_ENV_FILE", "ENV_FILE_ENV", "env_file", "load_env_file", "parse_env"]
