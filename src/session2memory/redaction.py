from __future__ import annotations

import re
from pathlib import Path

_HOME_PREFIX_RE = re.compile(r"(?<![\w./~])/~")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w./])/[A-Za-z0-9._/-]+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]+")
_TOKEN_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b")
_ENV_ASSIGN_RE = re.compile(
    r"(?m)^([A-Z0-9_]+)=(['\"]?)([^'\"\n]{3,})\2\s*$"
)


def redact_text(text: str, *, home: Path | None = None) -> str:
    redacted = text
    if home is not None:
        home_text = home.expanduser().as_posix().rstrip("/")
        if home_text:
            redacted = redacted.replace(home_text, "[REDACTED:home]")
    redacted = _HOME_PREFIX_RE.sub("[REDACTED:home]", redacted)
    redacted = _ABSOLUTE_PATH_RE.sub("[REDACTED:path]", redacted)
    redacted = _WINDOWS_PATH_RE.sub("[REDACTED:path]", redacted)
    redacted = _TOKEN_RE.sub("[REDACTED:token]", redacted)
    redacted = _ENV_ASSIGN_RE.sub(r"\1=[REDACTED:env]", redacted)
    return redacted
