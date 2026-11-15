from __future__ import annotations

import re
from pathlib import Path

from session2memory.auto.secrets import scrub_secrets

_HOME_PREFIX_RE = re.compile(r"(?<![\w./~])/~")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w./])/[A-Za-z0-9._/-]+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]+")
_ENV_ASSIGN_RE = re.compile(
    r"(?m)^([A-Z0-9_]+)=(['\"]?)([^'\"\n]{3,})\2\s*$"
)


def redact_text(text: str, *, home: Path | None = None) -> str:
    # Credentials first, with the same patterns as stored memory, so path and env rules
    # below cannot split a token (a bearer value or key block) and leave part of it behind.
    redacted = scrub_secrets(text, placeholder="[REDACTED:token]")
    if home is not None:
        home_text = home.expanduser().as_posix().rstrip("/")
        if home_text:
            redacted = redacted.replace(home_text, "[REDACTED:home]")
    redacted = _HOME_PREFIX_RE.sub("[REDACTED:home]", redacted)
    redacted = _ABSOLUTE_PATH_RE.sub("[REDACTED:path]", redacted)
    redacted = _WINDOWS_PATH_RE.sub("[REDACTED:path]", redacted)
    redacted = _ENV_ASSIGN_RE.sub(r"\1=[REDACTED:env]", redacted)
    return redacted
