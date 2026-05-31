from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from session2memory.review_normalize import normalize_review_text

ConflictResolve = Literal["keep-new", "keep-old", "skip"]


@dataclass(frozen=True)
class ConflictGroup:
    conflict_id: str
    workspace_id: str
    kind: str
    normalized_text: str
    review_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


def find_conflicts(rows: list[dict[str, Any]]) -> list[ConflictGroup]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("durable_suggestion") is not True:
            continue
        key = (
            str(row.get("workspace_id", "")),
            str(row.get("kind", "")),
            normalize_review_text(str(row.get("text", ""))),
        )
        buckets.setdefault(key, []).append(row)

    groups: list[ConflictGroup] = []
    for index, ((workspace_id, kind, normalized), members) in enumerate(
        sorted(buckets.items()), start=1
    ):
        evidence_ids = {str(row.get("evidence_id", "")) for row in members}
        if len(evidence_ids) < 2:
            continue
        review_ids = tuple(str(row.get("id", "")) for row in members)
        groups.append(
            ConflictGroup(
                conflict_id=f"c{index:04d}",
                workspace_id=workspace_id,
                kind=kind,
                normalized_text=normalized,
                review_ids=review_ids,
                evidence_ids=tuple(sorted(evidence_ids)),
            )
        )
    return groups


def winners_for_resolve(
    *,
    groups: list[ConflictGroup],
    rows: list[dict[str, Any]],
    resolve: ConflictResolve,
    existing_memory_text: str,
) -> set[str]:
    row_by_id = {str(row.get("id", "")): row for row in rows}
    winners: set[str] = set()
    for group in groups:
        members = [row_by_id[review_id] for review_id in group.review_ids if review_id in row_by_id]
        if resolve == "skip":
            continue
        if resolve == "keep-new":
            winner = max(members, key=_review_sort_key)
            winners.add(str(winner.get("id", "")))
            continue
        if resolve == "keep-old":
            in_memory = [
                member
                for member in members
                if normalize_review_text(str(member.get("text", ""))) in existing_memory_text
            ]
            winner = min(in_memory, key=_review_sort_key) if in_memory else min(
                members, key=_review_sort_key
            )
            winners.add(str(winner.get("id", "")))
    return winners


def blocked_without_resolve(groups: list[ConflictGroup], resolve: ConflictResolve | None) -> bool:
    return bool(groups) and resolve is None


def _review_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("id", "")), str(row.get("evidence_id", "")))
