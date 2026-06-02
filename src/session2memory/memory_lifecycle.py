from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from session2memory.state.store import StateStore


class MemoryLifecycleError(Exception):
    pass


@dataclass(frozen=True)
class MemoryLifecycleResult:
    memory_entry_id: str
    status: str
    exported_workspaces: tuple[str, ...] = ()
    synced_targets: int = 0


def open_state_store(output_dir: Path) -> StateStore | None:
    db_path = output_dir / "session2memory.db"
    if not db_path.exists():
        return None
    return StateStore.open(db_path, output_dir=output_dir)


def revoke_memory(*, output_dir: Path, memory_entry_id: str) -> MemoryLifecycleResult:
    store = _require_store(output_dir)
    try:
        entry = store.get_memory_entry(memory_entry_id=memory_entry_id)
        if entry is None:
            raise MemoryLifecycleError(f"Memory entry not found: {memory_entry_id}")
        workspace_id = str(entry["workspace_id"])
        store.set_memory_status(memory_entry_id=memory_entry_id, status="revoked")
        exported = store.export_memory_entries(output_dir=output_dir)
    finally:
        store.close()
    synced = _resync_workspace(output_dir=output_dir, workspace_id=workspace_id)
    return MemoryLifecycleResult(
        memory_entry_id=memory_entry_id,
        status="revoked",
        exported_workspaces=tuple(exported),
        synced_targets=synced,
    )


def supersede_memory(
    *,
    output_dir: Path,
    old_id: str,
    new_id: str,
) -> MemoryLifecycleResult:
    store = _require_store(output_dir)
    try:
        old_entry = store.get_memory_entry(memory_entry_id=old_id)
        if old_entry is None:
            raise MemoryLifecycleError(f"Memory entry not found: {old_id}")
        new_entry = store.get_memory_entry(memory_entry_id=new_id)
        if new_entry is None:
            raise MemoryLifecycleError(f"Memory entry not found: {new_id}")
        workspace_id = str(new_entry["workspace_id"])
        store.set_memory_status(memory_entry_id=old_id, status="superseded")
        store.set_memory_status(memory_entry_id=new_id, status="active")
        store.set_memory_supersedes(memory_entry_id=new_id, supersedes_id=old_id)
        exported = store.export_memory_entries(output_dir=output_dir)
    finally:
        store.close()
    synced = _resync_workspace(output_dir=output_dir, workspace_id=workspace_id)
    return MemoryLifecycleResult(
        memory_entry_id=new_id,
        status="superseded",
        exported_workspaces=tuple(exported),
        synced_targets=synced,
    )


def _require_store(output_dir: Path) -> StateStore:
    store = open_state_store(output_dir)
    if store is None:
        raise MemoryLifecycleError(
            f"No state database at {(output_dir / 'session2memory.db').as_posix()}"
        )
    return store


def _resync_workspace(*, output_dir: Path, workspace_id: str) -> int:
    from session2memory.sync_back import sync_recorded_targets

    return len(sync_recorded_targets(output_dir=output_dir, workspace_ids=(workspace_id,)))
