from __future__ import annotations

from session2memory.models import SessionMessage

NOISE_PREFIXES = (
    "You are Codex",
    "You are Claude",
    "# AGENTS.md instructions",
    "# CLAUDE.md",
    "# GEMINI.md",
)

TELEMETRY_MARKERS = (
    "ui_telemetry",
    "token_count",
    "cached_content_token_count",
)

SIGNAL_MARKERS = (
    "決定",
    "驗證",
    "坑",
    "完成",
    "failed",
    "passed",
)


def is_noise(message: SessionMessage) -> bool:
    text = message.text.strip()
    if message.role == "system":
        return True
    if any(text.startswith(prefix) for prefix in NOISE_PREFIXES):
        return True
    if any(marker in text for marker in TELEMETRY_MARKERS):
        return True
    if message.role == "tool" and text.count("\n") > 300:
        return True
    if text.startswith("Traceback (most recent call last)") and not _contains_signal(text):
        return True
    return False


def _contains_signal(text: str) -> bool:
    return any(marker in text for marker in SIGNAL_MARKERS)
