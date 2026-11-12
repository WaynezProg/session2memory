"""Credential scrubbing shared by everything that stores or sends session text."""

from __future__ import annotations

import re

_SECRETS = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abprs]-[A-Za-z0-9-]{10,})\b"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{20,}=*"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def scrub_secrets(text: str) -> str:
    """Whatever the model was told, a credential must never land in stored memory."""
    return _SECRETS.sub("[REDACTED]", text)
