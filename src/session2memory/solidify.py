from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

JSONDict = dict[str, Any]
SCHEMA_VERSION = 1
CANDIDATE_FILES = {
    "memory_candidate": "memory_candidates.md",
    "rule_candidate": "rule_candidates.md",
    "validator_candidate": "validator_candidates.md",
}


class SolidifyError(ValueError):
    pass


@dataclass(frozen=True)
class SolidifyResult:
    distill_dir: Path
    solidified_dir: Path
    solidified: int
    ready_for_review: int
    needs_human_review: int
    blocked: int


def solidify_distill(distill_dir: Path) -> SolidifyResult:
    candidates_path = (
        distill_dir / "merged_candidates.jsonl"
        if (distill_dir / "merged_candidates.jsonl").exists()
        else distill_dir / "candidates.jsonl"
    )
    candidates = _read_jsonl_strict(candidates_path)
    validation_rows = _read_jsonl_strict(distill_dir / "validation.jsonl")
    evidence_rows = _read_jsonl_strict(distill_dir / "evidence_index.jsonl")
    validation_by_id = {
        str(row["candidate_id"]): row
        for row in validation_rows
        if isinstance(row.get("candidate_id"), str) and row.get("candidate_id")
    }
    evidence_by_id = {
        str(row["evidence_id"]): row
        for row in evidence_rows
        if isinstance(row.get("evidence_id"), str) and row.get("evidence_id")
    }
    solidified_rows = [
        _solidified_record(candidate, validation_by_id, evidence_by_id) for candidate in candidates
    ]
    solidified_dir = distill_dir / "solidified"
    markdown_files = {
        solidified_dir / filename: _render_markdown(candidate_type, solidified_rows, evidence_by_id)
        for candidate_type, filename in CANDIDATE_FILES.items()
    }
    manifest = _manifest(solidified_rows)
    files = {
        solidified_dir / "solidified.jsonl": _format_jsonl(solidified_rows),
        solidified_dir / "manifest.json": _format_json(manifest),
        **markdown_files,
    }
    _atomic_writes(files)
    counts = manifest["counts"]
    return SolidifyResult(
        distill_dir=distill_dir,
        solidified_dir=solidified_dir,
        solidified=counts["solidified"],
        ready_for_review=counts["ready_for_review"],
        needs_human_review=counts["needs_human_review"],
        blocked=counts["blocked"],
    )


def _solidified_record(
    candidate: JSONDict,
    validation_by_id: dict[str, JSONDict],
    evidence_by_id: dict[str, JSONDict],
) -> JSONDict:
    candidate_id = str(candidate.get("candidate_id", ""))
    validation = validation_by_id.get(candidate_id, {})
    outcome = str(
        candidate.get("validation_outcome")
        or validation.get("validation_outcome")
        or "blocked"
    )
    score = _int(candidate.get("validation_score", validation.get("validation_score", 0)))
    evidence_ids = _unique_strings(candidate.get("evidence_ids"))
    blocked_by = _unique_strings(candidate.get("blocked_by", validation.get("blocked_by")))
    hard_gate_failures = _unique_strings(
        candidate.get("hard_gate_failures", validation.get("hard_gate_failures"))
    )
    return {
        "solidified_id": _solidified_id(candidate_id, str(candidate.get("claim", "")), outcome),
        "candidate_id": candidate_id,
        "candidate_type": str(candidate.get("candidate_type", "")),
        "claim": str(candidate.get("claim", "")),
        "reuse_scope": str(candidate.get("reuse_scope", "")),
        "risk_level": str(candidate.get("risk_level", "")),
        "status": _solidified_status(outcome),
        "validation_outcome": outcome,
        "validation_score": score,
        "evidence_ids": evidence_ids,
        "merged_from": _unique_strings(candidate.get("merged_from", validation.get("merged_from"))),
        "blocked_by": blocked_by,
        "hard_gate_failures": hard_gate_failures,
        "suggested_review_action": _suggested_action(
            outcome=outcome,
            candidate_type=str(candidate.get("candidate_type", "")),
            evidence_ids=evidence_ids,
            evidence_by_id=evidence_by_id,
        ),
    }


def _solidified_status(outcome: str) -> str:
    if outcome == "pass":
        return "ready_for_review"
    if outcome == "needs_review":
        return "needs_human_review"
    return "blocked"


def _suggested_action(
    *,
    outcome: str,
    candidate_type: str,
    evidence_ids: list[str],
    evidence_by_id: dict[str, JSONDict],
) -> str:
    if outcome == "blocked":
        return "none"
    if candidate_type == "validator_candidate":
        return "create_validator_test"
    if candidate_type == "rule_candidate":
        return "review_rule_candidate"
    if candidate_type == "memory_candidate":
        return "create_pending_review_row"
    if any(evidence_id not in evidence_by_id for evidence_id in evidence_ids):
        return "none"
    return "needs_manual_triage"


def _render_markdown(
    candidate_type: str,
    rows: list[JSONDict],
    evidence_by_id: dict[str, JSONDict],
) -> str:
    title = candidate_type.replace("_", " ").title()
    selected = [row for row in rows if row.get("candidate_type") == candidate_type]
    lines = [f"# {title}", ""]
    if not selected:
        lines.append("No candidates.")
        lines.append("")
        return "\n".join(lines)
    for row in selected:
        evidence_ids = _unique_strings(row.get("evidence_ids"))
        lines.extend(
            [
                f"## {row.get('candidate_id', '')}",
                "",
                f"- claim: {row.get('claim', '')}",
                (
                    f"- validation: {row.get('validation_outcome', '')} "
                    f"({row.get('validation_score', 0)})"
                ),
                f"- evidence_ids: {', '.join(evidence_ids)}",
                f"- source_availability: {_source_availability(evidence_ids, evidence_by_id)}",
                f"- risk_level: {row.get('risk_level', '')}",
                f"- suggested_action: {row.get('suggested_review_action', '')}",
            ]
        )
        failures = _unique_strings(row.get("hard_gate_failures"))
        if failures:
            lines.append(f"- block_reasons: {', '.join(failures)}")
        lines.append("")
    return "\n".join(lines)


def _source_availability(evidence_ids: list[str], evidence_by_id: dict[str, JSONDict]) -> str:
    available = 0
    unavailable = 0
    missing = 0
    for evidence_id in evidence_ids:
        row = evidence_by_id.get(evidence_id)
        if row is None:
            missing += 1
        elif row.get("source_available") is False:
            unavailable += 1
        else:
            available += 1
    return f"available={available} unavailable={unavailable} missing={missing}"


def _manifest(rows: list[JSONDict]) -> JSONDict:
    counts = {
        "solidified": len(rows),
        "ready_for_review": sum(1 for row in rows if row.get("status") == "ready_for_review"),
        "needs_human_review": sum(1 for row in rows if row.get("status") == "needs_human_review"),
        "blocked": sum(1 for row in rows if row.get("status") == "blocked"),
    }
    return {
        "generator": "session2memory",
        "command": "solidify",
        "schema_version": SCHEMA_VERSION,
        "counts": counts,
        "output_files": [
            "solidified/solidified.jsonl",
            "solidified/memory_candidates.md",
            "solidified/rule_candidates.md",
            "solidified/validator_candidates.md",
            "solidified/manifest.json",
        ],
    }


def _read_jsonl_strict(path: Path) -> list[JSONDict]:
    if not path.exists():
        raise SolidifyError(f"Missing required file: {path}")
    rows: list[JSONDict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SolidifyError(f"Invalid JSONL in {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(loaded, dict):
            raise SolidifyError(f"Invalid JSONL in {path}:{line_number}: expected object")
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
        raise SolidifyError(str(exc)) from exc


def _format_jsonl(rows: list[JSONDict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def _format_json(row: JSONDict) -> str:
    return json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _solidified_id(candidate_id: str, claim: str, outcome: str) -> str:
    digest = sha256("\0".join((candidate_id, claim, outcome)).encode("utf-8")).hexdigest()
    return "so_" + digest[:12]


def _unique_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in result:
            result.append(item)
    return result


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
