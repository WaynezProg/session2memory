from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from session2memory.memory_entries import MemoryEntry, load_workspace_memory_entries
from session2memory.memory_lifecycle import open_state_store
from session2memory.redaction import redact_text

MARKER_START = "<!-- session2memory:sync-start -->"
MARKER_END = "<!-- session2memory:sync-end -->"
_MARKER_BLOCK_RE = re.compile(
    re.escape(MARKER_START) + r"[\s\S]*?" + re.escape(MARKER_END),
    re.MULTILINE,
)
SECTION_HEADING = "## Session memory (session2memory)"
SECTION_INTRO = (
    "Promoted durable memory from `session2memory`. "
    "Refresh with `uv run session2memory sync`."
)

SYNC_TARGET_IDS = (
    "agents",
    "claude",
    "cursor",
    "codex",
    "openclaw",
    "hermes",
)
DEFAULT_SYNC_TARGETS: tuple[str, ...] = ("agents", "claude", "cursor")


class SyncError(Exception):
    pass


@dataclass(frozen=True)
class SyncWrite:
    target: str
    path: Path
    created: bool
    changed: bool
    entry_count: int


@dataclass(frozen=True)
class SyncResult:
    workspace_id: str
    writes: tuple[SyncWrite, ...]


@dataclass(frozen=True)
class _DestinationPlan:
    target_ids: tuple[str, ...]
    path: Path
    render: Callable[[Sequence[MemoryEntry]], str]
    is_cursor_rule: bool


def sync_workspace_memory(
    *,
    output_dir: Path,
    workspace: Path,
    targets: Sequence[str] | None = None,
    dry_run: bool = False,
    max_entries: int = 80,
    force: bool = False,
    since_last_sync: bool = False,
) -> SyncResult:
    workspace_id = resolve_workspace_id(output_dir=output_dir, workspace=workspace)
    entries = load_workspace_memory_entries(output_dir=output_dir, workspace_id=workspace_id)
    if not entries:
        raise SyncError(
            f"No promoted memory at memories/{workspace_id}.md "
            "(run review promote first)."
        )
    if len(entries) > max_entries:
        entries = entries[-max_entries:]
    store = open_state_store(output_dir)
    plans = _destination_plans(
        workspace=workspace.expanduser().resolve(strict=False),
        workspace_id=workspace_id,
        targets=_normalize_targets(targets),
    )
    writes: list[SyncWrite] = []
    for plan in plans:
        rendered_body = redact_text(
            plan.render(entries),
            home=Path.home(),
        )
        body_hash = sha256(rendered_body.encode("utf-8")).hexdigest()
        if (
            since_last_sync
            and not force
            and store is not None
            and store.get_sync_hash(
                workspace_id=workspace_id,
                target=",".join(plan.target_ids),
                dest_path=plan.path.as_posix(),
            )
            == body_hash
        ):
            writes.append(
                SyncWrite(
                    target=",".join(plan.target_ids),
                    path=plan.path,
                    created=not plan.path.is_file(),
                    changed=False,
                    entry_count=len(entries),
                )
            )
            continue
        existing = _read_text(plan.path)
        if plan.is_cursor_rule:
            merged = _merge_cursor_rule(existing, body=rendered_body)
        else:
            merged = merge_marked_section(
                existing,
                section_heading=SECTION_HEADING,
                section_intro=SECTION_INTRO,
                body=rendered_body,
            )
        existed = plan.path.is_file()
        changed = not existed or merged != existing
        if not dry_run and changed:
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            plan.path.write_text(merged, encoding="utf-8")
        if not dry_run and store is not None:
            store.record_sync_hash(
                workspace_id=workspace_id,
                target=",".join(plan.target_ids),
                dest_path=plan.path.as_posix(),
                content_hash=body_hash,
            )
        writes.append(
            SyncWrite(
                target=",".join(plan.target_ids),
                path=plan.path,
                created=not existed,
                changed=changed,
                entry_count=len(entries),
            )
        )
    if store is not None:
        store.close()
    return SyncResult(workspace_id=workspace_id, writes=tuple(writes))


def sync_recorded_targets(
    *,
    output_dir: Path,
    workspace_ids: Sequence[str],
    dry_run: bool = False,
) -> tuple[SyncWrite, ...]:
    store = open_state_store(output_dir)
    if store is None:
        return ()
    writes: list[SyncWrite] = []
    try:
        for workspace_id in workspace_ids:
            entries = load_workspace_memory_entries(
                output_dir=output_dir,
                workspace_id=workspace_id,
            )
            for row in store.list_sync_targets(workspace_id=workspace_id):
                target = str(row["target"])
                path = Path(str(row["dest_path"]))
                rendered_body = redact_text(
                    render_harness_memory_body(entries),
                    home=Path.home(),
                )
                body_hash = sha256(rendered_body.encode("utf-8")).hexdigest()
                existing = _read_text(path)
                if _is_cursor_recorded_target(target):
                    merged = _merge_cursor_rule(existing, body=rendered_body)
                else:
                    merged = merge_marked_section(
                        existing,
                        section_heading=SECTION_HEADING,
                        section_intro=SECTION_INTRO,
                        body=rendered_body,
                    )
                existed = path.is_file()
                changed = not existed or merged != existing
                if not dry_run and changed:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(merged, encoding="utf-8")
                if not dry_run:
                    store.record_sync_hash(
                        workspace_id=workspace_id,
                        target=target,
                        dest_path=path.as_posix(),
                        content_hash=body_hash,
                    )
                writes.append(
                    SyncWrite(
                        target=target,
                        path=path,
                        created=not existed,
                        changed=changed,
                        entry_count=len(entries),
                    )
                )
    finally:
        store.close()
    return tuple(writes)


def resolve_workspace_id(*, output_dir: Path, workspace: Path) -> str:
    resolved = workspace.expanduser().resolve(strict=False)
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        workspaces = manifest.get("workspaces")
        if isinstance(workspaces, dict):
            for workspace_id, info in workspaces.items():
                if not isinstance(info, dict):
                    continue
                for key in ("canonical_path", "repo_root", "opened_cwd"):
                    raw = info.get(key)
                    if not isinstance(raw, str) or not raw:
                        continue
                    if Path(raw).expanduser().resolve(strict=False) == resolved:
                        return str(workspace_id)
    memories_dir = output_dir / "memories"
    if memories_dir.is_dir():
        matches = sorted(path.stem for path in memories_dir.glob("*.md"))
        if len(matches) == 1:
            return matches[0]
    raise SyncError(
        f"Could not map workspace {resolved.as_posix()} to a session2memory workspace_id. "
        "Re-run import with --workspace or pass output that includes manifest.json."
    )


def merge_marked_section(
    existing: str,
    *,
    section_heading: str,
    section_intro: str,
    body: str,
) -> str:
    block = "\n".join((MARKER_START, body.rstrip(), MARKER_END))
    if _MARKER_BLOCK_RE.search(existing):
        merged = _MARKER_BLOCK_RE.sub(block, existing, count=1)
        return merged if merged.endswith("\n") else merged + "\n"
    section = "\n".join(
        (
            "",
            section_heading,
            "",
            section_intro,
            "",
            block,
            "",
        )
    )
    if not existing:
        return section.lstrip("\n")
    return existing.rstrip() + section


def render_harness_memory_body(entries: Sequence[MemoryEntry]) -> str:
    lines = [
        "Use these promoted memories when continuing work in this repo.",
        "",
    ]
    for entry in entries:
        evidence = f" _(evidence: {entry.evidence_id})_" if entry.evidence_id else ""
        lines.append(f"- **[{entry.kind}]** {entry.text}{evidence}")
    lines.append("")
    lines.append(f"_entries: {len(entries)}_")
    return "\n".join(lines)


def _merge_cursor_rule(existing: str, *, body: str) -> str:
    block = "\n".join((MARKER_START, body.rstrip(), MARKER_END))
    if existing and _MARKER_BLOCK_RE.search(existing):
        merged = _MARKER_BLOCK_RE.sub(block, existing, count=1)
        return merged if merged.endswith("\n") else merged + "\n"
    return (
        "---\n"
        "description: Promoted session memory from session2memory (managed sync block)\n"
        "alwaysApply: false\n"
        "---\n\n"
        f"{SECTION_HEADING}\n\n"
        f"{SECTION_INTRO}\n\n"
        f"{block}\n"
    )


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _normalize_targets(targets: Sequence[str] | None) -> tuple[str, ...]:
    selected = tuple(targets) if targets else DEFAULT_SYNC_TARGETS
    normalized: list[str] = []
    for target in selected:
        if target not in SYNC_TARGET_IDS:
            supported = ", ".join(SYNC_TARGET_IDS)
            raise SyncError(f"Unsupported sync target: {target} (supported: {supported})")
        if target not in normalized:
            normalized.append(target)
    return tuple(normalized)


def _is_cursor_recorded_target(target: str) -> bool:
    return "cursor" in {part.strip() for part in target.split(",")}


def _destination_plans(
    *,
    workspace: Path,
    workspace_id: str,
    targets: tuple[str, ...],
) -> list[_DestinationPlan]:
    by_path: dict[Path, _DestinationPlan] = {}
    for target in targets:
        path, is_cursor_rule = _destination_path(
            target=target,
            workspace=workspace,
            workspace_id=workspace_id,
        )
        existing = by_path.get(path)
        if existing is None:
            by_path[path] = _DestinationPlan(
                target_ids=(target,),
                path=path,
                render=render_harness_memory_body,
                is_cursor_rule=is_cursor_rule,
            )
            continue
        by_path[path] = _DestinationPlan(
            target_ids=(*existing.target_ids, target),
            path=path,
            render=existing.render,
            is_cursor_rule=existing.is_cursor_rule,
        )
    return list(by_path.values())


def _destination_path(
    *,
    target: str,
    workspace: Path,
    workspace_id: str,
) -> tuple[Path, bool]:
    if target in {"agents", "codex"}:
        return workspace / "AGENTS.md", False
    if target == "claude":
        return workspace / "CLAUDE.md", False
    if target == "cursor":
        return workspace / ".cursor" / "rules" / "session2memory-memory.mdc", True
    if target == "openclaw":
        return Path("~/.openclaw/memory").expanduser() / f"{workspace_id}.md", False
    if target == "hermes":
        return Path("~/.hermes/memory").expanduser() / f"{workspace_id}.md", False
    raise SyncError(f"Unsupported sync target: {target}")
