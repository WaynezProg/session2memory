from pathlib import Path

from session2memory.auto.export import export, session_path
from session2memory.auto.store import AutoStore


def add_session(store: AutoStore, key: str, *, minute: int, summary: str | None) -> None:
    store.upsert_session(
        source_key=f"codex:{key}",
        tool="codex",
        session_id=key,
        agent="codex",
        scope="coding",
        project="/project",
        title=None,
        digest="U: task",
        version_no=1,
        last_activity=f"2026-11-01T12:{minute:02d}:00+00:00",
    )
    if summary is not None:
        store.mark_curated(f"codex:{key}", 1, summary=summary)


def test_newer_unsummarized_sessions_do_not_prune_exported_history(tmp_path: Path) -> None:
    store = AutoStore.open(tmp_path / "store.sqlite")
    add_session(store, "old", minute=1, summary="older useful history")
    add_session(store, "new", minute=2, summary="newer useful history")
    root = tmp_path / "memory"
    export(store, root, session_limit=2)
    before = {path: path.read_bytes() for path in root.rglob("*.md")}

    add_session(store, "pending", minute=3, summary=None)
    add_session(store, "skipped", minute=4, summary=None)
    store.mark_curated("codex:skipped", 1)
    report = export(store, root, session_limit=2)

    assert report.sessions == 2
    assert report.files_removed == 0
    assert {path: path.read_bytes() for path in root.rglob("*.md")} == before


def test_explicit_limit_counts_summaries_not_empty_session_rows(tmp_path: Path) -> None:
    store = AutoStore.open(tmp_path / "store.sqlite")
    add_session(store, "old", minute=1, summary="older history")
    add_session(store, "new", minute=2, summary="newer history")
    add_session(store, "pending", minute=3, summary=None)
    root = tmp_path / "memory"

    report = export(store, root, session_limit=1)

    newest = store.get_session("codex:new")
    assert newest is not None
    assert report.sessions == 1
    assert list(root.rglob("*.md")) == [session_path(root, newest)]


def test_unbounded_summary_query_preserves_scope_and_project(tmp_path: Path) -> None:
    store = AutoStore.open(tmp_path / "store.sqlite")
    add_session(store, "old", minute=1, summary="older history")
    add_session(store, "pending", minute=2, summary=None)

    assert [row.source_key for row in store.sessions(summary_only=True, limit=None)] == [
        "codex:old"
    ]
    assert store.sessions(summary_only=True, limit=None, reader="openclaw") == []
    assert store.sessions(summary_only=True, limit=None, project="/other") == []
