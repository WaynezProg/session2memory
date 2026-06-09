from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory.cli import app
from session2memory.models import EvidencePointer, MemoryCandidate
from session2memory.state.store import StateStore
from session2memory.worklog import (
    WorklogRange,
    generate_worklog,
    render_worklog_markdown,
    resolve_worklog_range,
)


def _evidence(*, session_id: str = "s1", start: int = 2, end: int = 2) -> EvidencePointer:
    return EvidencePointer(
        tool="codex",
        session_id=session_id,
        source_path=Path("/tmp/s.jsonl"),
        message_start=start,
        message_end=end,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:abc",
    )


def _candidate(*, kind: str, text: str, session_id: str = "s1") -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,  # type: ignore[arg-type]
        text=text,
        workspace_id="repo-123",
        evidence=_evidence(session_id=session_id),
        durable=False,
    )


def read_golden(name: str) -> str:
    return (Path(__file__).parent / "golden" / name).read_text(encoding="utf-8")


def _seed_store(tmp_path: Path) -> tuple[StateStore, Path]:
    output_dir = tmp_path / "session-memory"
    output_dir.mkdir()
    store = StateStore.open(output_dir / "session2memory.db", output_dir=output_dir)
    store.upsert_candidate(
        import_date="2026-06-01",
        candidate=_candidate(kind="completed", text="Shipped auth fix.", session_id="s-a"),
    )
    store.upsert_candidate(
        import_date="2026-06-02",
        candidate=_candidate(kind="decision", text="Use SQLite for state.", session_id="s-b"),
    )
    store.upsert_candidate(
        import_date="2026-06-07",
        candidate=_candidate(kind="verification", text="pytest green.", session_id="s-c"),
    )
    store.upsert_candidate(
        import_date="2026-06-08",
        candidate=_candidate(
            kind="pitfall",
            text="Re-import clears daily markdown.",
            session_id="s-d",
        ),
    )
    return store, output_dir


def test_resolve_yesterday() -> None:
    worklog_range = resolve_worklog_range(
        period="yesterday",
        date_from=None,
        date_to=None,
        reference_date=date(2026, 6, 9),
    )
    assert worklog_range.label == "yesterday"
    assert worklog_range.date_from == date(2026, 6, 8)
    assert worklog_range.date_to == date(2026, 6, 8)
    assert worklog_range.output_name == "2026-06-08.md"


def test_resolve_last_week() -> None:
    worklog_range = resolve_worklog_range(
        period="last-week",
        date_from=None,
        date_to=None,
        reference_date=date(2026, 6, 9),
    )
    assert worklog_range.date_from == date(2026, 6, 1)
    assert worklog_range.date_to == date(2026, 6, 7)
    assert worklog_range.output_name == "2026-06-01_2026-06-07.md"


def test_resolve_last_month() -> None:
    worklog_range = resolve_worklog_range(
        period="last-month",
        date_from=None,
        date_to=None,
        reference_date=date(2026, 6, 9),
    )
    assert worklog_range.date_from == date(2026, 5, 1)
    assert worklog_range.date_to == date(2026, 5, 31)
    assert worklog_range.output_name == "2026-05-01_2026-05-31.md"


def test_resolve_custom_range() -> None:
    worklog_range = resolve_worklog_range(
        period=None,
        date_from="2026-06-01",
        date_to="2026-06-07",
    )
    assert worklog_range.output_name == "2026-06-01_2026-06-07.md"


def test_resolve_rejects_period_and_custom_range() -> None:
    with pytest.raises(ValueError, match="not both"):
        resolve_worklog_range(
            period="yesterday",
            date_from="2026-06-01",
            date_to="2026-06-07",
        )


def test_generate_worklog_reads_db_not_daily_markdown(tmp_path: Path) -> None:
    store, output_dir = _seed_store(tmp_path)
    try:
        result = generate_worklog(
            output_dir=output_dir,
            state_store=store,
            worklog_range=WorklogRange(
                label="2026-06-01..2026-06-07",
                date_from=date(2026, 6, 1),
                date_to=date(2026, 6, 7),
                output_name="2026-06-01_2026-06-07.md",
            ),
        )
    finally:
        store.close()

    markdown = result.path.read_text(encoding="utf-8")
    assert result.entry_count == 3
    assert "hks_type: session_worklog" in markdown
    assert "## Shipped" in markdown
    assert "Shipped auth fix." in markdown
    assert "## Verified" in markdown
    assert "pytest green." in markdown
    assert "## Decisions" in markdown
    assert "Use SQLite for state." in markdown
    assert "## Pitfalls" in markdown
    assert "_No entries._" in markdown
    assert "Re-import clears daily markdown." not in markdown
    assert "evidence_id=" in markdown
    assert "tool=codex" in markdown
    assert "session_id=" in markdown
    assert "lines=2-2" in markdown


def test_worklog_survives_managed_output_replacement(tmp_path: Path) -> None:
    store, output_dir = _seed_store(tmp_path)
    try:
        generate_worklog(
            output_dir=output_dir,
            state_store=store,
            worklog_range=WorklogRange(
                label="yesterday",
                date_from=date(2026, 6, 8),
                date_to=date(2026, 6, 8),
                output_name="2026-06-08.md",
            ),
        )
        (output_dir / "daily").mkdir(exist_ok=True)
        daily_path = output_dir / "daily" / "2026-06-08.md"
        daily_path.write_text("stale daily markdown\n", encoding="utf-8")
        result = generate_worklog(
            output_dir=output_dir,
            state_store=store,
            worklog_range=WorklogRange(
                label="yesterday",
                date_from=date(2026, 6, 8),
                date_to=date(2026, 6, 8),
                output_name="2026-06-08.md",
            ),
        )
    finally:
        store.close()

    markdown = result.path.read_text(encoding="utf-8")
    assert "Re-import clears daily markdown." in markdown
    assert "stale daily markdown" not in markdown


def test_render_worklog_markdown_sections(tmp_path: Path) -> None:
    store = StateStore.open(tmp_path / "session2memory.db")
    stored = store.upsert_candidate(
        import_date="2026-06-01",
        candidate=_candidate(kind="constraint", text="No raw transcripts in HKS."),
    )
    store.close()
    markdown = render_worklog_markdown(
        worklog_range=WorklogRange(
            label="custom",
            date_from=date(2026, 6, 1),
            date_to=date(2026, 6, 1),
            output_name="custom.md",
        ),
        rows=[("2026-06-01", stored)],
    )
    assert "## Constraints" in markdown
    assert "No raw transcripts in HKS." in markdown


def test_cli_worklog_custom_range(tmp_path: Path) -> None:
    store, output_dir = _seed_store(tmp_path)
    store.close()
    result = CliRunner().invoke(
        app,
        [
            "worklog",
            "--from",
            "2026-06-01",
            "--to",
            "2026-06-07",
            "--output",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "entries=3" in result.output
    assert (output_dir / "worklogs" / "2026-06-01_2026-06-07.md").is_file()


def test_worklog_matches_golden(tmp_path: Path) -> None:
    store, output_dir = _seed_store(tmp_path)
    try:
        result = generate_worklog(
            output_dir=output_dir,
            state_store=store,
            worklog_range=WorklogRange(
                label="2026-06-01..2026-06-07",
                date_from=date(2026, 6, 1),
                date_to=date(2026, 6, 7),
                output_name="2026-06-01_2026-06-07.md",
            ),
        )
    finally:
        store.close()
    golden = read_golden("worklog_2026-06-01_2026-06-07.md")
    assert result.path.read_text(encoding="utf-8") == golden


def test_cli_worklog_yesterday(tmp_path: Path) -> None:
    store, output_dir = _seed_store(tmp_path)
    store.close()
    result = CliRunner().invoke(
        app,
        [
            "worklog",
            "yesterday",
            "--output",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (output_dir / "worklogs" / "2026-06-08.md").is_file()

