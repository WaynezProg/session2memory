from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from session2memory import __version__
from session2memory.agentic_os_index import AgenticOsIndex
from session2memory.models import MemoryCandidate, WorkspaceIdentity
from session2memory.redaction import redact_text
from session2memory.state.store import StoredCandidate

P0_TOOL_ORDER = (
    "codex",
    "claude",
    "claude-desktop",
    "qwen",
    "opencode",
    "cursor",
    "cursor-cli",
    "openclaw",
    "hermes",
)


def write_output(
    *,
    output_dir: Path,
    date: str,
    candidates: Sequence[MemoryCandidate] | None = None,
    stored_candidates: Sequence[StoredCandidate] | None = None,
    workspaces: Mapping[str, WorkspaceIdentity],
    scanned_tools: Sequence[str],
    source_roots: Mapping[str, Path],
    skipped: Sequence[str],
    session_count: int,
    message_count: int,
    filtered_count: int,
    dry_run: bool,
    agentic_os_index: AgenticOsIndex | None = None,
    redact_exports: bool = True,
) -> None:
    if dry_run:
        return

    home = Path.home()
    if stored_candidates is not None:
        ordered_stored = sorted(stored_candidates, key=lambda row: row.evidence_id)
        ordered = [row.candidate for row in ordered_stored]
        evidence_by_id = {id(row.candidate): row.evidence_id for row in ordered_stored}
        review_rows = ordered_stored
    else:
        if candidates is None:
            raise ValueError("candidates or stored_candidates is required")
        ordered = sorted(candidates, key=_candidate_sort_key)
        evidence_by_id = {
            id(candidate): f"e{index:06d}" for index, candidate in enumerate(ordered, 1)
        }
        review_rows = None

    _clear_managed_output(output_dir)
    (output_dir / "daily").mkdir(parents=True, exist_ok=True)
    (output_dir / "memories").mkdir(parents=True, exist_ok=True)
    (output_dir / "evidence").mkdir(parents=True, exist_ok=True)
    (output_dir / "review").mkdir(parents=True, exist_ok=True)

    daily_path = output_dir / "daily" / f"{date}.md"
    daily_path.write_text(
        _maybe_redact(
            _daily_markdown(date, ordered, evidence_by_id, workspaces, scanned_tools),
            redact_exports=redact_exports,
            home=home,
        ),
        encoding="utf-8",
    )
    evidence_lines = [
        json.dumps(
            _evidence_record(
                evidence_by_id[id(candidate)],
                candidate,
                agentic_os_index=agentic_os_index,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
        for candidate in ordered
    ]
    evidence_path = output_dir / "evidence" / "index.jsonl"
    evidence_path.write_text(
        "\n".join(evidence_lines) + ("\n" if evidence_lines else ""),
        encoding="utf-8",
    )
    review_path = output_dir / "review" / f"{date}.jsonl"
    review_lines = []
    if review_rows is not None:
        for stored in review_rows:
            review_lines.append(
                json.dumps(
                    _review_record(
                        stored.review_id,
                        stored.evidence_id,
                        stored.candidate,
                        status=stored.review_status,
                        review_note=stored.review_note,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    else:
        review_lines = [
            json.dumps(
                _review_record(
                    _review_id(candidate),
                    evidence_by_id[id(candidate)],
                    candidate,
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
            for candidate in ordered
        ]
    review_path.write_text(
        "\n".join(review_lines) + ("\n" if review_lines else ""),
        encoding="utf-8",
    )
    memory_files = sorted(
        path.relative_to(output_dir).as_posix()
        for path in (output_dir / "memories").glob("*.md")
    )
    output_files = [
        daily_path.relative_to(output_dir).as_posix(),
        evidence_path.relative_to(output_dir).as_posix(),
        "manifest.json",
        review_path.relative_to(output_dir).as_posix(),
        *memory_files,
    ]

    manifest = {
        "date": date,
        "generator": "session2memory",
        "version": __version__,
        "counts": {
            "sessions": session_count,
            "messages": message_count,
            "filtered": filtered_count,
            "evidence_records": len(ordered),
            "durable_memories": 0,
            "durable_suggestions": sum(1 for candidate in ordered if candidate.durable),
            "daily_entries": len(ordered),
            "review_entries": len(ordered),
        },
        "output_files": output_files,
        "scanned_tools": sorted(scanned_tools),
        "source_roots": {
            tool: source_roots[tool].as_posix() for tool in sorted(source_roots)
        },
        "skipped": sorted(skipped),
        "workspaces": {
            workspace_id: _workspace_record(workspace)
            for workspace_id, workspace in sorted(workspaces.items())
        },
        "hks": _hks_manifest_hint(date),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _clear_managed_output(output_dir: Path) -> None:
    for directory in ("daily", "evidence", "review"):
        shutil.rmtree(output_dir / directory, ignore_errors=True)
    (output_dir / "manifest.json").unlink(missing_ok=True)


def _candidate_sort_key(
    candidate: MemoryCandidate,
) -> tuple[str, str, str, str, int, str, str, int, str, str, bool]:
    return (
        candidate.workspace_id,
        candidate.kind,
        candidate.text,
        candidate.evidence.session_id,
        candidate.evidence.message_start,
        candidate.evidence.tool,
        candidate.evidence.source_path.as_posix(),
        candidate.evidence.message_end,
        candidate.evidence.digest,
        candidate.evidence.workspace_path.as_posix()
        if candidate.evidence.workspace_path
        else "",
        candidate.durable,
    )


def _daily_markdown(
    date: str,
    candidates: Sequence[MemoryCandidate],
    evidence_by_id: Mapping[int, str],
    workspaces: Mapping[str, WorkspaceIdentity],
    scanned_tools: Sequence[str],
) -> str:
    grouped = _group_by_workspace(candidates)
    workspace_ids = sorted(set(workspaces) | set(grouped))
    lines = [
        "---",
        "hks_type: session_daily",
        f"date: {date}",
        "generator: session2memory",
        "source_domain: coding_session",
        f"tools: {_yaml_flow_list(_ordered_tools(scanned_tools))}",
        "schema_version: 1",
        "---",
        f"# {date}",
        "",
        "## Summary",
        f"- entries: {len(candidates)}",
        f"- workspaces: {len(workspace_ids)}",
        f"- durable_suggestions: {sum(1 for candidate in candidates if candidate.durable)}",
        "",
        "## Workspaces",
    ]

    if workspace_ids:
        for workspace_id in workspace_ids:
            workspace_candidates = grouped.get(workspace_id, [])
            memory_kinds = sorted({candidate.kind for candidate in workspace_candidates})
            tools = _ordered_tools(
                {candidate.evidence.tool for candidate in workspace_candidates}
            )
            lines.extend(
                [
                    f"### {workspace_id}",
                    f"- entries: {len(workspace_candidates)}",
                    f"- memory_kinds: {', '.join(memory_kinds) if memory_kinds else 'none'}",
                    f"- tools: {', '.join(tools) if tools else 'none'}",
                    "",
                ]
            )
        lines.pop()
    else:
        lines.append("_No workspaces._")

    lines.extend(["", "## Entries"])
    for candidate in candidates:
        lines.append(_daily_entry_line(candidate, evidence_by_id[id(candidate)]))
    if not candidates:
        lines.append("_No entries._")
    return "\n".join(lines).rstrip() + "\n"


def _workspace_markdown(
    workspace: WorkspaceIdentity | None,
    workspace_id: str,
    candidates: Sequence[MemoryCandidate],
    evidence_by_id: Mapping[int, str],
) -> str:
    lines = [f"# {workspace_id}", ""]
    if workspace:
        lines.extend(
            [
                f"- canonical_path: {workspace.canonical_path.as_posix()}",
                f"- repo_root: {workspace.repo_root.as_posix() if workspace.repo_root else ''}",
                f"- opened_cwd: {workspace.opened_cwd.as_posix() if workspace.opened_cwd else ''}",
                "",
            ]
        )
    for candidate in candidates:
        evidence_id = evidence_by_id[id(candidate)]
        lines.append(f"- [{candidate.kind}] {candidate.text} (evidence: {evidence_id})")
    return "\n".join(lines).rstrip() + "\n"


def _group_by_workspace(
    candidates: Sequence[MemoryCandidate],
) -> dict[str, list[MemoryCandidate]]:
    grouped: dict[str, list[MemoryCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.workspace_id, []).append(candidate)
    return dict(sorted(grouped.items()))


def _evidence_record(
    evidence_id: str,
    candidate: MemoryCandidate,
    *,
    agentic_os_index: AgenticOsIndex | None = None,
) -> dict[str, Any]:
    record = candidate.evidence.to_json()
    record.update(
        {
            "id": evidence_id,
            "evidence_id": evidence_id,
            "kind": candidate.kind,
            "workspace_id": candidate.workspace_id,
            "durable": candidate.durable,
        }
    )
    if agentic_os_index is not None:
        record = agentic_os_index.enrich_evidence_record(
            record,
            source_path=candidate.evidence.source_path,
        )
    return record


def _maybe_redact(text: str, *, redact_exports: bool, home: Path) -> str:
    if not redact_exports:
        return text
    return redact_text(text, home=home)


def _review_record(
    review_id: str,
    evidence_id: str,
    candidate: MemoryCandidate,
    *,
    status: str = "pending",
    review_note: str = "",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": review_id,
        "status": status,
        "kind": candidate.kind,
        "text": candidate.text,
        "workspace_id": candidate.workspace_id,
        "evidence_id": evidence_id,
        "source": _source_record(candidate),
        "durable_suggestion": candidate.durable,
        "review_note": review_note,
        "extraction": candidate.extraction,
    }
    if candidate.confidence is not None:
        record["confidence"] = candidate.confidence
    if candidate.evidence_quote:
        record["evidence_quote"] = candidate.evidence_quote
    return record


def _source_record(candidate: MemoryCandidate) -> dict[str, str | int]:
    return {
        "tool": candidate.evidence.tool,
        "session_id": candidate.evidence.session_id,
        "message_start": candidate.evidence.message_start,
        "message_end": candidate.evidence.message_end,
    }


def _source_summary(candidate: MemoryCandidate) -> str:
    source = _source_record(candidate)
    return (
        f"source: {source['tool']}, session: {source['session_id']}, "
        f"lines: {source['message_start']}-{source['message_end']}"
    )


def _daily_entry_line(candidate: MemoryCandidate, evidence_id: str) -> str:
    return f"- [{candidate.kind}] {candidate.text} {_entry_metadata(candidate, evidence_id)}"


def _entry_metadata(candidate: MemoryCandidate, evidence_id: str) -> str:
    return (
        "{"
        f"workspace_id={candidate.workspace_id} "
        f"memory_kind={candidate.kind} "
        f"tool={candidate.evidence.tool} "
        f"session_id={candidate.evidence.session_id} "
        f"evidence_id={evidence_id} "
        f"lines={candidate.evidence.message_start}-{candidate.evidence.message_end}"
        "}"
    )


def _review_id(candidate: MemoryCandidate) -> str:
    raw = "\0".join(
        (
            candidate.workspace_id,
            candidate.kind,
            candidate.text,
            candidate.evidence.tool,
            candidate.evidence.session_id,
            candidate.evidence.source_path.as_posix(),
            str(candidate.evidence.message_start),
            str(candidate.evidence.message_end),
            candidate.evidence.digest,
        )
    )
    return "r" + sha256(raw.encode("utf-8")).hexdigest()[:16]


def _workspace_record(workspace: WorkspaceIdentity) -> dict[str, str | None]:
    return {
        "canonical_path": workspace.canonical_path.as_posix(),
        "repo_root": workspace.repo_root.as_posix() if workspace.repo_root else None,
        "opened_cwd": workspace.opened_cwd.as_posix() if workspace.opened_cwd else None,
        "tool_workspace_id": workspace.tool_workspace_id,
    }


def _hks_manifest_hint(date: str) -> dict[str, str | list[str]]:
    return {
        "source_type": "session_memory",
        "primary_documents": [f"daily/{date}.md"],
        "metadata_fields": ["date", "workspace_id", "tool", "memory_kind"],
    }


def _ordered_tools(tools: Sequence[str] | set[str]) -> list[str]:
    tool_set = {tool for tool in tools if tool}
    ordered = [tool for tool in P0_TOOL_ORDER if tool in tool_set]
    ordered.extend(sorted(tool_set - set(P0_TOOL_ORDER)))
    return ordered


def _yaml_flow_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(values) + "]"
