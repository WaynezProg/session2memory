"""One unattended run: scan, compress and archive, curate memory, export, index.

Each step is independent and reports what it did; a failing step is recorded and the
others still run. A lock file keeps two runs from overlapping.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from session2memory.auto.curate import Runner, curate, default_runner
from session2memory.auto.export import export
from session2memory.auto.files import archive_claude, compress_codex_rollouts, purge_holding
from session2memory.auto.scan import DEFAULT_MAX_FILE_BYTES, ScanSources, scan
from session2memory.auto.store import AutoStore, now_iso
from session2memory.models import SourceHome
from session2memory.state.store import StateStore

ALL_STEPS = ("scan", "compress", "archive", "curate", "export", "index")
DB_NAME = "session2memory.db"
LAST_SCAN_KEY = "last_scan_at"
SCAN_OVERLAP = timedelta(hours=2)


@dataclass(frozen=True)
class AutoConfig:
    store_dir: Path
    sources: ScanSources
    # Codex homes whose cold rollouts this pipeline compresses (homes Codex itself does
    # not maintain). The main home is left to Codex.
    compress_homes: tuple[SourceHome, ...] = ()
    steps: Sequence[str] = ALL_STEPS
    since_days: float = 30.0
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_llm_calls: int = 20
    llm_command: str | None = None
    compress_min_age_days: float = 14.0
    hold_days: float = 3.0
    archive_min_age_days: float = 10.0
    stale_after_days: int = 120
    # Sessions that waited longer than this for curation are dropped from the queue.
    curate_max_age_days: float = 21.0
    # Scopes whose sessions are curated; ``None`` curates every scope.
    curate_scopes: tuple[str, ...] | None = None
    index_command: Sequence[str] = ()
    index_env: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False

    @property
    def db_path(self) -> Path:
        return self.store_dir / DB_NAME

    @property
    def memory_dir(self) -> Path:
        return self.store_dir / "memory"

    @property
    def archive_dir(self) -> Path:
        return self.store_dir / "archive" / "claude"

    @property
    def holding_dir(self) -> Path:
        return self.store_dir / "holding"


def _index(config: AutoConfig) -> dict[str, object]:
    """Hand the exported tree to an external indexer (HKS ``ks update``)."""
    if not config.index_command:
        return {"skipped": "no index command configured"}
    argv = [
        part.replace("{memory_dir}", config.memory_dir.as_posix()) for part in config.index_command
    ]
    started = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=1800,
            env={**os.environ, **config.index_env},
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc)[:200]}
    return {
        "exit_code": done.returncode,
        "seconds": round(time.monotonic() - started, 1),
        "stderr_tail": done.stderr[-300:] if done.returncode else "",
    }


def _open_state(config: AutoConfig) -> StateStore:
    """The persistent database, or for a dry run a throwaway in-memory copy of it."""
    if not config.dry_run:
        return StateStore.open(config.db_path)
    memory = sqlite3.connect(":memory:")
    if config.db_path.is_file():
        disk = sqlite3.connect(f"file:{config.db_path.as_posix()}?mode=ro", uri=True)
        try:
            disk.backup(memory)
        finally:
            disk.close()
    state = StateStore(memory)
    state._apply_schema()
    return state


def run_auto(
    config: AutoConfig, *, runner: Runner | None = None, now: datetime | None = None
) -> dict[str, object]:
    """Run the configured steps once and return the report (also stored in the database)."""
    now = now or datetime.now(UTC)
    report: dict[str, object] = {"started_at": now_iso(now), "dry_run": config.dry_run}
    if config.dry_run:
        # A rehearsal leaves the store exactly as it is: no directory, mode or lock change.
        lock = open(os.devnull, "w")  # noqa: SIM115 - closed in the finally below
    else:
        # The store holds digests, summaries and archived transcripts: owner only.
        config.store_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(config.store_dir, 0o700)
        lock = (config.store_dir / ".lock").open("w")
    try:
        try:
            if not config.dry_run:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            report["skipped"] = "another run holds the lock"
            return report
        state = _open_state(config)
        store = AutoStore(state._connection)
        run_id = uuid.uuid4().hex[:12]
        report["run_id"] = run_id
        store.start_run(run_id)
        try:
            for step in config.steps:
                started = time.monotonic()
                try:
                    result = _run_step(step, config, state, store, runner, run_id, now)
                except Exception as exc:  # noqa: BLE001 - one step must not stop the others
                    result = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                result["seconds"] = round(time.monotonic() - started, 1)
                report[step] = result
            report["totals"] = store.counts()
            report["finished_at"] = now_iso()
            store.finish_run(run_id, report)
        finally:
            state.close()
    finally:
        lock.close()
    return report


def _run_step(
    step: str,
    config: AutoConfig,
    state: StateStore,
    store: AutoStore,
    runner: Runner | None,
    run_id: str,
    now: datetime,
) -> dict[str, object]:
    if step == "scan":
        previous = store.get_meta(LAST_SCAN_KEY)
        since = now - timedelta(days=config.since_days)
        if previous:
            since = max(since, datetime.fromisoformat(previous) - SCAN_OVERLAP)
        result = scan(
            state.sources, store, config.sources, since=since, max_bytes=config.max_file_bytes
        ).as_dict()
        result["since"] = now_iso(since)
        # A source that could not be read keeps its chance: the next run starts from the
        # same point again (bounded by since_days).
        if not config.dry_run and not result.get("read_errors"):
            store.set_meta(LAST_SCAN_KEY, now_iso(now))
        return result
    if step == "compress":
        done = compress_codex_rollouts(
            store,
            config.compress_homes,
            config.holding_dir,
            now=now,
            min_age_days=config.compress_min_age_days,
            dry_run=config.dry_run,
            registry=state.sources,
        ).as_dict()
        done["purge"] = purge_holding(
            store, now=now, hold_days=config.hold_days, dry_run=config.dry_run
        ).as_dict()
        return done
    if step == "archive":
        if config.sources.claude_root is None:
            return {"skipped": "no Claude root configured"}
        return archive_claude(
            store,
            config.sources.claude_root,
            config.archive_dir,
            now=now,
            min_age_days=config.archive_min_age_days,
            dry_run=config.dry_run,
            registry=state.sources,
        ).as_dict()
    if step == "curate":
        if config.dry_run:
            return {"skipped": "dry run", "sessions_pending": len(store.pending_sessions())}
        result = curate(
            store,
            runner or default_runner(config.llm_command),
            max_calls=config.max_llm_calls,
            run_id=run_id,
            now=now,
            max_age_days=config.curate_max_age_days,
            scopes=config.curate_scopes,
        ).as_dict()
        result["memories_stale"] = store.mark_stale(
            older_than_days=config.stale_after_days, run_id=run_id, now=now
        )
        return result
    if step == "export":
        if config.dry_run:
            return {"skipped": "dry run"}
        return export(store, config.memory_dir).as_dict()
    if step == "index":
        return {"skipped": "dry run"} if config.dry_run else _index(config)
    return {"error": f"unknown step {step}"}
