from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

JSONDict = dict[str, Any]
CandidateType = Literal["memory_candidate", "rule_candidate", "validator_candidate"]

SCHEMA_VERSION = 1
TIMESTAMP_UNAVAILABLE = "1970-01-01T00:00:00+00:00"


class DistillError(ValueError):
    pass


@dataclass(frozen=True)
class DistillResult:
    date: str
    distill_dir: Path
    review_rows: int
    approved_reviews: int
    evidence_records: int
    candidates: int


def distill_reviews(*, output_dir: Path, date: str) -> DistillResult:
    review_path = output_dir / "review" / f"{date}.jsonl"
    evidence_path = output_dir / "evidence" / "index.jsonl"
    manifest_path = output_dir / "manifest.json"
    distill_dir = output_dir / "distill" / date

    rows = _read_jsonl_strict(review_path) if review_path.exists() else []
    evidence_rows = _read_jsonl_strict(evidence_path) if evidence_path.exists() else []
    evidence_by_id = _evidence_by_id(evidence_rows)
    approved: list[JSONDict] = []
    for row in rows:
        if row.get("status") != "approved":
            continue
        normalized = _normalize_review_row(row)
        if normalized is not None:
            approved.append(normalized)

    distilled_evidence: list[JSONDict] = []
    candidates: list[JSONDict] = []
    seen_evidence_ids: set[str] = set()

    for row in approved:
        evidence_id = str(row.get("evidence_id") or f"missing:{row['id']}")
        source_evidence = evidence_by_id.get(evidence_id)
        evidence_record = _distill_evidence(
            row=row,
            evidence_id=evidence_id,
            evidence=source_evidence,
        )
        if evidence_id not in seen_evidence_ids:
            distilled_evidence.append(evidence_record)
            seen_evidence_ids.add(evidence_id)
        candidates.append(_candidate_from_review(row=row, evidence_id=evidence_id))

    owned_files = {
        distill_dir / "evidence_index.jsonl": _format_jsonl(distilled_evidence),
        distill_dir / "candidates.jsonl": _format_jsonl(candidates),
        distill_dir / "manifest.json": _format_json(
            _manifest(
                date=date,
                output_dir=output_dir,
                review_path=review_path,
                evidence_path=evidence_path,
                manifest_path=manifest_path,
                review_rows=len(rows),
                approved_reviews=len(approved),
                evidence_records=len(distilled_evidence),
                candidates=len(candidates),
            )
        ),
    }
    _atomic_writes(owned_files)
    return DistillResult(
        date=date,
        distill_dir=distill_dir,
        review_rows=len(rows),
        approved_reviews=len(approved),
        evidence_records=len(distilled_evidence),
        candidates=len(candidates),
    )


def _normalize_review_row(row: JSONDict) -> JSONDict | None:
    review_id = row.get("id")
    text = row.get("text")
    if not isinstance(review_id, str) or not review_id:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    return dict(row)


def _evidence_by_id(rows: list[JSONDict]) -> dict[str, JSONDict]:
    indexed: dict[str, JSONDict] = {}
    for row in rows:
        evidence_id = row.get("evidence_id", row.get("id"))
        if isinstance(evidence_id, str) and evidence_id:
            indexed[evidence_id] = row
    return indexed


def _distill_evidence(*, row: JSONDict, evidence_id: str, evidence: JSONDict | None) -> JSONDict:
    source = evidence or {}
    source_path = _string(source.get("source_path"))
    source_available, unavailable_reason = _source_availability(source_path, source)
    linked_session_id = _string(
        source.get("linked_session_id")
        or source.get("session_id")
        or _row_source(row).get("session_id")
        or ""
    )
    timestamp = _string(source.get("timestamp") or row.get("timestamp") or TIMESTAMP_UNAVAILABLE)
    summary = _summary_for_evidence(evidence=evidence, timestamp=timestamp)
    record: JSONDict = {
        "evidence_id": evidence_id,
        "source_path": source_path,
        "source_type": _string(source.get("source_type") or "session_message"),
        "timestamp": timestamp,
        "linked_session_id": linked_session_id,
        "confidence": _confidence(row.get("confidence", source.get("confidence", 1.0))),
        "source_available": source_available,
        "source_unavailable_reason": unavailable_reason,
        "tool": _string(source.get("tool") or _row_source(row).get("tool") or ""),
        "workspace_id": _string(row.get("workspace_id") or source.get("workspace_id") or ""),
        "review_ids": [_string(row["id"])],
        "message_start": _optional_int(
            source.get("message_start") or _row_source(row).get("message_start")
        ),
        "message_end": _optional_int(
            source.get("message_end") or _row_source(row).get("message_end")
        ),
        "actor_roles": _string_list(source.get("actor_roles")) or ["unknown"],
        "evidence_mode": _string(source.get("evidence_mode") or "real"),
        "summary": summary,
        "digest": _string(source.get("digest") or ""),
    }
    if evidence is None:
        record["source_type"] = "review_row"
        record["source_available"] = False
        record["source_unavailable_reason"] = "missing_evidence_record"
        record["evidence_mode"] = "unknown"
    return record


def _candidate_from_review(*, row: JSONDict, evidence_id: str) -> JSONDict:
    candidate_type = _candidate_type(row)
    claim = str(row["text"]).strip()
    candidate: JSONDict = {
        "candidate_id": _candidate_id(
            candidate_type=candidate_type,
            workspace_id=_string(row.get("workspace_id") or ""),
            claim=claim,
            evidence_id=evidence_id,
        ),
        "candidate_type": candidate_type,
        "claim": claim,
        "evidence_ids": [evidence_id],
        "reuse_scope": _reuse_scope(row),
        "risk_level": _risk_level(candidate_type),
        "status": "proposed",
        "normalized_claim": _normalize_claim(claim),
        "source_review_ids": [_string(row["id"])],
        "workspace_id": _string(row.get("workspace_id") or ""),
        "created_at": TIMESTAMP_UNAVAILABLE,
        "distiller": "session2memory",
        "claim_mode": _claim_mode(candidate_type),
        "merged_from": [],
        "blocked_by": [],
        "review_notes": [_string(row.get("review_note") or "")] if row.get("review_note") else [],
    }
    return candidate


def _candidate_type(row: JSONDict) -> CandidateType:
    kind = _string(row.get("kind"))
    if kind == "verification":
        return "validator_candidate"
    if kind in {"constraint", "pitfall"}:
        return "rule_candidate"
    return "memory_candidate"


def _reuse_scope(row: JSONDict) -> str:
    return "workspace" if row.get("workspace_id") else "session"


def _risk_level(candidate_type: CandidateType) -> str:
    if candidate_type == "validator_candidate":
        return "low"
    if candidate_type == "rule_candidate":
        return "high"
    return "medium"


def _claim_mode(candidate_type: CandidateType) -> str:
    if candidate_type == "validator_candidate":
        return "plan_or_validation"
    return "real_completion"


def _candidate_id(
    *,
    candidate_type: CandidateType,
    workspace_id: str,
    claim: str,
    evidence_id: str,
) -> str:
    raw = "\0".join((candidate_type, workspace_id, _normalize_claim(claim), evidence_id))
    return "dc_" + sha256(raw.encode("utf-8")).hexdigest()[:12]


def _source_availability(source_path: str, evidence: JSONDict) -> tuple[bool, str | None]:
    if evidence.get("source_available") is False:
        reason = evidence.get("source_unavailable_reason")
        return False, _string(reason or "source_unavailable")
    if not source_path:
        return False, "source_path_unavailable"
    return Path(source_path).expanduser().exists(), (
        None if Path(source_path).expanduser().exists() else "source_path_not_found"
    )


def _summary_for_evidence(*, evidence: JSONDict | None, timestamp: str) -> str:
    if evidence is None:
        return "Review row referenced evidence that was not present in evidence/index.jsonl."
    if timestamp == TIMESTAMP_UNAVAILABLE:
        return "Source timestamp unavailable; using deterministic sentinel timestamp."
    return "Reviewer-approved evidence span for a distill candidate."


def _manifest(
    *,
    date: str,
    output_dir: Path,
    review_path: Path,
    evidence_path: Path,
    manifest_path: Path,
    review_rows: int,
    approved_reviews: int,
    evidence_records: int,
    candidates: int,
) -> JSONDict:
    distill_rel = f"distill/{date}"
    return {
        "generator": "session2memory",
        "command": "distill",
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "input_files": [
            _relative_or_absolute(review_path, output_dir),
            _relative_or_absolute(evidence_path, output_dir),
            _relative_or_absolute(manifest_path, output_dir),
        ],
        "output_files": [
            f"{distill_rel}/evidence_index.jsonl",
            f"{distill_rel}/candidates.jsonl",
            f"{distill_rel}/manifest.json",
        ],
        "counts": {
            "review_rows": review_rows,
            "approved_reviews": approved_reviews,
            "evidence_records": evidence_records,
            "candidates": candidates,
        },
    }


def _read_jsonl_strict(path: Path) -> list[JSONDict]:
    rows: list[JSONDict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DistillError(f"Invalid JSONL in {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(loaded, dict):
            raise DistillError(f"Invalid JSONL in {path}:{line_number}: expected object")
        rows.append(loaded)
    return rows


def _atomic_writes(files: dict[Path, str]) -> None:
    temp_paths: list[Path] = []
    try:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(content, encoding="utf-8")
            temp_paths.append(tmp)
        for path in files:
            os.replace(path.with_name(f".{path.name}.tmp"), path)
    except OSError as exc:
        for tmp in temp_paths:
            tmp.unlink(missing_ok=True)
        raise DistillError(str(exc)) from exc


def _format_jsonl(rows: list[JSONDict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def _format_json(row: JSONDict) -> str:
    return json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _row_source(row: JSONDict) -> JSONDict:
    source = row.get("source")
    return source if isinstance(source, dict) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _confidence(value: object) -> float:
    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    return 1.0


def _normalize_claim(value: str) -> str:
    return " ".join(value.lower().split())


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
