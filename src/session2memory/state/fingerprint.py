from __future__ import annotations

from hashlib import sha256
from pathlib import Path

_CHUNK = 1 << 20


def source_file_fingerprint(path: Path) -> tuple[str, int]:
    """Digest of the file bytes, streamed so multi-GB sessions are never held in memory.

    For valid UTF-8 files this equals the previous ``digest_text(decoded)`` value, so
    fingerprints already stored in ``source_files`` stay valid.
    """
    try:
        stat = path.stat()
    except OSError:
        return "", 0
    digest = sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
    except OSError:
        return "", stat.st_mtime_ns
    return "sha256:" + digest.hexdigest(), stat.st_mtime_ns
