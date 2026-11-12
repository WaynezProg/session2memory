from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol, cast

from session2memory.models import EvidencePointer, Role, SessionMessage, SessionRecord, digest_text

ZSTD_SUFFIXES = (".zst", ".zstd")


class SessionAdapter(Protocol):
    tool: str

    def iter_sessions(self, date: str) -> Iterator[SessionRecord]:
        raise NotImplementedError


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def is_zstd_path(path: Path) -> bool:
    return path.name.endswith(ZSTD_SUFFIXES)


def jsonl_stem(path: Path) -> str:
    """File name without ``.jsonl`` / ``.jsonl.zst`` / ``.jsonl.zstd``."""
    name = path.name
    for suffix in ZSTD_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name[: -len(".jsonl")] if name.endswith(".jsonl") else name


def iter_text_lines(path: Path) -> Iterator[str]:
    """Yield decoded lines of a plain or zstd-compressed text file without unpacking to disk.

    Compressed files are streamed through the ``zstd`` executable on stdin, so the source
    path is only ever opened read-only (and symlinks work).
    """
    if not is_zstd_path(path):
        with path.open("r", encoding="utf-8") as handle:
            yield from handle
        return
    executable = shutil.which("zstd")
    if executable is None:
        raise ValueError(
            f"{path.as_posix()}: zstd executable not found; cannot read compressed file"
        )
    with path.open("rb") as source:
        process = subprocess.Popen(
            [executable, "-dc"],
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        completed = False
        try:
            for raw in process.stdout:
                yield raw.decode("utf-8")
            completed = True
        finally:
            process.stdout.close()
            if not completed:
                process.kill()
            returncode = process.wait()
        if returncode != 0:
            raise ValueError(f"{path.as_posix()}: zstd exited with status {returncode}")


def read_jsonl(
    path: Path, *, on_line: Callable[[str], None] | None = None
) -> Iterator[tuple[int, dict[str, object]]]:
    """Yield ``(line_number, object)``. ``on_line`` sees every non-empty stripped line."""
    for line_number, line in enumerate(iter_text_lines(path), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if on_line is not None:
            on_line(stripped)
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{path.as_posix()}:{line_number}: malformed JSON: {exc.msg}"
            ) from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"{path.as_posix()}:{line_number}: expected JSON object")
        yield line_number, cast("dict[str, object]", loaded)


class ContentDigest:
    """Running digest over the non-empty lines of a session, independent of its encoding."""

    def __init__(self) -> None:
        self._hash = sha256()
        self.record_count = 0

    def add(self, line: str) -> None:
        self.record_count += 1
        self._hash.update(line.encode("utf-8"))
        self._hash.update(b"\n")

    def value(self) -> str:
        return "sha256:" + self._hash.hexdigest()


def text_from_content(content: object) -> str:
    """Join the text of a ``str`` or a list of ``{"text": ...}`` blocks."""
    if isinstance(content, str):
        return content
    texts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    texts.append(text)
    return "\n".join(texts)


def skipped_file_reason(tool: str, path: Path, exc: Exception) -> str:
    return f"{tool}: skipped {path.as_posix()}: {exc}"


def timestamps_touch_date(date: str, *values: datetime | None) -> bool:
    return any(value is not None and value.date().isoformat() == date for value in values)


def file_modified_on_date(path: Path, date: str) -> bool:
    try:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return False
    return modified_at.date().isoformat() == date


def file_session_touches_date(record: SessionRecord, date: str) -> bool:
    return timestamps_touch_date(
        date,
        record.started_at,
        record.updated_at,
    ) or file_modified_on_date(record.source_path, date)


def jsonl_candidate_paths(
    root: Path,
    *,
    date: str,
    primary_patterns: Sequence[str],
    modified_patterns: Sequence[str] = ("**/*.jsonl",),
    exclude: Callable[[Path], bool] | None = None,
) -> list[Path]:
    paths: set[Path] = set()
    for pattern in primary_patterns:
        paths.update(root.glob(pattern))

    if root.exists():
        for pattern in modified_patterns:
            paths.update(
                path for path in root.glob(pattern) if file_modified_on_date(path, date)
            )

    if exclude:
        paths = {path for path in paths if not exclude(path)}
    return sorted(paths)


def normalize_role(role: str) -> Role:
    if role in {"user", "assistant", "tool", "system"}:
        return cast("Role", role)
    return "unknown"


def make_message(
    *,
    tool: str,
    session_id: str,
    source_path: Path,
    line_number: int,
    role: str,
    text: str,
    timestamp: datetime | None,
    cwd: Path | None,
    locator: str | None = None,
) -> SessionMessage:
    pointer = EvidencePointer(
        tool=tool,
        session_id=session_id,
        source_path=source_path,
        message_start=line_number,
        message_end=line_number,
        workspace_path=cwd,
        digest=digest_text(text),
    )
    return SessionMessage(
        index=line_number,
        role=normalize_role(role),
        text=text,
        timestamp=timestamp,
        raw_pointer=pointer,
        locator=locator,
    )
