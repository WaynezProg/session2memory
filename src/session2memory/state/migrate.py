from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from session2memory.memory_entries import parse_memory_markdown
from session2memory.models import EvidencePointer, MemoryCandidate

if TYPE_CHECKING:
    from session2memory.state.store import StateStore


@dataclass
class MigrationReport:
    migrated_candidates: int = 0
    migrated_memory_entries: int = 0
    unmapped_legacy_evidence_ids: list[str] = field(default_factory=list)


def migrate_legacy_output(store: StateStore, output_dir: Path) -> MigrationReport:
    if not store.is_empty():
        return MigrationReport()
    evidence_path = output_dir / "evidence" / "index.jsonl"
    if not evidence_path.is_file():
        return MigrationReport()
    report = MigrationReport()
    legacy_evidence: dict[str, dict[str, object]] = {}
    for line in evidence_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        legacy_id = str(row.get("evidence_id", row.get("id", "")))
        legacy_evidence[legacy_id] = row
    review_dir = output_dir / "review"
    if not review_dir.is_dir():
        return report
    for review_path in sorted(review_dir.glob("*.jsonl")):
        import_date = review_path.stem
        for line in review_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            review_row = json.loads(line)
            legacy_evidence_id = str(review_row.get("evidence_id", ""))
            evidence_row = legacy_evidence.get(legacy_evidence_id)
            if evidence_row is None:
                report.unmapped_legacy_evidence_ids.append(legacy_evidence_id)
                continue
            candidate = _candidate_from_legacy(evidence_row, review_row)
            stored = store.upsert_candidate(import_date=import_date, candidate=candidate)
            store.update_review_status(
                review_id=stored.review_id,
                status=str(review_row.get("status", "pending")),
                note=str(review_row.get("review_note", "")),
            )
            report.migrated_candidates += 1
    memories_dir = output_dir / "memories"
    if memories_dir.is_dir():
        for memory_path in memories_dir.glob("*.md"):
            workspace_id = memory_path.stem
            for entry in parse_memory_markdown(memory_path.read_text(encoding="utf-8")):
                store.insert_memory_entry(
                    workspace_id=workspace_id,
                    candidate_id=None,
                    kind=entry.kind,
                    text=entry.text,
                    evidence_id=entry.evidence_id,
                    review_ref=entry.review_ref,
                    tool=entry.tool,
                    session_id=entry.session_id,
                )
                report.migrated_memory_entries += 1
    report_path = output_dir / "migration_report.json"
    report_path.write_text(
        json.dumps(
            {
                "migrated_candidates": report.migrated_candidates,
                "migrated_memory_entries": report.migrated_memory_entries,
                "unmapped_legacy_evidence_ids": report.unmapped_legacy_evidence_ids,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def _as_int(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _candidate_from_legacy(
    evidence_row: dict[str, object],
    review_row: dict[str, object],
) -> MemoryCandidate:
    source_path = Path(str(evidence_row.get("source_path", "")))
    tool = str(evidence_row.get("tool", "unknown"))
    session_id = str(evidence_row.get("session_id", "unknown"))
    message_start = _as_int(evidence_row.get("message_start"), default=0)
    message_end = _as_int(evidence_row.get("message_end"), default=message_start)
    digest = str(evidence_row.get("digest", ""))
    workspace_path_raw = evidence_row.get("workspace_path")
    workspace_path = Path(str(workspace_path_raw)) if workspace_path_raw else None
    evidence = EvidencePointer(
        tool=tool,
        session_id=session_id,
        source_path=source_path,
        message_start=message_start,
        message_end=message_end,
        workspace_path=workspace_path,
        digest=digest,
    )
    extraction = review_row.get("extraction", "marker")
    return MemoryCandidate(
        kind=review_row.get("kind", "daily"),  # type: ignore[arg-type]
        text=str(review_row.get("text", "")),
        workspace_id=str(review_row.get("workspace_id", "")),
        evidence=evidence,
        durable=bool(review_row.get("durable_suggestion", False)),
        extraction=extraction if extraction in {"marker", "llm"} else "marker",  # type: ignore[arg-type]
        confidence=(
            float(review_row["confidence"])  # type: ignore[arg-type]
            if review_row.get("confidence") is not None
            else None
        ),
        evidence_quote=(
            str(review_row["evidence_quote"]) if review_row.get("evidence_quote") else None
        ),
    )
