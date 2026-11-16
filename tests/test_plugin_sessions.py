"""Sessions a plugin runs for an OpenClaw agent belong to that agent, not to coding."""

from __future__ import annotations

from pathlib import Path

import pytest

from session2memory.auto.rehome import rehome
from session2memory.auto.scope import forced_reader, openclaw_roots, plugin_session_owner, within
from session2memory.auto.store import AutoStore


@pytest.fixture
def state(tmp_path: Path) -> Path:
    root = tmp_path / "oc"
    (root / "state" / "plugin-state" / "muse-cli" / "sessions").mkdir(parents=True)
    (root / "workspaces" / "video-studio" / "nagato").mkdir(parents=True)
    return root / "state"


def plugin_session(state: Path, name: str, workspace: Path | str | None) -> Path:
    folder = state / "plugin-state" / "muse-cli" / "sessions" / name
    folder.mkdir()
    if workspace is not None:
        (folder / "AGENTS.md").write_text(
            f"## Runtime\nWorking directory: {workspace}\n\n## Other\n", encoding="utf-8"
        )
    return folder


def test_a_plugins_session_folder_is_openclaws_and_names_its_agents_workspace(
    state: Path, tmp_path: Path
) -> None:
    roots = openclaw_roots(state)
    nagato = state.parent / "workspaces" / "video-studio" / "nagato"
    mine = plugin_session(state, "s1", nagato)
    silent = plugin_session(state, "s2", None)
    elsewhere = plugin_session(state, "s3", tmp_path / "coding-project")

    assert within(mine, roots) and within(mine / "deeper", roots)
    assert forced_reader({}, mine, state) == "openclaw"
    assert plugin_session_owner(mine, roots) == nagato
    assert plugin_session_owner(mine / "sub" / "dir", roots) == nagato
    assert plugin_session_owner(silent, roots) is None
    # A session folder cannot claim a folder that is not OpenClaw's.
    assert plugin_session_owner(elsewhere, roots) is None
    assert plugin_session_owner(nagato, roots) is None
    assert plugin_session_owner(state / "logs", roots) is None
    assert not within(state / "logs", roots), "the rest of the state dir stays as it was"


def register(store: AutoStore, name: str, project: Path, *, scope: str = "coding") -> str:
    key = f"muse:{name}"
    store.upsert_session(
        source_key=key, tool="muse", session_id=name, agent="muse",
        scope=scope, project=project.as_posix(), title=None, digest="x" * 200,  # type: ignore[arg-type]
        version_no=1, last_activity="2026-11-10T00:00:00+00:00",
    )  # fmt: skip
    return key


def test_rehome_gives_the_agent_its_sessions_and_the_memory_only_they_fed(
    state: Path, tmp_path: Path
) -> None:
    nagato = state.parent / "workspaces" / "video-studio" / "nagato"
    one = plugin_session(state, "s1", nagato)
    gone = state / "plugin-state" / "muse-cli" / "sessions" / "deleted"
    coding = tmp_path / "work"
    store = AutoStore.open(tmp_path / "store.db")
    try:
        a = register(store, "s1", one)
        b = register(store, "deleted", gone)
        c = register(store, "real", coding)
        store.upsert_session(
            source_key="openclaw-native:nagato/1", tool="openclaw-native", session_id="nagato/1",
            agent="openclaw/nagato", scope="openclaw", project=nagato.as_posix(), title=None,
            digest=None, version_no=1, last_activity=None,
        )  # fmt: skip
        topic = store.ensure_topic("影片上架", scope="coding")
        kept_topic = store.ensure_topic("coding agent", scope="coding")

        def add(text: str, project: Path, evidence: list[str], topic_id: str = topic) -> str:
            memory_id, _ = store.add_memory(
                project=project.as_posix(), scope="coding", kind="fact", text=text,
                evidence=evidence, run_id=None, topic_id=topic_id,
            )  # fmt: skip
            return memory_id

        mine = add("說明欄要可直接貼上", one, [a])
        old = add("舊說法", one, [a])
        store.set_memory_status(old, "superseded", reason="x", run_id=None, evidence=[])
        orphan = add("資料夾已刪的那條", gone, [b])
        mixed = add("也有真的 coding session 佐證", one, [a, c], kept_topic)
        other = add("跟這些無關", coding, [c], kept_topic)
        before = list(store._connection.iterdump())

        preview = rehome(store, openclaw_roots(state), dry_run=True)
        assert list(store._connection.iterdump()) == before
        report = rehome(store, openclaw_roots(state))

        assert preview.memories_handed_over == report.memories_handed_over == 3
        assert (report.sessions_moved, report.sessions_without_owner) == (2, 1)
        assert (report.memories_kept_coding, report.memories_refiled) == (1, 1)
        assert report.by_agent == {"nagato": 2} and report.topics_emptied == 0
        agent = store.topic_named("nagato", "openclaw")
        assert agent is not None and agent.description == "video-studio"
        got = store.get_memory(mine)
        assert got is not None and (got.scope, got.topic_id, got.text) == (
            "openclaw", agent.topic_id, "說明欄要可直接貼上",
        )  # fmt: skip
        assert [op for op, _, _ in store.events(mine)] == ["add", "narrow", "retopic"]
        past = store.get_memory(old)
        assert past is not None and (past.scope, past.status) == ("openclaw", "superseded")
        # Nobody says whose it was: it is OpenClaw's, and waits to be filed.
        lost = store.get_memory(orphan)
        assert lost is not None and (lost.scope, lost.topic_id) == ("openclaw", topic)
        for memory_id in (mixed, other):
            untouched = store.get_memory(memory_id)
            assert untouched is not None and untouched.scope == "coding"
            assert untouched.topic_id == kept_topic
        moved = store.get_session(a)
        assert moved is not None and (moved.scope, moved.project) == ("openclaw", nagato.as_posix())
        stays = store.get_session(b)
        assert stays is not None and (stays.scope, stays.project) == ("openclaw", gone.as_posix())
        real = store.get_session(c)
        assert real is not None and real.scope == "coding"
        # An OpenClaw reader now sees it; a second pass finds nothing left to do.
        assert [m.memory_id for m in store.memories(reader="openclaw")] == [orphan, mine] or {
            m.memory_id for m in store.memories(reader="openclaw")
        } == {orphan, mine}
        again = rehome(store, openclaw_roots(state))
        assert (again.memories_handed_over, again.sessions_moved) == (0, 0)
    finally:
        store.close()
