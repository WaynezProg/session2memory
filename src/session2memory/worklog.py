from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from session2memory.state.store import StateStore, StoredCandidate

WORKLOG_KIND_SECTIONS: tuple[tuple[str, str], ...] = (
    ("completed", "Shipped"),
    ("verification", "Verified"),
    ("decision", "Decisions"),
    ("constraint", "Constraints"),
    ("pitfall", "Pitfalls"),
    ("daily", "Notes"),
)


@dataclass(frozen=True)
class WorklogRange:
    label: str
    date_from: date
    date_to: date
    output_name: str


@dataclass(frozen=True)
class WorklogResult:
    path: Path
    entry_count: int
    workspace_count: int
    date_from: str
    date_to: str
    label: str


def resolve_worklog_range(
    *,
    period: str | None,
    date_from: str | None,
    date_to: str | None,
    reference_date: date | None = None,
) -> WorklogRange:
    today = reference_date or date.today()
    if period is not None:
        if date_from is not None or date_to is not None:
            raise ValueError("Use either a period argument or --from/--to, not both.")
        if period == "yesterday":
            target = today - timedelta(days=1)
            return WorklogRange(
                label="yesterday",
                date_from=target,
                date_to=target,
                output_name=_output_name(target, target),
            )
        if period == "last-week":
            start, end = _previous_calendar_week(today)
            return WorklogRange(
                label="last-week",
                date_from=start,
                date_to=end,
                output_name=_output_name(start, end),
            )
        if period == "last-month":
            start, end = _previous_calendar_month(today)
            return WorklogRange(
                label="last-month",
                date_from=start,
                date_to=end,
                output_name=_output_name(start, end),
            )
        raise ValueError(f"Unsupported period: {period}")
    if date_from is None or date_to is None:
        raise ValueError(
            "Provide a period (yesterday, last-week, last-month) or both --from and --to."
        )
    parsed_from = date.fromisoformat(date_from)
    parsed_to = date.fromisoformat(date_to)
    if parsed_from > parsed_to:
        raise ValueError("--from must be on or before --to.")
    return WorklogRange(
        label=f"{parsed_from.isoformat()}..{parsed_to.isoformat()}",
        date_from=parsed_from,
        date_to=parsed_to,
        output_name=_output_name(parsed_from, parsed_to),
    )


def generate_worklog(
    *,
    output_dir: Path,
    state_store: StateStore,
    worklog_range: WorklogRange,
) -> WorklogResult:
    date_from = worklog_range.date_from.isoformat()
    date_to = worklog_range.date_to.isoformat()
    rows = state_store.list_candidates_in_range(date_from=date_from, date_to=date_to)
    markdown = render_worklog_markdown(
        worklog_range=worklog_range,
        rows=rows,
    )
    worklogs_dir = output_dir / "worklogs"
    worklogs_dir.mkdir(parents=True, exist_ok=True)
    output_path = worklogs_dir / worklog_range.output_name
    output_path.write_text(markdown, encoding="utf-8")
    workspace_ids = {stored.candidate.workspace_id for _, stored in rows}
    return WorklogResult(
        path=output_path,
        entry_count=len(rows),
        workspace_count=len(workspace_ids),
        date_from=date_from,
        date_to=date_to,
        label=worklog_range.label,
    )


def render_worklog_markdown(
    *,
    worklog_range: WorklogRange,
    rows: list[tuple[str, StoredCandidate]],
) -> str:
    date_from = worklog_range.date_from.isoformat()
    date_to = worklog_range.date_to.isoformat()
    workspace_ids = sorted({stored.candidate.workspace_id for _, stored in rows})
    lines = [
        "---",
        "hks_type: session_worklog",
        f"period: {worklog_range.label}",
        f"date_from: {date_from}",
        f"date_to: {date_to}",
        "generator: session2memory",
        "source_domain: coding_session",
        "schema_version: 1",
        "---",
        f"# Worklog: {worklog_range.label}",
        "",
        "## Summary",
        f"- entries: {len(rows)}",
        f"- workspaces: {len(workspace_ids)}",
        f"- date_range: {date_from} .. {date_to}",
        "",
    ]
    grouped: dict[str, list[tuple[str, StoredCandidate]]] = {
        kind: [] for kind, _ in WORKLOG_KIND_SECTIONS
    }
    for import_date, stored in rows:
        grouped.setdefault(stored.candidate.kind, []).append((import_date, stored))

    for kind, section in WORKLOG_KIND_SECTIONS:
        section_rows = grouped.get(kind, [])
        lines.append(f"## {section}")
        if section_rows:
            for import_date, stored in section_rows:
                lines.append(_worklog_entry_line(import_date=import_date, stored=stored))
        else:
            lines.append("_No entries._")
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _worklog_entry_line(*, import_date: str, stored: StoredCandidate) -> str:
    candidate = stored.candidate
    evidence = candidate.evidence
    meta = (
        "{"
        f"evidence_id={stored.evidence_id} "
        f"tool={evidence.tool} "
        f"session_id={evidence.session_id} "
        f"lines={evidence.message_start}-{evidence.message_end} "
        f"workspace_id={candidate.workspace_id} "
        f"import_date={import_date}"
        "}"
    )
    return f"- [{candidate.kind}] {candidate.text} {meta}"


def _output_name(date_from: date, date_to: date) -> str:
    if date_from == date_to:
        return f"{date_from.isoformat()}.md"
    return f"{date_from.isoformat()}_{date_to.isoformat()}.md"


def _previous_calendar_week(today: date) -> tuple[date, date]:
    this_week_monday = today - timedelta(days=today.weekday())
    last_week_sunday = this_week_monday - timedelta(days=1)
    last_week_monday = last_week_sunday - timedelta(days=6)
    return last_week_monday, last_week_sunday


def _previous_calendar_month(today: date) -> tuple[date, date]:
    first_of_this_month = today.replace(day=1)
    last_day_prev = first_of_this_month - timedelta(days=1)
    first_day_prev = last_day_prev.replace(day=1)
    return first_day_prev, last_day_prev
