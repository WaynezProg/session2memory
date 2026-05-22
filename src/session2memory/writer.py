from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from session2memory import __version__
from session2memory.models import MemoryCandidate, WorkspaceIdentity


def write_output(
    *,
    output_dir: Path,
    date: str,
    candidates: Sequence[MemoryCandidate],
    workspaces: Mapping[str, WorkspaceIdentity],
    scanned_tools: Sequence[str],
    source_roots: Mapping[str, Path],
    skipped: Sequence[str],
    session_count: int,
    message_count: int,
    filtered_count: int,
    dry_run: bool,
) -> None:
    if dry_run:
        return

    ordered = sorted(candidates, key=_candidate_sort_key)
    evidence_by_id = {id(candidate): f"e{index:06d}" for index, candidate in enumerate(ordered, 1)}

    _clear_managed_output(output_dir)
    (output_dir / "daily").mkdir(parents=True, exist_ok=True)
    (output_dir / "memories").mkdir(parents=True, exist_ok=True)
    (output_dir / "evidence").mkdir(parents=True, exist_ok=True)

    (output_dir / "daily" / f"{date}.md").write_text(
        _daily_markdown(date, ordered, evidence_by_id),
        encoding="utf-8",
    )
    for workspace_id, workspace_candidates in _group_by_workspace(ordered).items():
        (output_dir / "memories" / f"{workspace_id}.md").write_text(
            _workspace_markdown(
                workspaces.get(workspace_id),
                workspace_id,
                workspace_candidates,
                evidence_by_id,
            ),
            encoding="utf-8",
        )

    evidence_lines = [
        json.dumps(
            _evidence_record(evidence_by_id[id(candidate)], candidate),
            ensure_ascii=False,
            sort_keys=True,
        )
        for candidate in ordered
    ]
    (output_dir / "evidence" / "index.jsonl").write_text(
        "\n".join(evidence_lines) + ("\n" if evidence_lines else ""),
        encoding="utf-8",
    )

    manifest = {
        "date": date,
        "generator": "session2memory",
        "version": __version__,
        "counts": {
            "sessions": session_count,
            "messages": message_count,
            "filtered": filtered_count,
            "evidence_records": len(candidates),
            "durable_memories": sum(1 for candidate in candidates if candidate.durable),
        },
        "scanned_tools": sorted(scanned_tools),
        "source_roots": {
            tool: source_roots[tool].as_posix() for tool in sorted(source_roots)
        },
        "skipped": sorted(skipped),
        "workspaces": {
            workspace_id: _workspace_record(workspace)
            for workspace_id, workspace in sorted(workspaces.items())
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _clear_managed_output(output_dir: Path) -> None:
    for directory in ("daily", "memories", "evidence"):
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
    date: str, candidates: Sequence[MemoryCandidate], evidence_by_id: Mapping[int, str]
) -> str:
    lines = [f"# {date}", ""]
    for candidate in candidates:
        lines.append(
            f"- [{candidate.kind}] {candidate.text} "
            f"(workspace: {candidate.workspace_id}, evidence: {evidence_by_id[id(candidate)]})"
        )
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


def _evidence_record(evidence_id: str, candidate: MemoryCandidate) -> dict[str, Any]:
    record = candidate.evidence.to_json()
    record.update(
        {
            "evidence_id": evidence_id,
            "kind": candidate.kind,
            "workspace_id": candidate.workspace_id,
            "durable": candidate.durable,
        }
    )
    return record


def _workspace_record(workspace: WorkspaceIdentity) -> dict[str, str | None]:
    return {
        "canonical_path": workspace.canonical_path.as_posix(),
        "repo_root": workspace.repo_root.as_posix() if workspace.repo_root else None,
        "opened_cwd": workspace.opened_cwd.as_posix() if workspace.opened_cwd else None,
        "tool_workspace_id": workspace.tool_workspace_id,
    }
