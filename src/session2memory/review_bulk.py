from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ReviewStatus = Literal["pending", "approved", "rejected", "promoted"]


@dataclass(frozen=True)
class BulkFilter:
    status: ReviewStatus | None = "pending"
    kinds: frozenset[str] | None = None
    workspace_id: str | None = None
    tool: str | None = None
    review_ids: frozenset[str] | None = None


@dataclass(frozen=True)
class BulkResult:
    matched: int
    updated: int
    skipped: int


def bulk_update_reviews(
    *,
    output_dir: Path,
    date: str,
    target_status: ReviewStatus,
    filters: BulkFilter,
    durable: bool | None,
    note: str | None,
    dry_run: bool,
) -> BulkResult:
    review_path = output_dir / "review" / f"{date}.jsonl"
    if not review_path.exists():
        return BulkResult(matched=0, updated=0, skipped=0)

    rows = _read_jsonl(review_path)
    matched = 0
    updated = 0
    skipped = 0
    for row in rows:
        if not _matches_filter(row, filters):
            continue
        matched += 1
        if row.get("status") == "promoted":
            skipped += 1
            continue
        if row.get("status") == target_status:
            skipped += 1
            continue
        if not dry_run:
            row["status"] = target_status
            if note is not None:
                row["review_note"] = note
            if durable is not None:
                row["durable_suggestion"] = durable
        updated += 1

    if not dry_run and updated:
        _write_jsonl(review_path, rows)
    return BulkResult(matched=matched, updated=updated, skipped=skipped)


def _matches_filter(row: dict[str, Any], filters: BulkFilter) -> bool:
    if filters.review_ids is not None and row.get("id") not in filters.review_ids:
        return False
    if filters.status is not None and row.get("status") != filters.status:
        return False
    if filters.kinds is not None and row.get("kind") not in filters.kinds:
        return False
    if filters.workspace_id is not None and row.get("workspace_id") != filters.workspace_id:
        return False
    if filters.tool is not None:
        source = row.get("source")
        if not isinstance(source, dict) or source.get("tool") != filters.tool:
            return False
    return True


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            loaded = json.loads(line)
            if isinstance(loaded, dict):
                rows.append(loaded)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
