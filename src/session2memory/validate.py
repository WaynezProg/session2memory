from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

JSONDict = dict[str, Any]
VALID_CANDIDATE_TYPES = {"memory_candidate", "rule_candidate", "validator_candidate"}
MOCK_MODES = {"mock", "dry_run"}
SCHEMA_VERSION = 1


class ValidateError(ValueError):
    pass


@dataclass(frozen=True)
class ValidateResult:
    distill_dir: Path
    validated: int
    passed: int
    needs_review: int
    blocked: int
    merged_candidates: int


def validate_distill(distill_dir: Path) -> ValidateResult:
    evidence = _read_jsonl_strict(distill_dir / "evidence_index.jsonl")
    candidates = _read_jsonl_strict(distill_dir / "candidates.jsonl")
    evidence_by_id = {
        str(row["evidence_id"]): row
        for row in evidence
        if isinstance(row.get("evidence_id"), str) and row.get("evidence_id")
    }
    merged_candidates = _merge_duplicates(candidates)
    validation_rows: list[JSONDict] = []
    enriched_candidates: list[JSONDict] = []

    for candidate in merged_candidates:
        validation = _validate_candidate(candidate, evidence_by_id, evidence)
        validation_rows.append(validation)
        enriched = dict(candidate)
        enriched.update(
            {
                "validation_outcome": validation["validation_outcome"],
                "validation_score": validation["validation_score"],
                "hard_gate_failures": validation["hard_gate_failures"],
                "blocked_by": validation["blocked_by"],
                "merged_from": validation["merged_from"],
            }
        )
        if validation["validation_outcome"] == "blocked":
            enriched["status"] = "blocked"
        elif validation["merged_from"]:
            enriched["status"] = "merged"
        enriched_candidates.append(enriched)

    report = _report(validation_rows)
    files = {
        distill_dir / "validation.jsonl": _format_jsonl(validation_rows),
        distill_dir / "validation_report.json": _format_json(report),
        distill_dir / "merged_candidates.jsonl": _format_jsonl(enriched_candidates),
    }
    _atomic_writes(files)
    return ValidateResult(
        distill_dir=distill_dir,
        validated=len(validation_rows),
        passed=report["counts"]["pass"],
        needs_review=report["counts"]["needs_review"],
        blocked=report["counts"]["blocked"],
        merged_candidates=len(enriched_candidates),
    )


def _merge_duplicates(candidates: list[JSONDict]) -> list[JSONDict]:
    grouped: dict[tuple[str, str, str, str], JSONDict] = {}
    order: list[tuple[str, str, str, str]] = []
    for candidate in candidates:
        key = (
            str(candidate.get("candidate_type", "")),
            str(candidate.get("reuse_scope", "")),
            str(candidate.get("workspace_id", "")),
            _normalize_claim(
                str(candidate.get("normalized_claim") or candidate.get("claim") or "")
            ),
        )
        existing = grouped.get(key)
        if existing is None:
            clone = dict(candidate)
            clone["evidence_ids"] = _unique_strings(candidate.get("evidence_ids"))
            clone["merged_from"] = _unique_strings(candidate.get("merged_from"))
            grouped[key] = clone
            order.append(key)
            continue
        existing["evidence_ids"] = _unique_strings(
            [*existing.get("evidence_ids", []), *_unique_strings(candidate.get("evidence_ids"))]
        )
        merged_from = _unique_strings(existing.get("merged_from"))
        candidate_id = candidate.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            merged_from.append(candidate_id)
        existing["merged_from"] = _unique_strings(merged_from)
    return [grouped[key] for key in order]


def _validate_candidate(
    candidate: JSONDict,
    evidence_by_id: dict[str, JSONDict],
    all_evidence_rows: list[JSONDict],
) -> JSONDict:
    candidate_id = str(candidate.get("candidate_id", ""))
    evidence_ids = _unique_strings(candidate.get("evidence_ids"))
    evidence_rows = [
        evidence_by_id[evidence_id]
        for evidence_id in evidence_ids
        if evidence_id in evidence_by_id
    ]
    failures: list[str] = []
    blocked_by: list[str] = []

    if not evidence_ids:
        failures.append("missing_evidence_ids")
    missing = [evidence_id for evidence_id in evidence_ids if evidence_id not in evidence_by_id]
    if missing:
        failures.append("missing_evidence_record")
        blocked_by.extend(missing)
    if candidate.get("candidate_type") not in VALID_CANDIDATE_TYPES:
        failures.append("invalid_candidate_type")
    if _missing_source_path_without_marker(evidence_rows):
        failures.append("missing_source_path")
    missing_existing_source_paths = _missing_existing_source_paths(evidence_rows)
    if missing_existing_source_paths:
        failures.append("missing_source_path")
        blocked_by.extend(missing_existing_source_paths)
    if _source_unavailable_without_reason(evidence_rows):
        failures.append("source_unavailable_without_reason")
    if _missing_source_record(evidence_rows):
        failures.append("missing_evidence_record")
    if evidence_rows and _assistant_only(evidence_rows):
        failures.append("assistant_only_claim")
    if _mock_or_dry_run_real_completion(candidate, evidence_rows):
        failures.append("mock_or_dry_run_real_completion")
    invalid_timestamp_ids = _invalid_timestamp_evidence_ids(evidence_rows)
    if invalid_timestamp_ids:
        failures.append("invalid_timestamp")
        blocked_by.extend(invalid_timestamp_ids)
    contradiction_ids = _contradicting_evidence_ids(
        candidate_id,
        evidence_rows,
        all_evidence_rows,
    )
    if contradiction_ids.invalid_timestamp_ids:
        failures.append("invalid_timestamp")
        blocked_by.extend(contradiction_ids.invalid_timestamp_ids)
    if contradiction_ids.contradicting_ids:
        failures.append("newer_contradictory_evidence")
        blocked_by.extend(contradiction_ids.contradicting_ids)

    score = _score_candidate(candidate, evidence_ids=evidence_ids, evidence_rows=evidence_rows)
    if failures:
        outcome = "blocked"
        score = min(score, 49)
    elif score >= 70:
        outcome = "pass"
    elif score >= 50:
        outcome = "needs_review"
    else:
        outcome = "blocked"

    return {
        "candidate_id": candidate_id,
        "validation_outcome": outcome,
        "validation_score": score,
        "hard_gate_failures": _unique_strings(failures),
        "evidence_ids": evidence_ids,
        "merged_from": _unique_strings(candidate.get("merged_from")),
        "blocked_by": _unique_strings(blocked_by),
    }


def _score_candidate(
    candidate: JSONDict,
    *,
    evidence_ids: list[str],
    evidence_rows: list[JSONDict],
) -> int:
    score = 50
    if any(row.get("source_type") == "user_correction" for row in evidence_rows):
        score += 30
    if any("user" in _roles(row) for row in evidence_rows):
        score += 25
    if any(
        row.get("source_type") in {"tool_output", "test_result", "file_snapshot"}
        for row in evidence_rows
    ):
        score += 20
    if _unique_strings(candidate.get("source_review_ids")):
        score += 15
    if len(evidence_ids) > 1:
        score += 10
    if any(row.get("source_type") == "assistant_summary" for row in evidence_rows):
        score += 5
    if any(row.get("source_available") is False for row in evidence_rows):
        score -= 15
    if any(row.get("evidence_mode") == "unknown" for row in evidence_rows):
        score -= 10
    risk_level = candidate.get("risk_level")
    if risk_level == "high":
        score -= 10
    elif risk_level == "critical":
        score -= 25
    return max(0, min(100, score))


def _missing_source_path_without_marker(evidence_rows: list[JSONDict]) -> bool:
    return any(
        not row.get("source_path") and row.get("source_available") is not False
        for row in evidence_rows
    )


def _missing_existing_source_paths(evidence_rows: list[JSONDict]) -> list[str]:
    missing: list[str] = []
    for row in evidence_rows:
        if _source_explicitly_unavailable(row):
            continue
        source_path = row.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            continue
        try:
            exists = Path(source_path).expanduser().exists()
        except OSError:
            exists = False
        if not exists and isinstance(row.get("evidence_id"), str):
            missing.append(row["evidence_id"])
    return missing


def _source_explicitly_unavailable(row: JSONDict) -> bool:
    reason = row.get("source_unavailable_reason")
    if row.get("source_available") is False and reason:
        return True
    return reason in {"unavailable", "permission_denied"}


def _source_unavailable_without_reason(evidence_rows: list[JSONDict]) -> bool:
    return any(
        row.get("source_available") is False and not row.get("source_unavailable_reason")
        for row in evidence_rows
    )


def _missing_source_record(evidence_rows: list[JSONDict]) -> bool:
    return any(
        row.get("source_unavailable_reason") == "missing_evidence_record"
        for row in evidence_rows
    )


def _assistant_only(evidence_rows: list[JSONDict]) -> bool:
    for row in evidence_rows:
        roles = set(_roles(row))
        if row.get("source_type") != "assistant_summary" and roles != {"assistant"}:
            return False
    return True


def _mock_or_dry_run_real_completion(candidate: JSONDict, evidence_rows: list[JSONDict]) -> bool:
    if candidate.get("claim_mode") != "real_completion" or not evidence_rows:
        return False
    modes = {str(row.get("evidence_mode", "unknown")) for row in evidence_rows}
    return bool(modes) and modes <= MOCK_MODES


@dataclass(frozen=True)
class ContradictionResult:
    contradicting_ids: list[str]
    invalid_timestamp_ids: list[str]


def _contradicting_evidence_ids(
    candidate_id: str,
    candidate_evidence_rows: list[JSONDict],
    all_evidence_rows: list[JSONDict],
) -> ContradictionResult:
    contradicting: list[str] = []
    invalid_timestamp_ids: list[str] = []
    latest_candidate_timestamp = _latest_timestamp(candidate_evidence_rows)
    for row in all_evidence_rows:
        ids = _unique_strings(row.get("contradicts_candidate_ids"))
        single = row.get("contradicts_candidate_id")
        if isinstance(single, str):
            ids.append(single)
        evidence_id = row.get("evidence_id")
        if candidate_id not in ids or not isinstance(evidence_id, str):
            continue
        parsed_timestamp = _parse_timestamp(row.get("timestamp"))
        if parsed_timestamp is None:
            invalid_timestamp_ids.append(evidence_id)
            continue
        is_user_correction = row.get("source_type") == "user_correction"
        is_newer = (
            latest_candidate_timestamp is None
            or parsed_timestamp > latest_candidate_timestamp
        )
        if is_user_correction or is_newer:
            contradicting.append(evidence_id)
    return ContradictionResult(
        contradicting_ids=_unique_strings(contradicting),
        invalid_timestamp_ids=_unique_strings(invalid_timestamp_ids),
    )


def _invalid_timestamp_evidence_ids(evidence_rows: list[JSONDict]) -> list[str]:
    invalid: list[str] = []
    for row in evidence_rows:
        if _parse_timestamp(row.get("timestamp")) is None and isinstance(
            row.get("evidence_id"),
            str,
        ):
            invalid.append(row["evidence_id"])
    return invalid


def _latest_timestamp(evidence_rows: list[JSONDict]) -> datetime | None:
    timestamps: list[datetime] = []
    for row in evidence_rows:
        timestamp = _parse_timestamp(row.get("timestamp"))
        if timestamp is not None:
            timestamps.append(timestamp)
    return max(timestamps) if timestamps else None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _report(validation_rows: list[JSONDict]) -> JSONDict:
    counts = {"pass": 0, "needs_review": 0, "blocked": 0, "merged": 0}
    for row in validation_rows:
        outcome = row.get("validation_outcome")
        if outcome in counts:
            counts[outcome] += 1
    return {
        "generator": "session2memory",
        "command": "validate",
        "schema_version": SCHEMA_VERSION,
        "counts": counts,
    }


def _read_jsonl_strict(path: Path) -> list[JSONDict]:
    if not path.exists():
        raise ValidateError(f"Missing required file: {path}")
    rows: list[JSONDict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidateError(f"Invalid JSONL in {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(loaded, dict):
            raise ValidateError(f"Invalid JSONL in {path}:{line_number}: expected object")
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
        raise ValidateError(str(exc)) from exc


def _format_jsonl(rows: list[JSONDict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def _format_json(row: JSONDict) -> str:
    return json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _unique_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in result:
            result.append(item)
    return result


def _roles(row: JSONDict) -> list[str]:
    return _unique_strings(row.get("actor_roles"))


def _normalize_claim(value: str) -> str:
    return " ".join(value.lower().split())
