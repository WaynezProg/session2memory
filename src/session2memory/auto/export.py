"""Write distilled memory and session summaries as Markdown for people, agents and HKS.

The tree is a rendering of the database, safe to delete and regenerate:

    projects/<name>-<hash>.md        active memory of one workspace
    sessions/<YYYY-MM>/<tool>--<id>.md   one summarised session

A file is rewritten only when its content changed, so an incremental indexer (``ks
update``) only sees real changes. ``reader`` decides what is included: ``coding`` exports
everything, ``openclaw`` only what OpenClaw agents themselves produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from session2memory.auto.scope import Reader
from session2memory.auto.store import MEMORY_KINDS, AutoStore, MemoryRow, SessionRow

KIND_TITLES = {
    "decision": "決定",
    "constraint": "限制",
    "pitfall": "坑",
    "fact": "事實",
    "preference": "偏好",
    "procedure": "做法",
}


@dataclass
class ExportReport:
    projects: int = 0
    sessions: int = 0
    files_written: int = 0
    files_unchanged: int = 0
    files_removed: int = 0

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


def project_slug(project: str) -> str:
    tail = "-".join(part for part in Path(project).parts[-2:] if part not in ("/", ""))
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-") or "project"
    return f"{name[:60]}-{sha256(project.encode('utf-8')).hexdigest()[:8]}"


def _write(path: Path, content: str, report: ExportReport) -> None:
    try:
        if path.read_text(encoding="utf-8") == content:
            report.files_unchanged += 1
            return
    except (OSError, UnicodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    report.files_written += 1


def render_project(project: str, memories: list[MemoryRow]) -> str:
    lines = [
        "---",
        "type: project_memory",
        f"project: {project}",
        f"memories: {len(memories)}",
        "---",
        "",
        f"# 專案記憶：{project}",
        "",
    ]
    for kind in MEMORY_KINDS:
        chosen = [memory for memory in memories if memory.kind == kind]
        if not chosen:
            continue
        lines += [f"## {KIND_TITLES[kind]}", ""]
        for memory in chosen:
            sources = "、".join(memory.evidence[:3])
            lines.append(
                f"- {memory.text}（{memory.memory_id}；確認於 {memory.last_confirmed_at[:10]}；"
                f"來源 {sources}）"
            )
        lines.append("")
    return "\n".join(lines)


def render_session(session: SessionRow) -> str:
    title = session.title or f"{session.tool} session {session.session_id}"
    return "\n".join(
        [
            "---",
            "type: session_summary",
            f"source: {session.source_key}",
            f"tool: {session.tool}",
            f"scope: {session.scope}",
            f"project: {session.project or ''}",
            f"last_activity: {session.last_activity or ''}",
            f"summary_origin: {session.summary_origin or ''}",
            "---",
            "",
            f"# {title}",
            "",
            session.summary or "",
            "",
        ]
    )


def session_path(root: Path, session: SessionRow) -> Path:
    month = (session.last_activity or "unknown")[:7]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", session.session_id)[:80]
    # The readable part can collide after sanitising or truncating; the hash cannot.
    unique = sha256(session.source_key.encode("utf-8")).hexdigest()[:8]
    return root / "sessions" / month / f"{session.tool}--{safe}-{unique}.md"


def export(
    store: AutoStore, root: Path, *, reader: Reader = "coding", session_limit: int = 20_000
) -> ExportReport:
    report = ExportReport()
    expected: set[Path] = set()
    by_project: dict[str, list[MemoryRow]] = {}
    for memory in store.memories(reader=reader, limit=100_000):
        by_project.setdefault(memory.project, []).append(memory)
    for project, memories in sorted(by_project.items()):
        target = root / "projects" / f"{project_slug(project)}.md"
        expected.add(target)
        _write(target, render_project(project, memories), report)
        report.projects += 1
    for session in store.sessions(reader=reader, limit=session_limit):
        if not session.summary:
            continue
        target = session_path(root, session)
        expected.add(target)
        _write(target, render_session(session), report)
        report.sessions += 1
    # The tree mirrors the database: a project whose memory was all retired, or a session
    # no longer readable by this reader, must not stay behind for an indexer to pick up.
    for folder in ("projects", "sessions"):
        for existing in (root / folder).rglob("*.md"):
            if existing not in expected:
                existing.unlink()
                report.files_removed += 1
    return report
