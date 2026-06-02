from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from session2memory.adapters.cursor import cursor_clean_text, cursor_texts_from_content
from session2memory.memory_lifecycle import open_state_store
from session2memory.review_conflicts import (
    ConflictResolve,
    blocked_without_resolve,
    find_conflicts,
    winners_for_resolve,
)
from session2memory.review_normalize import normalize_review_text
from session2memory.state.store import StateStore

ReviewStatus = Literal["pending", "approved", "rejected", "promoted"]


@dataclass(frozen=True)
class PromoteResult:
    reviewed: int
    promoted: int
    conflicts: int = 0
    skipped_duplicate: int = 0
    skipped_conflict: int = 0
    blocked: bool = False


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


def list_review_conflicts(*, output_dir: Path, date: str) -> list[dict[str, Any]]:
    rows = _read_reviews(output_dir=output_dir, date=date)
    eligible = [
        row
        for row in rows
        if row.get("durable_suggestion") is True
        and row.get("status") in {"pending", "approved"}
    ]
    return [
        {
            "conflict_id": group.conflict_id,
            "workspace_id": group.workspace_id,
            "kind": group.kind,
            "normalized_text": group.normalized_text,
            "review_ids": list(group.review_ids),
            "evidence_ids": list(group.evidence_ids),
        }
        for group in find_conflicts(eligible)
    ]


def promote_reviews(
    *,
    output_dir: Path,
    date: str,
    resolve: ConflictResolve | None = None,
) -> PromoteResult:
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

    groups = find_conflicts(approved)
    if blocked_without_resolve(groups, resolve):
        return PromoteResult(
            reviewed=len(rows),
            promoted=0,
            conflicts=len(groups),
            blocked=True,
        )

    existing_memory = _existing_memory_corpus(output_dir)
    winners = winners_for_resolve(
        groups=groups,
        rows=rows,
        resolve=resolve or "keep-new",
        existing_memory_text=existing_memory,
    ) if groups else set()
    conflict_review_ids = {review_id for group in groups for review_id in group.review_ids}

    manifest_path = output_dir / "manifest.json"
    manifest = _read_manifest(manifest_path)
    promoted_count = 0
    skipped_duplicate = 0
    skipped_conflict = 0
    touched_memory_files: set[str] = set()
    planned_rows: list[tuple[dict[str, Any], str, bool]] = []
    store = open_state_store(output_dir)

    for row in approved:
        review_id = str(row.get("id", ""))
        if review_id in conflict_review_ids and review_id not in winners:
            skipped_conflict += 1
            continue
        memory_relpath, created, duplicate_kind = _plan_workspace_memory(
            output_dir=output_dir,
            date=date,
            row=row,
            existing_memory=existing_memory,
        )
        if duplicate_kind == "exact":
            row["status"] = "promoted"
            if store is not None:
                _insert_memory_entry_from_review(store=store, row=row, date=date)
            skipped_duplicate += 1
            continue
        if duplicate_kind == "semantic":
            row["status"] = "promoted"
            skipped_duplicate += 1
            continue
        planned_rows.append((row, memory_relpath, created))

    memory_contents: dict[str, str] = {}
    for row, memory_relpath, created in planned_rows:
        base = memory_contents.get(memory_relpath)
        if base is None:
            memory_path = output_dir / memory_relpath
            base = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
        if store is not None:
            memory_entry_id = _insert_memory_entry_from_review(store=store, row=row, date=date)
            if memory_entry_id is not None:
                row["memory_entry_id"] = memory_entry_id
        memory_contents[memory_relpath] = _render_workspace_memory_append(
            base,
            workspace_id=str(row["workspace_id"]),
            row=row,
            date=date,
        )
        touched_memory_files.add(memory_relpath)
        row["status"] = "promoted"
        if created:
            promoted_count += 1

    if store is not None:
        store.close()

    manifest_text = _render_manifest(
        manifest=manifest,
        date=date,
        memory_files=touched_memory_files,
        promoted_count=promoted_count,
        skipped_duplicate=skipped_duplicate,
        skipped_conflict=skipped_conflict,
    )
    writes: list[tuple[Path, str]] = [
        (review_path, _format_review_jsonl(rows)),
        (manifest_path, manifest_text),
        *((output_dir / relpath, content) for relpath, content in memory_contents.items()),
    ]
    _atomic_replace_writes(writes)
    return PromoteResult(
        reviewed=len(rows),
        promoted=promoted_count,
        conflicts=len(groups),
        skipped_duplicate=skipped_duplicate,
        skipped_conflict=skipped_conflict,
    )


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
        store = open_state_store(output_dir)
        if store is not None:
            store.update_review_status(
                review_id=review_id,
                status=status,
                note=note if note is not None else row.get("review_note", ""),
            )
            store.close()
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
    if tool == "cursor":
        preview = _cursor_sqlite_preview(source_path=source_path, start=start, end=end)
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
    elif tool in {"claude", "claude-desktop"}:
        text = _claude_event_text(event)
    elif tool == "qwen":
        text = _qwen_event_text(event)
    elif tool == "cursor-cli":
        text = _cursor_cli_event_text(event)
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


def _cursor_cli_event_text(event: dict[str, Any]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    return "\n".join(cursor_texts_from_content(message.get("content"))).strip()


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


def _cursor_sqlite_preview(*, source_path: Path, start: int, end: int) -> str:
    try:
        connection = sqlite3.connect(source_path)
    except sqlite3.Error as exc:
        return f"[source unreadable: {exc}]"
    try:
        rows = connection.execute("select rowid, data from blobs order by rowid")
        preview_lines: list[str] = []
        for rowid, data in rows:
            row_index = int(rowid)
            if row_index < start or row_index > end:
                continue
            raw = bytes(data) if isinstance(data, bytes | bytearray | memoryview) else b""
            if not raw.startswith(b"{"):
                continue
            try:
                loaded = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(loaded, dict):
                continue
            role = loaded.get("role")
            if role == "system":
                continue
            texts = cursor_texts_from_content(loaded.get("content"))
            text = "\n".join(cursor_clean_text(text) for text in texts if text).strip()
            if text:
                preview_lines.append(text)
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


def _existing_memory_corpus(output_dir: Path) -> str:
    memories_dir = output_dir / "memories"
    if not memories_dir.exists():
        return ""
    parts = [
        path.read_text(encoding="utf-8")
        for path in sorted(memories_dir.glob("*.md"))
        if path.is_file()
    ]
    return "\n".join(parts)


def _plan_workspace_memory(
    *,
    output_dir: Path,
    date: str,
    row: dict[str, Any],
    existing_memory: str,
) -> tuple[str, bool, str | None]:
    workspace_id = str(row["workspace_id"])
    memory_relpath = f"memories/{workspace_id}.md"
    memory_path = output_dir / memory_relpath
    existing = ""
    if memory_path.exists():
        existing = memory_path.read_text(encoding="utf-8")
    review_ref = f"{date}/{_promotion_key(row)}"
    if _review_ref_exists(existing, review_ref):
        return memory_relpath, False, "exact"
    normalized = normalize_review_text(str(row.get("text", "")))
    if normalized and normalized in existing_memory:
        return memory_relpath, False, "semantic"
    return memory_relpath, True, None


def _render_workspace_memory_append(
    existing: str,
    *,
    workspace_id: str,
    row: dict[str, Any],
    date: str,
) -> str:
    content = existing
    if content and not content.startswith("---\n"):
        content = _add_workspace_frontmatter(content, workspace_id)
    evidence_id = str(row["evidence_id"])
    review_ref = f"{date}/{_promotion_key(row)}"
    lines: list[str] = []
    if not content:
        lines.extend(_memory_header(workspace_id))
    elif not content.endswith("\n"):
        lines.append("")
    lines.append(_memory_entry_line(row=row, evidence_id=evidence_id, review_ref=review_ref))
    append_block = "\n".join(lines).rstrip() + "\n"
    return content + append_block


def _apply_workspace_memory_append(
    *,
    output_dir: Path,
    date: str,
    row: dict[str, Any],
    memory_relpath: str,
) -> None:
    workspace_id = str(row["workspace_id"])
    memory_path = output_dir / memory_relpath
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    memory_path.write_text(
        _render_workspace_memory_append(
            existing,
            workspace_id=workspace_id,
            row=row,
            date=date,
        ),
        encoding="utf-8",
    )


def _format_review_jsonl(rows: list[dict[str, Any]]) -> str:
    return (
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        + ("\n" if rows else "")
    )


def _atomic_replace_writes(writes: list[tuple[Path, str]]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for destination, content in writes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f"{destination.name}.tmp")
            temporary.write_text(content, encoding="utf-8")
            staged.append((temporary, destination))
        for temporary, destination in staged:
            temporary.replace(destination)
    except Exception:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
        raise


def _append_workspace_memory(
    *, output_dir: Path, date: str, row: dict[str, Any]
) -> tuple[str, bool]:
    existing_memory = _existing_memory_corpus(output_dir)
    memory_relpath, created, duplicate_kind = _plan_workspace_memory(
        output_dir=output_dir,
        date=date,
        row=row,
        existing_memory=existing_memory,
    )
    if duplicate_kind is not None:
        return memory_relpath, False
    _apply_workspace_memory_append(
        output_dir=output_dir,
        date=date,
        row=row,
        memory_relpath=memory_relpath,
    )
    return memory_relpath, created


def _memory_header(workspace_id: str) -> list[str]:
    return [
        "---",
        "hks_type: workspace_memory",
        "source_domain: coding_session",
        f"workspace_id: {workspace_id}",
        "generator: session2memory",
        "schema_version: 1",
        "---",
        f"# {workspace_id}",
        "",
    ]


def _add_workspace_frontmatter(existing: str, workspace_id: str) -> str:
    header = "\n".join(_memory_header(workspace_id)).rstrip() + "\n"
    if existing.startswith(f"# {workspace_id}\n"):
        body = existing.split("\n", 1)[1]
        return header + body.lstrip("\n")
    return header + existing.lstrip("\n")


def _memory_entry_line(
    *, row: dict[str, Any], evidence_id: str, review_ref: str
) -> str:
    return (
        f"- [{row['kind']}] {row['text']} "
        f"{_memory_metadata(row=row, evidence_id=evidence_id, review_ref=review_ref)}"
    )


def _review_ref_exists(existing: str, review_ref: str) -> bool:
    return f"review={review_ref}" in existing or f"review: {review_ref}" in existing


def _memory_metadata(*, row: dict[str, Any], evidence_id: str, review_ref: str) -> str:
    source = row.get("source")
    source = source if isinstance(source, dict) else {}
    tool = source.get("tool", "unknown")
    session_id = source.get("session_id", "unknown")
    message_start = source.get("message_start", "unknown")
    message_end = source.get("message_end", "unknown")
    metadata = [
        f"workspace_id={row.get('workspace_id', '')}",
        f"memory_kind={row.get('kind', '')}",
        f"tool={tool}",
        f"session_id={session_id}",
        f"evidence_id={evidence_id}",
        f"lines={message_start}-{message_end}",
        f"review={review_ref}",
    ]
    memory_entry_id = row.get("memory_entry_id")
    if memory_entry_id:
        metadata.append(f"memory_id={memory_entry_id}")
    supersedes = row.get("supersedes")
    if supersedes:
        metadata.append(f"supersedes={supersedes}")
    return "{" + " ".join(metadata) + "}"


def _insert_memory_entry_from_review(
    *,
    store: StateStore,
    row: dict[str, Any],
    date: str,
) -> str | None:
    stored = store.get_candidate_by_review_id(str(row.get("id", "")))
    if stored is None:
        return None
    source = row.get("source")
    source = source if isinstance(source, dict) else {}
    return store.insert_memory_entry(
        workspace_id=str(row["workspace_id"]),
        candidate_id=stored.candidate_id,
        kind=str(row.get("kind", "")),
        text=str(row.get("text", "")),
        evidence_id=str(row.get("evidence_id", stored.evidence_id)),
        review_ref=f"{date}/{_promotion_key(row)}",
        tool=str(source.get("tool", "unknown")),
        session_id=str(source.get("session_id", "unknown")),
        message_start=_positive_int(source.get("message_start")),
        message_end=_positive_int(source.get("message_end")),
    )


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


def _render_manifest(
    *,
    manifest: dict[str, Any],
    date: str,
    memory_files: set[str],
    promoted_count: int,
    skipped_duplicate: int = 0,
    skipped_conflict: int = 0,
) -> str:
    output_files = manifest.get("output_files", [])
    if not isinstance(output_files, list):
        output_files = []
    output_file_set = {str(item) for item in output_files}
    output_file_set.update(memory_files)
    manifest["output_files"] = sorted(output_file_set)

    counts = manifest.setdefault("counts", {})
    if isinstance(counts, dict):
        counts["durable_memories"] = int(counts.get("durable_memories", 0)) + promoted_count
        if skipped_duplicate:
            counts["promote_skipped_duplicate"] = (
                int(counts.get("promote_skipped_duplicate", 0)) + skipped_duplicate
            )
        if skipped_conflict:
            counts["promote_conflicts_skipped"] = (
                int(counts.get("promote_conflicts_skipped", 0)) + skipped_conflict
            )
    manifest["hks"] = {
        "source_type": "session_memory",
        "primary_documents": [f"daily/{date}.md"],
        "metadata_fields": ["date", "workspace_id", "tool", "memory_kind"],
    }

    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
