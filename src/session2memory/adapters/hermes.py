from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from session2memory.adapters.text_log import iter_text_log_sessions
from session2memory.models import SessionRecord


class HermesAdapter:
    tool = "hermes"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skipped: list[str] = []

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        self.skipped.clear()
        yield from iter_text_log_sessions(
            root=self.root,
            tool=self.tool,
            date=date,
            skipped=self.skipped,
        )
