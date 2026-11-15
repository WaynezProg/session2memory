"""Credential scrubbing shared by everything that stores or sends session text."""

from __future__ import annotations

import re

# ASCII-only boundaries, not \b: Python counts CJK characters as word characters,
# so "用ghp_...推" has no \b around the token and it would slip through.
_SECRETS = re.compile(
    r"(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abprs]-[A-Za-z0-9-]{10,})(?![A-Za-z0-9_])"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{20,}=*"
    # The whole key block: up to its END marker, or to the end when the text was cut.
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    flags=re.DOTALL,
)


def scrub_secrets(text: str, *, placeholder: str = "[REDACTED]") -> str:
    """Whatever the model was told, a credential must never land in stored memory."""
    return _SECRETS.sub(placeholder, text)
