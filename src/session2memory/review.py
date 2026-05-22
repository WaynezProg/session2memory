from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

ReviewStatus = Literal["pending", "approved", "rejected", "promoted"]


@dataclass(frozen=True)
class PromoteResult:
    reviewed: int
    promoted: int


@dataclass(frozen=True)
class ReviewUpdateResult:
    review_id: str
    status: ReviewStatus


@dataclass(frozen=True)
class ReviewInspection:
    row: dict[str, Any]
    evidence: dict[str, Any] | None
    preview: str


class ReviewNotFoundError(ValueError):
    pass


def list_reviews(
    *, output_dir: Path, date: str, status: ReviewStatus | None = None
) -> list[dict[str, Any]]:
    rows = _read_reviews(output_dir=output_dir, date=date)
    if status is None:
        return rows
    return [row for row in rows if row.get("status") == status]


def inspect_review(*, output_dir: Path, date: str, review_id: str) -> ReviewInspection:
    rows = _read_reviews(output_dir=output_dir, date=date)
    for row in rows:
        if row.get("id") != review_id:
            continue
        evidence = _find_evidence(output_dir=output_dir, row=row)
        return ReviewInspection(
            row=dict(row),
            evidence=dict(evidence) if evidence is not None else None,
            preview=_evidence_preview(evidence),
        )
    raise ReviewNotFoundError(f"Review id not found: {review_id}")


def approve_review(
    *,
    output_dir: Path,
    date: str,
    review_id: str,
    note: str | None = None,
    durable: bool = False,
) -> ReviewUpdateResult:
    return _update_review_status(
        output_dir=output_dir,
        date=date,
        review_id=review_id,
        status="approved",
        note=note,
        durable=True if durable else None,
    )


def reject_review(
    *,
    output_dir: Path,
    date: str,
    review_id: str,
    note: str | None = None,
) -> ReviewUpdateResult:
    return _update_review_status(
        output_dir=output_dir,
        date=date,
        review_id=review_id,
        status="rejected",
        note=note,
        durable=None,
    )


def promote_reviews(*, output_dir: Path, date: str) -> PromoteResult:
    review_path = _review_path(output_dir=output_dir, date=date)
    if not review_path.exists():
        return PromoteResult(reviewed=0, promoted=0)

    rows = _read_jsonl(review_path)
    approved = [
        row
        for row in rows
        if row.get("status") == "approved" and row.get("durable_suggestion") is True
    ]
    if not approved:
        return PromoteResult(reviewed=len(rows), promoted=0)

    manifest_path = output_dir / "manifest.json"
    manifest = _read_manifest(manifest_path)
    promoted_count = 0
    touched_memory_files: set[str] = set()
    for row in approved:
        memory_relpath, created = _append_workspace_memory(
            output_dir=output_dir,
            date=date,
            row=row,
        )
        touched_memory_files.add(memory_relpath)
        row["status"] = "promoted"
        if created:
            promoted_count += 1

    _write_jsonl(review_path, rows)
    _update_manifest(
        manifest_path=manifest_path,
        manifest=manifest,
        memory_files=touched_memory_files,
        promoted_count=promoted_count,
    )
    return PromoteResult(reviewed=len(rows), promoted=promoted_count)


def _update_review_status(
    *,
    output_dir: Path,
    date: str,
    review_id: str,
    status: ReviewStatus,
    note: str | None,
    durable: bool | None,
) -> ReviewUpdateResult:
    review_path = _review_path(output_dir=output_dir, date=date)
    rows = _read_jsonl(review_path)
    for row in rows:
        if row.get("id") != review_id:
            continue
        row["status"] = status
        if note is not None:
            row["review_note"] = note
        if durable is not None:
            row["durable_suggestion"] = durable
        _write_jsonl(review_path, rows)
        return ReviewUpdateResult(review_id=review_id, status=status)
    raise ReviewNotFoundError(f"Review id not found: {review_id}")


def _read_reviews(*, output_dir: Path, date: str) -> list[dict[str, Any]]:
    return _read_jsonl(_review_path(output_dir=output_dir, date=date))


def _review_path(*, output_dir: Path, date: str) -> Path:
    return output_dir / "review" / f"{date}.jsonl"


def _find_evidence(*, output_dir: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    evidence_id = str(row.get("evidence_id", ""))
    for evidence in _read_jsonl(output_dir / "evidence" / "index.jsonl"):
        if evidence.get("id") == evidence_id or evidence.get("evidence_id") == evidence_id:
            return evidence
    return None


def _evidence_preview(evidence: dict[str, Any] | None, *, limit: int = 1200) -> str:
    if evidence is None:
        return "[evidence unavailable]"
    source_value = evidence.get("source_path")
    if not isinstance(source_value, str) or not source_value:
        return "[source unavailable]"
    source_path = Path(source_value)
    start = _positive_int(evidence.get("message_start"))
    end = _positive_int(evidence.get("message_end"))
    if start is None:
        return "[source range unavailable]"
    if end is None or end < start:
        end = start
    if not source_path.exists():
        return f"[source unavailable: {source_path.as_posix()}]"

    tool = str(evidence.get("tool", ""))
    if tool == "opencode":
        preview = _opencode_preview(
            source_path=source_path,
            evidence=evidence,
            start=start,
            end=end,
        )
        return _truncate(preview, limit)
    preview = _jsonl_preview(source_path=source_path, tool=tool, start=start, end=end)
    return _truncate(preview, limit)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _jsonl_preview(*, source_path: Path, tool: str, start: int, end: int) -> str:
    try:
        lines = source_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return f"[source unreadable: {exc}]"
    if start > len(lines):
        return "[source range unavailable]"

    preview_lines: list[str] = []
    for raw in lines[start - 1 : end]:
        preview_lines.append(_event_preview_text(raw=raw, tool=tool))
    return "\n".join(line for line in preview_lines if line).strip() or "[empty evidence preview]"


def _event_preview_text(*, raw: str, tool: str) -> str:
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    if not isinstance(event, dict):
        return raw.strip()

    if tool == "codex":
        text = _codex_event_text(event)
    elif tool == "claude":
        text = _claude_event_text(event)
    elif tool == "qwen":
        text = _qwen_event_text(event)
    else:
        text = ""
    return text or raw.strip()


def _codex_event_text(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ""
    if payload.get("type") != "message":
        return ""
    return _join_text_blocks(payload.get("content"))


def _claude_event_text(event: dict[str, Any]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    return _join_text_blocks(message.get("content"))


def _qwen_event_text(event: dict[str, Any]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    return _join_text_blocks(message.get("parts"))


def _join_text_blocks(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    texts: list[str] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return "\n".join(texts)


def _opencode_preview(
    *, source_path: Path, evidence: dict[str, Any], start: int, end: int
) -> str:
    session_id = str(evidence.get("session_id", ""))
    if not session_id:
        return "[source range unavailable]"
    try:
        connection = sqlite3.connect(source_path)
    except sqlite3.Error as exc:
        return f"[source unreadable: {exc}]"
    try:
        rows = connection.execute(
            """
            select p.data
            from message m
            join part p on p.message_id = m.id and p.session_id = m.session_id
            where m.session_id = ?
            order by m.time_created, m.id, p.id
            """,
            (session_id,),
        )
        preview_lines: list[str] = []
        for row_index, (part_data,) in enumerate(rows, start=1):
            if row_index < start or row_index > end:
                continue
            try:
                part_json = json.loads(str(part_data))
            except json.JSONDecodeError:
                continue
            if not isinstance(part_json, dict) or part_json.get("type") != "text":
                continue
            text = part_json.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            preview_lines.append(text.strip())
        return "\n".join(preview_lines).strip() or "[empty evidence preview]"
    except sqlite3.Error as exc:
        return f"[source unreadable: {exc}]"
    finally:
        connection.close()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        loaded = json.loads(line)
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _append_workspace_memory(
    *, output_dir: Path, date: str, row: dict[str, Any]
) -> tuple[str, bool]:
    workspace_id = str(row["workspace_id"])
    memory_path = output_dir / "memories" / f"{workspace_id}.md"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    evidence_id = str(row["evidence_id"])
    review_ref = f"{date}/{_promotion_key(row)}"
    if f"review: {review_ref}" in existing:
        return memory_path.relative_to(output_dir).as_posix(), False

    lines: list[str] = []
    if not existing:
        lines.extend(_memory_header(workspace_id))
    elif not existing.endswith("\n"):
        lines.append("")
    lines.append(
        f"- [{row['kind']}] {row['text']} "
        f"(evidence: {evidence_id}, review: {review_ref})"
    )

    with memory_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
    return memory_path.relative_to(output_dir).as_posix(), True


def _memory_header(workspace_id: str) -> list[str]:
    return [f"# {workspace_id}", ""]


def _promotion_key(row: dict[str, Any]) -> str:
    raw = "\0".join(
        (
            str(row.get("workspace_id", "")),
            str(row.get("kind", "")),
            str(row.get("text", "")),
            str(row.get("evidence_id", "")),
        )
    )
    return "p" + sha256(raw.encode("utf-8")).hexdigest()[:16]


def _update_manifest(
    *,
    manifest_path: Path,
    manifest: dict[str, Any],
    memory_files: set[str],
    promoted_count: int,
) -> None:
    output_files = manifest.get("output_files", [])
    if not isinstance(output_files, list):
        output_files = []
    output_file_set = {str(item) for item in output_files}
    output_file_set.update(memory_files)
    manifest["output_files"] = sorted(output_file_set)

    counts = manifest.setdefault("counts", {})
    if isinstance(counts, dict):
        counts["durable_memories"] = int(counts.get("durable_memories", 0)) + promoted_count

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
