from __future__ import annotations

import re
import subprocess
from hashlib import sha256
from pathlib import Path

from session2memory.models import SessionRecord, WorkspaceIdentity


def _slug_base(path: Path) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", path.name.lower()).strip("-")
    return cleaned or "workspace"


def _short_digest(path: Path) -> str:
    return sha256(path.as_posix().encode("utf-8")).hexdigest()[:8]


def find_git_root(cwd: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def resolve_workspace(record: SessionRecord) -> WorkspaceIdentity:
    opened_cwd = record.cwd.resolve(strict=False) if record.cwd else None
    source_parent = record.source_path.parent.resolve(strict=False)
    if opened_cwd and opened_cwd.exists():
        repo_root = find_git_root(opened_cwd)
        canonical = repo_root or opened_cwd
    elif opened_cwd:
        repo_root = None
        canonical = opened_cwd
    else:
        repo_root = None
        canonical = source_parent
    workspace_id = f"{_slug_base(canonical)}-{_short_digest(canonical)}"
    return WorkspaceIdentity(
        workspace_id=workspace_id,
        canonical_path=canonical,
        repo_root=repo_root,
        opened_cwd=opened_cwd,
        tool_workspace_id=record.tool_workspace_id,
    )
