from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Protocol

from session2memory.extraction import extract_candidates
from session2memory.filtering import is_noise
from session2memory.models import MemoryCandidate, SessionRecord, WorkspaceIdentity
from session2memory.workspace import resolve_workspace
from session2memory.writer import write_output


class PipelineAdapter(Protocol):
    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        raise NotImplementedError


def run_pipeline(
    *,
    adapters: Mapping[str, PipelineAdapter],
    output_dir: Path,
    date: str,
    source_roots: Mapping[str, Path],
    dry_run: bool,
    workspace: Path | None = None,
) -> tuple[int, int]:
    session_count = 0
    message_count = 0
    filtered_count = 0
    candidates: list[MemoryCandidate] = []
    workspaces: dict[str, WorkspaceIdentity] = {}
    workspace_filter = workspace.resolve(strict=False) if workspace else None

    for _tool, adapter in sorted(adapters.items()):
        for record in adapter.iter_sessions(date):
            resolved_workspace = resolve_workspace(record)
            if workspace_filter and not _matches_workspace(
                record, resolved_workspace, workspace_filter
            ):
                continue
            session_count += 1
            message_count += len(record.messages)
            filtered_count += sum(1 for message in record.messages if is_noise(message))
            workspaces[resolved_workspace.workspace_id] = resolved_workspace
            candidates.extend(extract_candidates(record, resolved_workspace))

    write_output(
        output_dir=output_dir,
        date=date,
        candidates=candidates,
        workspaces=workspaces,
        scanned_tools=sorted(adapters),
        source_roots=source_roots,
        skipped=[],
        session_count=session_count,
        message_count=message_count,
        filtered_count=filtered_count,
        dry_run=dry_run,
    )
    return session_count, len(candidates)


def _matches_workspace(
    record: SessionRecord, resolved_workspace: WorkspaceIdentity, workspace_filter: Path
) -> bool:
    record_cwd = record.cwd.resolve(strict=False) if record.cwd else None
    return workspace_filter in {record_cwd, resolved_workspace.canonical_path}
