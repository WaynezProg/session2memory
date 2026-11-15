"""Shared rules for ``evidence/index.jsonl`` across imported dates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Evidence rows from every imported date share ``evidence/index.jsonl``; ids are only
# unique within one date when import runs without state (e000001, ...), so readers
# must match rows on this field as well as on the id.
EVIDENCE_DATE_FIELD = "import_date"


def evidence_rows_for_date[R: Mapping[str, Any]](rows: Sequence[R], date: str) -> list[R]:
    """Evidence rows that may belong to ``date``, ordered so the best match is last.

    Rows tagged with another ``import_date`` are excluded. Untagged rows (written before
    the field existed) come first and rows tagged with ``date`` after them, so a
    last-match-wins lookup by id prefers the exact date.
    """
    untagged = [row for row in rows if not row.get(EVIDENCE_DATE_FIELD)]
    tagged = [row for row in rows if row.get(EVIDENCE_DATE_FIELD) == date]
    return [*untagged, *tagged]
