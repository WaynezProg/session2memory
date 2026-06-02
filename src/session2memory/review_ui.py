from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from session2memory.review import (
    ReviewNotFoundError,
    approve_review,
    inspect_review,
    list_reviews,
    promote_reviews,
    reject_review,
)


@dataclass(frozen=True)
class ReviewAppConfig:
    output_dir: Path
    date: str


def build_review_app(config: ReviewAppConfig) -> Any:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, RichLog, Static

    class ReviewApp(App[None]):
        BINDINGS = [
            Binding("a", "approve", "Approve"),
            Binding("r", "reject", "Reject"),
            Binding("p", "promote", "Promote"),
            Binding("q", "quit", "Quit"),
            Binding("j", "cursor_down", "Down"),
            Binding("k", "cursor_up", "Up"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self._config = config
            self._rows = list_reviews(output_dir=config.output_dir, date=config.date)
            self._index = 0

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                with Vertical(id="sidebar"):
                    yield Static("Reviews", id="title")
                    yield RichLog(id="list", highlight=True, markup=False)
                with Vertical(id="detail"):
                    yield Static("Detail", id="detail-title")
                    yield RichLog(id="preview", highlight=True, markup=False)
            yield Footer()

        def on_mount(self) -> None:
            self._render_list()
            self._render_detail()

        def _render_list(self) -> None:
            log = self.query_one("#list", RichLog)
            log.clear()
            if not self._rows:
                log.write("No review rows.")
                return
            for offset, row in enumerate(self._rows):
                marker = ">" if offset == self._index else " "
                extraction = row.get("extraction", "marker")
                confidence = row.get("confidence")
                conf = f" conf={confidence}" if confidence is not None else ""
                log.write(
                    f"{marker} {row.get('id', '')} [{row.get('status', '')}] "
                    f"{extraction}{conf} {row.get('kind', '')}: {row.get('text', '')}"
                )

        def _render_detail(self) -> None:
            preview = self.query_one("#preview", RichLog)
            preview.clear()
            if not self._rows:
                return
            row = self._rows[self._index]
            review_id = str(row.get("id", ""))
            try:
                inspection = inspect_review(
                    output_dir=self._config.output_dir,
                    date=self._config.date,
                    review_id=review_id,
                )
            except ReviewNotFoundError:
                preview.write("Review row missing.")
                return
            preview.write(str(inspection.row.get("text", "")))
            preview.write("")
            preview.write(inspection.preview)

        def action_cursor_down(self) -> None:
            if self._index < len(self._rows) - 1:
                self._index += 1
                self._render_list()
                self._render_detail()

        def action_cursor_up(self) -> None:
            if self._index > 0:
                self._index -= 1
                self._render_list()
                self._render_detail()

        def _selected_id(self) -> str:
            return str(self._rows[self._index].get("id", ""))

        def action_approve(self) -> None:
            if not self._rows:
                return
            approve_review(
                output_dir=self._config.output_dir,
                date=self._config.date,
                review_id=self._selected_id(),
            )
            self._reload_rows()

        def action_reject(self) -> None:
            if not self._rows:
                return
            reject_review(
                output_dir=self._config.output_dir,
                date=self._config.date,
                review_id=self._selected_id(),
            )
            self._reload_rows()

        def action_promote(self) -> None:
            promote_reviews(output_dir=self._config.output_dir, date=self._config.date)
            self._reload_rows()

        def _reload_rows(self) -> None:
            self._rows = list_reviews(output_dir=self._config.output_dir, date=self._config.date)
            if self._index >= len(self._rows):
                self._index = max(0, len(self._rows) - 1)
            self._render_list()
            self._render_detail()

    return ReviewApp()


def run_review_ui(config: ReviewAppConfig) -> None:
    build_review_app(config).run()
