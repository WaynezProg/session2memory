"""Readers the source registry uses to re-open a session from one of its locations."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from session2memory.adapters.antigravity import AntigravityAdapter
from session2memory.adapters.claude import ClaudeAdapter, ClaudeDesktopAdapter
from session2memory.adapters.codex import CodexAdapter
from session2memory.adapters.cursor import CursorAdapter, CursorCliAdapter
from session2memory.adapters.grok import GrokAdapter
from session2memory.adapters.muse import MuseAdapter
from session2memory.adapters.openclaw_native import OpenClawNativeAdapter
from session2memory.state.sources import SessionReader


def default_readers() -> Mapping[str, SessionReader]:
    """One reader per registry-tracked tool. Readers take the location path explicitly, so
    the root given here is irrelevant."""
    root = Path()
    return {
        "codex": CodexAdapter(root).read_session,
        "claude": ClaudeAdapter(root).read_session,
        "claude-desktop": ClaudeDesktopAdapter(root).read_session,
        "openclaw-native": OpenClawNativeAdapter(root).read_session,
        "grok": GrokAdapter(root).read_session,
        "muse": MuseAdapter(root).read_session,
        "antigravity": AntigravityAdapter(root).read_session,
        "cursor": CursorAdapter(root).read_session,
        "cursor-cli": CursorCliAdapter(root).read_session,
    }
