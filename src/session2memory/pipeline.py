from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from session2memory.agentic_os_index import AgenticOsIndex
from session2memory.extraction import extract_candidates
from session2memory.filtering import is_noise
from session2memory.llm_extract import (
    LlmExtractBackend,
    items_to_candidates,
    merge_llm_candidates,
)
from session2memory.models import MemoryCandidate, SessionRecord, WorkspaceIdentity
from session2memory.state.fingerprint import source_file_fingerprint
from session2memory.state.store import StateStore
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
    agentic_os_index: AgenticOsIndex | None = None,
    agentic_os_sessions_only: bool = False,
    llm_backend: LlmExtractBackend | None = None,
    state_store: StateStore | None = None,
) -> tuple[int, int]:
    session_count = 0
    message_count = 0
    filtered_count = 0
    candidates: list[MemoryCandidate] = []
    workspaces: dict[str, WorkspaceIdentity] = {}
    skipped: list[str] = []
    workspace_filter = workspace.resolve(strict=False) if workspace else None
    registered_logs: set[Path] | None = None
    if agentic_os_sessions_only and agentic_os_index is not None:
        registered_logs = agentic_os_index.registered_log_paths_for_date(date)

    for tool, adapter in sorted(adapters.items()):
        source_root = source_roots.get(tool)
        if source_root is not None and not source_root.exists():
            skipped.append(f"{tool}: missing source root: {source_root.as_posix()}")
            continue
        for record in adapter.iter_sessions(date):
            if registered_logs is not None:
                log_path = record.source_path.expanduser().resolve(strict=False)
                if log_path not in registered_logs:
                    continue
            resolved_workspace = resolve_workspace(record)
            if workspace_filter and not _matches_workspace(
                record, resolved_workspace, workspace_filter
            ):
                continue
            source_path = record.source_path.expanduser()
            if state_store is not None:
                digest, mtime_ns = source_file_fingerprint(source_path)
                if digest and not state_store.upsert_source_file(
                    tool=record.tool,
                    path=source_path.as_posix(),
                    digest=digest,
                    mtime_ns=mtime_ns,
                ):
                    continue
                state_store.upsert_workspace(resolved_workspace)
            session_count += 1
            message_count += len(record.messages)
            filtered_count += sum(1 for message in record.messages if is_noise(message))
            workspaces[resolved_workspace.workspace_id] = resolved_workspace
            marker_candidates = extract_candidates(record, resolved_workspace)
            llm_candidates = _extract_llm_candidates(
                record,
                resolved_workspace,
                llm_backend,
            )
            session_candidates = marker_candidates + merge_llm_candidates(
                existing=marker_candidates,
                llm_candidates=llm_candidates,
            )
            if state_store is not None:
                for candidate in session_candidates:
                    state_store.upsert_candidate(import_date=date, candidate=candidate)
            else:
                candidates.extend(session_candidates)
        skipped.extend(_adapter_skipped(adapter))

    if state_store is not None:
        if not dry_run:
            state_store.export_output(
                output_dir=output_dir,
                import_date=date,
                scanned_tools=sorted(adapters),
                source_roots=dict(source_roots),
                skipped=skipped,
                session_count=session_count,
                message_count=message_count,
                filtered_count=filtered_count,
                agentic_os_index=agentic_os_index,
            )
        candidate_count = len(state_store.list_candidates_for_date(date))
    else:
        write_output(
            output_dir=output_dir,
            date=date,
            candidates=candidates,
            workspaces=workspaces,
            scanned_tools=sorted(adapters),
            source_roots=source_roots,
            skipped=skipped,
            session_count=session_count,
            message_count=message_count,
            filtered_count=filtered_count,
            dry_run=dry_run,
            agentic_os_index=agentic_os_index,
        )
        candidate_count = len(candidates)
    return session_count, candidate_count


def _matches_workspace(
    record: SessionRecord, resolved_workspace: WorkspaceIdentity, workspace_filter: Path
) -> bool:
    record_cwd = record.cwd.resolve(strict=False) if record.cwd else None
    return workspace_filter in {record_cwd, resolved_workspace.canonical_path}


def _extract_llm_candidates(
    record: SessionRecord,
    workspace: WorkspaceIdentity,
    llm_backend: LlmExtractBackend | None,
) -> list[MemoryCandidate]:
    if llm_backend is None:
        return []
    messages = [message for message in record.messages if not is_noise(message)]
    if not messages:
        return []
    items = llm_backend.extract(messages=messages, workspace_id=workspace.workspace_id)
    return items_to_candidates(
        items=items,
        messages=messages,
        workspace_id=workspace.workspace_id,
    )


def _adapter_skipped(adapter: PipelineAdapter) -> list[str]:
    raw: object = getattr(adapter, "skipped", ())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return []
    return [str(reason) for reason in raw]
