from __future__ import annotations

from pathlib import Path

from session2memory.models import digest_text


def source_file_fingerprint(path: Path) -> tuple[str, int]:
    try:
        stat = path.stat()
    except OSError:
        return "", 0
    try:
        payload = path.read_bytes()
    except OSError:
        return "", stat.st_mtime_ns
    return digest_text(payload.decode("utf-8", errors="replace")), stat.st_mtime_ns
