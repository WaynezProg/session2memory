from __future__ import annotations

from session2memory.models import SessionMessage

NOISE_PREFIXES = (
    "You are Codex",
    "You are Claude",
)

INSTRUCTION_MARKERS = (
    "agents.md instructions",
    "claude.md instructions",
    "gemini.md instructions",
    "<instructions>",
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
    normalized_text = text.casefold()
    if message.role == "system":
        return True
    if any(text.startswith(prefix) for prefix in NOISE_PREFIXES):
        return True
    if any(marker in normalized_text for marker in INSTRUCTION_MARKERS):
        return True
    if any(marker in text for marker in TELEMETRY_MARKERS):
        return True
    if message.role == "tool" and (text.count("\n") > 300 or len(text) > 20_000):
        return True
    if text.startswith("Traceback (most recent call last)") and not _contains_signal(text):
        return True
    return False


def _contains_signal(text: str) -> bool:
    return any(marker in text for marker in SIGNAL_MARKERS)
