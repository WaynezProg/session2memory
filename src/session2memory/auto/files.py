"""Compression and archival of cold session files.

Two different situations, two different actions:

* **Codex rollouts** in homes Codex does not maintain itself (the per-agent homes OpenClaw
  keeps): Codex reads ``rollout-….jsonl.zst`` wherever it expects ``rollout-….jsonl`` and
  re-inflates it on resume, so a cold rollout is compressed **in place** under exactly the
  name Codex's own maintenance would give it. The original is moved to a holding directory
  first and only removed after a holding period.
* **Claude Code transcripts**: Claude Code cannot read a compressed transcript and deletes
  transcripts on its own after ``cleanupPeriodDays``. These are **copied** into a
  compressed archive and the original is left alone, so the history survives the cleanup.

Every compressed file is decompressed again and compared by SHA-256 before anything else
happens to the original. Nothing is done to a file that changed recently.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from session2memory.auto.store import AutoStore, now_iso
from session2memory.models import SourceHome

CHUNK = 1 << 20
ARCHIVE_ACTION = "archive"
COMPRESS_ACTION = "compress"


@dataclass
class FilesReport:
    examined: int = 0
    done: int = 0
    skipped_recent: int = 0
    skipped_done: int = 0
    skipped_busy: int = 0
    failed: int = 0
    source_bytes: int = 0
    target_bytes: int = 0
    purged: int = 0
    purged_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)

    def fail(self, path: Path, why: str) -> None:
        self.failed += 1
        if len(self.errors) < 20:
            self.errors.append(f"{path.as_posix()}: {why}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_of_decompressed(path: Path, zstd: str) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        process = subprocess.Popen([zstd, "-dc"], stdin=source, stdout=subprocess.PIPE)
        assert process.stdout is not None
        while chunk := process.stdout.read(CHUNK):
            digest.update(chunk)
        process.stdout.close()
        if process.wait() != 0:
            raise ValueError("zstd could not decompress its own output")
    return digest.hexdigest()


def compress_verified(source: Path, target: Path, *, level: int, zstd: str) -> tuple[str, int]:
    """Write ``target`` from ``source`` and prove it decompresses to the same bytes.

    Returns ``(sha256 of the original, size of the compressed file)``. ``target`` only
    appears once verification has passed.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".s2m-tmp")
    try:
        with source.open("rb") as handle, temporary.open("wb") as output:
            subprocess.run(
                [zstd, "-q", f"-{level}", "-T0", "-c"], stdin=handle, stdout=output, check=True
            )
        original = _sha256_file(source)
        if _sha256_of_decompressed(temporary, zstd) != original:
            raise ValueError("compressed copy does not match the original")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return original, target.stat().st_size


def _is_cold(path: Path, now: datetime, min_age_days: float) -> bool:
    return path.stat().st_mtime <= (now - timedelta(days=min_age_days)).timestamp()


def archive_claude(
    store: AutoStore,
    projects_root: Path,
    archive_root: Path,
    *,
    now: datetime,
    min_age_days: float,
    level: int = 10,
    dry_run: bool = False,
) -> FilesReport:
    """Copy cold Claude transcripts (including subagent ones) into a compressed archive."""
    report = FilesReport()
    zstd = shutil.which("zstd")
    if zstd is None or not projects_root.is_dir():
        report.errors.append("zstd executable or Claude projects directory not found")
        return report
    for path in sorted(projects_root.rglob("*.jsonl")):
        try:
            stat = path.stat()
            if not _is_cold(path, now, min_age_days):
                report.skipped_recent += 1
                continue
            report.examined += 1
            known = store.file_action(path)
            if known and known[1:3] == (stat.st_size, stat.st_mtime_ns) and Path(known[0]).exists():
                report.skipped_done += 1
                continue
            target = archive_root / path.relative_to(projects_root).with_name(path.name + ".zst")
            if dry_run:
                report.done += 1
                report.source_bytes += stat.st_size
                continue
            digest, size = compress_verified(path, target, level=level, zstd=zstd)
            store.record_file_action(
                path=path,
                action=ARCHIVE_ACTION,
                target=target,
                source_bytes=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                target_bytes=size,
                content_sha256=digest,
                now=now,
            )
            report.done += 1
            report.source_bytes += stat.st_size
            report.target_bytes += size
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            report.fail(path, str(exc)[:160])
    return report


def _busy_threads(home_root: Path) -> set[str]:
    """Thread ids Codex currently holds a writer lock for in this home."""
    locks = home_root.parent / "thread-writer-locks"
    if not locks.is_dir():
        return set()
    return {entry.name.split(".")[0] for entry in locks.iterdir()}


def compress_codex_rollouts(
    store: AutoStore,
    homes: tuple[SourceHome, ...],
    holding_root: Path,
    *,
    now: datetime,
    min_age_days: float,
    level: int = 3,
    dry_run: bool = False,
) -> FilesReport:
    """Compress cold ``.jsonl`` rollouts in place, keeping each original in holding."""
    report = FilesReport()
    zstd = shutil.which("zstd")
    if zstd is None:
        report.errors.append("zstd executable not found")
        return report
    for home in homes:
        if not home.root.is_dir():
            continue
        busy = _busy_threads(home.root)
        for path in sorted(home.root.rglob("rollout-*.jsonl")):
            try:
                stat = path.stat()
                if not _is_cold(path, now, min_age_days):
                    report.skipped_recent += 1
                    continue
                report.examined += 1
                target = path.with_name(path.name + ".zst")
                if any(thread and thread in path.name for thread in busy) or target.exists():
                    report.skipped_busy += 1
                    continue
                if dry_run:
                    report.done += 1
                    report.source_bytes += stat.st_size
                    continue
                digest, size = compress_verified(path, target, level=level, zstd=zstd)
                if path.stat().st_mtime_ns != stat.st_mtime_ns:  # written to meanwhile
                    target.unlink()
                    report.skipped_busy += 1
                    continue
                os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                held = holding_root / home.home_id.replace("/", "_") / path.relative_to(home.root)
                held.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path.as_posix(), held.as_posix())
                store.record_file_action(
                    path=path,
                    action=COMPRESS_ACTION,
                    target=held,
                    source_bytes=stat.st_size,
                    source_mtime_ns=stat.st_mtime_ns,
                    target_bytes=size,
                    content_sha256=digest,
                    now=now,
                )
                report.done += 1
                report.source_bytes += stat.st_size
                report.target_bytes += size
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                report.fail(path, str(exc)[:160])
    return report


def purge_holding(
    store: AutoStore, *, now: datetime, hold_days: float, dry_run: bool = False
) -> FilesReport:
    """Remove held originals whose compressed replacement is still in place and intact."""
    report = FilesReport()
    zstd = shutil.which("zstd")
    cutoff = now_iso(now - timedelta(days=hold_days))
    for raw_path, _done_at in store.file_actions(COMPRESS_ACTION, unpurged_before=cutoff):
        path = Path(raw_path)
        action = store.file_action(path)
        if action is None:
            continue
        held = Path(action[0])
        compressed = path.with_name(path.name + ".zst")
        report.examined += 1
        if not held.exists():
            store.mark_purged(raw_path)
            continue
        # The session may have been resumed (Codex re-inflates it): then the held copy is
        # simply obsolete. Otherwise the compressed file must still verify before the
        # original goes.
        resumed = path.exists()
        try:
            if not resumed and (
                zstd is None
                or not compressed.exists()
                or _sha256_of_decompressed(compressed, zstd) != _sha256_file(held)
            ):
                report.fail(path, "compressed file missing or changed; held original kept")
                continue
            size = held.stat().st_size
            if not dry_run:
                held.unlink()
                store.mark_purged(raw_path)
            report.purged += 1
            report.purged_bytes += size
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            report.fail(path, str(exc)[:160])
    return report


def restore_compressed(store: AutoStore, path: Path) -> bool:
    """Undo one in-place compression while its original is still held."""
    action = store.file_action(path)
    if action is None or action[3] is not None:
        return False
    held = Path(action[0])
    if not held.exists() or path.exists():
        return False
    shutil.move(held.as_posix(), path.as_posix())
    path.with_name(path.name + ".zst").unlink(missing_ok=True)
    store.mark_purged(path.as_posix())
    return True
