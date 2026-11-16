"""Memory is filed under topics, not under the folder a session was opened in."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from session2memory.auto import console
from session2memory.auto import curate as curate_module
from session2memory.auto.curate import (
    MAX_NEW_TOPICS_PER_BATCH,
    LlmReply,
    build_prompt,
    curate,
)
from session2memory.auto.reconcile import reconcile
from session2memory.auto.retopic import retopic
from session2memory.auto.store import AutoStore
from session2memory.session_start import HookConfig, run_hook


@pytest.fixture(autouse=True)
def folders_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    # Curation passes over folders that are gone or temporary; these tests use made-up ones.
    monkeypatch.setattr(curate_module, "is_ephemeral_project", lambda project: False)


@pytest.fixture
def store(tmp_path: Path) -> AutoStore:
    opened = AutoStore.open(tmp_path / "store" / "session2memory.db")
    yield opened
    opened.close()


def session(
    store: AutoStore, name: str, project: str, *, scope: str = "coding", agent: str = "codex"
) -> str:
    key = f"codex:{name}"
    store.upsert_session(
        source_key=key,
        tool="codex",
        session_id=name,
        agent=agent,
        scope=scope,  # type: ignore[arg-type]
        project=project,
        title=None,
        digest="U: 設定 coding agent 的 hook\nA: " + "已完成設定並驗證。" * 30,
        version_no=1,
        last_activity="2026-11-10T01:00:00+00:00",
    )
    return key


def remember(
    store: AutoStore,
    text: str,
    project: str,
    *,
    topic: str | None = None,
    scope: str = "coding",
    evidence: list[str] | None = None,
) -> str:
    topic_id = store.ensure_topic(topic, scope="coding") if topic else None
    memory_id, _ = store.add_memory(
        project=project,
        scope=scope,  # type: ignore[arg-type]
        kind="fact",
        text=text,
        evidence=evidence or [],
        run_id=None,
        topic_id=topic_id,
    )
    return memory_id


class Runner:
    def __init__(self, *replies: dict[str, object]) -> None:
        self.prompts: list[str] = []
        self._replies = list(replies)

    def __call__(self, prompt: str) -> LlmReply:
        self.prompts.append(prompt)
        reply = dict(self._replies.pop(0)) if self._replies else {}
        reply.setdefault("sessions", [{"ref": "S1", "title": "標題", "summary": "摘要"}])
        return LlmReply(text=json.dumps(reply, ensure_ascii=False))


def add(text: str, topic: str | None = None, **extra: object) -> dict[str, object]:
    op: dict[str, object] = {"op": "add", "kind": "fact", "text": text, "evidence": ["S1"]}
    if topic is not None:
        op["topic"] = topic
    return {**op, **extra}


# ---- store ---------------------------------------------------------------------


def test_a_topic_is_found_again_whatever_the_case_or_spacing(store: AutoStore) -> None:
    first = store.ensure_topic("Coding  Agent", scope="coding", description="各 agent 的設定")
    assert store.ensure_topic("coding agent", scope="coding", description="別的說明") == first
    assert store.ensure_topic("coding agent", scope="openclaw") != first, "agents are apart"
    found = store.get_topic(first)
    assert found is not None
    assert (found.name, found.description) == ("Coding Agent", "各 agent 的設定")
    with pytest.raises(ValueError):
        store.ensure_topic("  ", scope="coding")


def test_the_same_fact_learnt_in_two_folders_is_one_memory_of_the_topic(store: AutoStore) -> None:
    first = remember(store, "hook 改完要重開 Session", "/work/bootstrap", topic="coding agent")
    again = remember(store, "hook 改完要重開 Session", "/work/wrong-folder", topic="coding agent")
    other = remember(store, "hook 改完要重開 Session", "/work/wrong-folder", topic="別的主題")
    assert again == first and other != first
    assert [op for op, _, _ in store.events(first)] == ["add", "confirm"]


def test_renaming_merging_and_moving_keep_every_memory(store: AutoStore) -> None:
    a = remember(store, "甲", "/work/one", topic="agent 設定")
    b = remember(store, "乙", "/work/two", topic="coding agent")
    source = store.topic_named("agent 設定", "coding")
    target = store.topic_named("coding agent", "coding")
    assert source is not None and target is not None
    with pytest.raises(ValueError, match="merge"):
        store.update_topic(source.topic_id, name="Coding Agent")
    store.update_topic(target.topic_id, name="Coding Agents", description="設定與限制")
    assert store.merge_topic(source.topic_id, target.topic_id, reason="同一件事") == 1
    assert store.get_topic(source.topic_id) is None
    rows = store.memories(topic_ids=[target.topic_id])
    assert {row.memory_id for row in rows} == {a, b}
    op, detail, _ = store.events(a)[-1]
    assert op == "retopic" and detail["to"] == target.topic_id and detail["reason"] == "同一件事"
    assert [topic.name for topic in store.topics()] == ["Coding Agents"]
    assert store.topics()[0].active == 2


def test_a_store_from_before_topics_still_reads(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    opened = AutoStore.open(path)
    remember(opened, "舊記憶", "/work/one")
    opened.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP INDEX idx_auto_memories_topic")
    raw.execute("ALTER TABLE auto_memories DROP COLUMN topic_id")
    raw.execute("DROP TABLE auto_topics")
    raw.commit()
    raw.close()

    old = AutoStore.open_read_only(path)
    assert old is not None
    try:
        assert [row.text for row in old.memories(project="/work/one")] == ["舊記憶"]
        assert old.topics() == [] and old.workspace_topics("/work/one") == []
        assert old.memories(topic_ids=["t_x"]) == []
    finally:
        old.close()
    upgraded = AutoStore.open(path)  # a writing open adds what is missing
    assert upgraded.unfiled_memories()[0].text == "舊記憶"
    upgraded.close()


# ---- curation ------------------------------------------------------------------


def test_what_is_learnt_in_the_wrong_folder_is_filed_under_its_topic(store: AutoStore) -> None:
    existing = remember(store, "Codex hook 放在 hooks.json", "/work/agents", topic="coding agent")
    session(store, "s1", "/work/bootstrap")
    runner = Runner({"memory": [add("Claude hook 改完要重開 Session", "Coding Agent")]})

    report = curate(store, runner, max_calls=3)

    assert (report.memories_added, report.topics_created) == (1, 0)
    topic = store.topic_named("coding agent", "coding")
    assert topic is not None
    texts = {row.text: row.project for row in store.memories(topic_ids=[topic.topic_id])}
    assert texts == {
        "Codex hook 放在 hooks.json": "/work/agents",
        "Claude hook 改完要重開 Session": "/work/bootstrap",  # where it was learnt, no more
    }
    prompt = runner.prompts[0]
    assert "- coding agent｜｜1 筆" in prompt and "資料夾「/work/bootstrap」" in prompt
    # Nothing of this topic had come up in this folder yet, so it was not listed.
    assert existing not in prompt

    session(store, "s2", "/work/bootstrap")
    later = Runner({"memory": [{"op": "confirm", "id": existing, "evidence": ["S1"]}]})
    assert curate(store, later, max_calls=3).memories_confirmed == 1
    assert f"- {existing}｜fact｜coding agent｜" in later.prompts[0]


def test_new_topics_are_bounded_and_the_rest_waits_unfiled(store: AutoStore) -> None:
    session(store, "s1", "/work/one")
    adds = [
        add(f"事實 {index}", f"主題 {index}", topic_description=f"說明 {index}")
        for index in range(MAX_NEW_TOPICS_PER_BATCH + 1)
    ]
    report = curate(store, Runner({"memory": [*adds, add("沒說主題")]}), max_calls=3)

    assert report.memories_added == len(adds) + 1
    assert report.topics_created == MAX_NEW_TOPICS_PER_BATCH
    assert [topic.description for topic in store.topics()] == ["說明 0", "說明 1"] or {
        topic.description for topic in store.topics()
    } == {"說明 0", "說明 1"}
    assert {row.text for row in store.unfiled_memories()} == {
        f"事實 {MAX_NEW_TOPICS_PER_BATCH}",
        "沒說主題",
    }


def test_an_agents_memory_is_filed_under_the_agent_without_asking(store: AutoStore) -> None:
    home = "/oc/workspaces/video-studio/albedo"
    session(store, "a1", home, scope="openclaw", agent="openclaw/albedo")
    runner = Runner({"memory": [add("審查退件要附理由", "隨便一個主題")]})

    curate(store, runner, max_calls=3)

    assert "現有主題" not in runner.prompts[0] and '"topic"' not in runner.prompts[0]
    agent = store.topic_named("albedo", "openclaw")
    assert agent is not None and agent.description == "video-studio"
    assert [row.text for row in store.memories(topic_ids=[agent.topic_id])] == ["審查退件要附理由"]
    assert store.topics(scope="coding") == []
    # Another agent's sessions are not compared with albedo's memory.
    session(store, "t1", "/oc/workspaces/software-studio/tuji", scope="openclaw", agent="x")
    other = Runner({})
    curate(store, other, max_calls=3)
    assert "審查退件要附理由" not in other.prompts[0]


def test_prompt_without_topics_is_the_agent_form() -> None:
    assert "topic" not in build_prompt("/p", [], [])
    assert "（目前沒有，請新建）" in build_prompt("/p", [], [], [])


def test_reconcile_looks_over_a_topic_across_folders(store: AutoStore) -> None:
    old = remember(store, "匯出用 CSV", "/work/one", topic="匯出")
    new = remember(store, "匯出改用 Parquet", "/work/two", topic="匯出")
    remember(store, "只有一條", "/work/one", topic="別的")
    runner = Runner({"memory": [{"op": "retire", "id": old, "because": [new], "reason": "改了"}]})

    report = reconcile(store, runner, max_calls=5)

    assert (report.llm_calls, report.memories_retired) == (1, 1)
    assert "主題「匯出」" in runner.prompts[0]
    assert [row.memory_id for row in store.memories()] != [] and store.get_memory(old) is not None
    assert store.get_memory(old).status == "retired"  # type: ignore[union-attr]
    assert reconcile(store, Runner(), max_calls=5).llm_calls == 0, "unchanged: no second call"


# ---- filing what has no topic --------------------------------------------------


def test_retopic_files_agents_by_rule_and_the_rest_by_the_model(store: AutoStore) -> None:
    home = "/oc/workspaces/video-studio/albedo"
    store.upsert_session(
        source_key="openclaw-native:albedo/1", tool="openclaw-native", session_id="albedo/1",
        agent="openclaw/albedo", scope="openclaw", project=home, title=None, digest=None,
        version_no=1, last_activity=None,
    )  # fmt: skip
    mine = remember(store, "封面先看 320px", home, scope="openclaw")
    a = remember(store, "Codex hook 放在 hooks.json", "/work/bootstrap")
    b = remember(store, "Claude hook 改完要重開", "/tmp/worktree-9")
    c = remember(store, "HKS alias 要先合併", "/work/hks")
    d = remember(store, "沒人理的", "/work/x")
    kept = remember(store, "已歸檔的不動", "/work/x", topic="既有主題")
    runner = Runner(
        {
            "topics": [{"name": "coding agent", "description": "各 agent 的設定與限制"}],
            "assign": [
                {"id": a, "topic": "coding agent"},
                {"id": b, "topic": "Coding Agent"},
                {"id": c, "topic": "沒宣告的主題"},
                {"id": kept, "topic": "coding agent"},
                {"id": d, "topic": "既有主題"},
            ],
        },
        {"merge": [{"from": "既有主題", "into": "不存在"}]},
    )

    report = retopic(store, runner, max_calls=5)

    assert (report.filed_by_agent, report.filed_by_topic, report.left_unfiled) == (1, 3, 1)
    assert (report.topics_created, report.topics_merged, report.llm_calls) == (1, 0, 2)
    assert report.ops_rejected == 3  # undeclared topic, not listed, merge target missing
    agent = store.topic_named("albedo", "openclaw")
    coding = store.topic_named("coding agent", "coding")
    assert agent is not None and coding is not None
    assert store.get_memory(mine).topic_id == agent.topic_id  # type: ignore[union-attr]
    assert {r.memory_id for r in store.memories(topic_ids=[coding.topic_id])} == {a, b}
    assert [row.memory_id for row in store.unfiled_memories()] == [c]
    assert "- " + a + "｜fact｜bootstrap｜" in runner.prompts[0] and mine not in runner.prompts[0]
    before = list(store._connection.iterdump())
    assert retopic(store, Runner({"assign": []}), max_calls=1, merge=False).filed_by_topic == 0
    assert list(store._connection.iterdump()) == before


def test_retopic_merges_topics_that_are_one_subject(store: AutoStore) -> None:
    keep = remember(store, "甲", "/w", topic="coding agent")
    moved = remember(store, "乙", "/w", topic="agent 設定")
    todo = remember(store, "丙", "/w")
    runner = Runner(
        {"assign": [{"id": todo, "topic": "coding agent"}]},
        {"merge": [{"from": "agent 設定", "into": "coding agent"}]},
    )
    report = retopic(store, runner, max_calls=5)
    assert report.topics_merged == 1 and [t.name for t in store.topics()] == ["coding agent"]
    assert {r.memory_id for r in store.memories()} == {keep, moved, todo}


# ---- what a new session is given -----------------------------------------------


def hook(store: AutoStore, tmp_path: Path, cwd: str, env: dict[str, str] | None = None) -> str:
    config = HookConfig(
        agent="claude",
        session_roots={},
        codex_memory_root=tmp_path / "codex" / "memories",
        claude_projects_root=tmp_path / "claude",
        ledger_path=tmp_path / "ledger.json",
        temp_roots=(),
        auto_db=tmp_path / "store" / "session2memory.db",
        handoff=False,
        openclaw_state_dir=tmp_path / "oc" / "state",
    )
    payload = {"session_id": "new", "cwd": cwd, "source": "startup"}
    return run_hook(config, payload, env or {}).context or ""


def test_a_session_gets_the_topics_that_came_up_in_its_folder(
    store: AutoStore, tmp_path: Path
) -> None:
    key = session(store, "s1", "/work/right")
    remember(store, "在開錯的資料夾學到", "/work/wrong", topic="coding agent", evidence=[key])
    remember(store, "同主題的另一條", "/work/elsewhere", topic="coding agent")
    remember(store, "無關主題", "/work/elsewhere", topic="影片製作")
    remember(store, "還沒歸檔的舊記憶", "/work/right")

    context = hook(store, tmp_path, "/work/right")

    assert "## 相關主題的記憶（coding agent；" in context
    assert "- [事實｜coding agent] 在開錯的資料夾學到" in context
    assert "- [事實｜coding agent] 同主題的另一條" in context
    assert "- [事實] 還沒歸檔的舊記憶" in context
    assert "無關主題" not in context and "記憶主題目錄" not in context


def test_a_folder_nothing_came_up_in_gets_the_list_of_topics(
    store: AutoStore, tmp_path: Path
) -> None:
    store.ensure_topic("coding agent", scope="coding", description="各 agent 的設定")
    remember(store, "甲", "/work/a", topic="coding agent")
    remember(store, "乙", "/work/b", topic="影片製作")
    store.ensure_topic("空主題", scope="coding")
    remember(store, "代理人的事", "/oc/x", scope="openclaw")

    context = hook(store, tmp_path, "/work/brand-new")

    assert "## 記憶主題目錄" in context and "session2memory auto topics <主題名稱>" in context
    assert "- coding agent（1 筆）：各 agent 的設定" in context and "- 影片製作（1 筆）" in context
    assert "空主題" not in context and "甲" not in context and "代理人的事" not in context


def test_an_agent_only_gets_its_own_memory_and_never_the_list(
    store: AutoStore, tmp_path: Path
) -> None:
    home = (tmp_path / "oc" / "state" / "workspace" / "albedo").as_posix()
    Path(home).mkdir(parents=True)
    session(store, "a1", home, scope="openclaw", agent="openclaw/albedo")
    curate(store, Runner({"memory": [add("審查退件要附理由")]}), max_calls=1)
    remember(store, "coding 的事", "/work/a", topic="coding agent")
    remember(store, "別的代理人的事", "/oc/tuji", scope="openclaw")

    own = hook(store, tmp_path, home)
    assert "審查退件要附理由" in own and "albedo" in own
    assert "coding 的事" not in own and "別的代理人的事" not in own

    empty = Path(home).parent / "newcomer"
    empty.mkdir()
    assert hook(store, tmp_path, empty.as_posix()) == ""


# ---- the console ---------------------------------------------------------------


def test_console_reads_and_files_by_topic(store: AutoStore) -> None:
    a = remember(store, "甲", "/work/one", topic="coding agent")
    b = remember(store, "乙", "/work/two", topic="影片製作")
    c = remember(store, "丙", "/work/two")
    home = "/oc/workspaces/video-studio/albedo"
    session(store, "a1", home, scope="openclaw", agent="openclaw/albedo")
    agent = store.agent_topic(home, [])
    assert agent is not None
    mine, _ = store.add_memory(
        project="/oc/x", scope="openclaw", kind="fact", text="丁", evidence=[], run_id=None,
        topic_id=agent,
    )  # fmt: skip
    coding = store.topic_named("coding agent", "coding")
    video = store.topic_named("影片製作", "coding")
    assert coding is not None and video is not None

    listed = {row["text"]: row["topic"] for row in console.memories(store, scope="coding")}
    assert listed == {
        "甲": {"id": coding.topic_id, "label": "coding agent"},
        "乙": {"id": video.topic_id, "label": "影片製作"},
        "丙": {"id": "general", "label": "待歸類"},
    }
    assert console.memories(store, scope="openclaw")[0]["topic"]["label"] == "albedo · video-studio"
    assert [row["id"] for row in console.memories(store, topic=coding.topic_id)] == [a]
    assert [row["id"] for row in console.memories(store, topic="general")] == [c]
    names = {row["name"]: (row["scope"], row["active"]) for row in console.topics(store)}
    assert names == {
        "coding agent": ("coding", 1),
        "影片製作": ("coding", 1),
        "albedo": ("openclaw", 1),
    }
    assert [row["name"] for row in console.topics(store, scope="openclaw")] == ["albedo"]

    moved = console.move(store, c, coding.topic_id, "講的是 agent")
    assert moved["topic"]["label"] == "coding agent"
    assert moved["events"][-1]["detail"]["reason"] == "by hand: 講的是 agent"
    for wrong in ((c, coding.topic_id), (mine, coding.topic_id), (c, "t_none")):
        with pytest.raises(ValueError):
            console.move(store, *wrong, "x")

    added = console.add(store, None, "pitfall", "手動加的", "團隊規則", topic=video.topic_id)
    assert (added["topic"]["label"], added["project"], added["scope"]) == (
        "影片製作",
        "(手動)",
        "coding",
    )
    with pytest.raises(ValueError, match="already says"):
        console.add(store, None, "pitfall", "手動加的", "再一次", topic=video.topic_id)
    with pytest.raises(ValueError, match="needs a topic"):
        console.add(store, None, "pitfall", "沒有主題", "x")

    renamed = console.topic_edit(store, video.topic_id, name="影片", description="製作與上架")
    assert (renamed["name"], renamed["description"]) == ("影片", "製作與上架")
    made = console.topic_add(store, "新主題", description="空的")
    assert made["active"] == 0
    with pytest.raises(ValueError):
        console.topic_add(store, "新主題")
    merged = console.topic_merge(store, video.topic_id, coding.topic_id, "合起來")
    assert merged["moved"] == 2 and merged["active"] == 4
    with pytest.raises(ValueError):
        console.topic_merge(store, agent, coding.topic_id, "不同範圍")
    edited = console.edit(store, b, "乙改", "寫清楚")
    assert edited["topic"]["id"] == coding.topic_id, "a rewording stays in its topic"
