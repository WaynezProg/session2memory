"""The JSON views the operator console reads, and the operations that change the store."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_auto_pipeline import NOW
from typer.testing import CliRunner

from session2memory.auto import console
from session2memory.auto import curate as curate_module
from session2memory.auto.run import DB_NAME
from session2memory.auto.store import AutoStore
from session2memory.cli import app

ALPHA = "/work/alpha"
BETA = "/work/beta"


def build(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A store with two projects, three sessions, and memory in every state."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    store = AutoStore.open(store_dir / DB_NAME)
    ids: dict[str, str] = {}
    try:
        for index, (key, project, tool) in enumerate(
            [
                ("codex:s1", ALPHA, "codex"),
                ("claude:s2", ALPHA, "claude"),
                ("cursor:s3", BETA, "cursor"),
            ]
        ):
            tool_name, _, session_id = key.partition(":")
            store.upsert_session(
                source_key=key,
                tool=tool,
                session_id=session_id,
                agent=tool_name,
                scope="coding",
                project=project,
                title=None,
                digest=f"U: 要求 {index}\nA: 回覆 {index}",
                version_no=1,
                last_activity=(NOW + timedelta(hours=index)).isoformat(timespec="seconds"),
                native_summary=None,
            )
        store.mark_curated("codex:s1", 1, title="匯出格式", summary="改用 Parquet")

        def remember(
            name: str, text: str, evidence: str, project: str = ALPHA, **more: object
        ) -> None:
            ids[name], _ = store.add_memory(
                project=project, scope="coding", kind="decision", text=text,
                evidence=[evidence], run_id="r1", now=NOW, **more,  # type: ignore[arg-type]
            )  # fmt: skip

        remember("old", "匯出格式用 CSV", "codex:s1")
        remember("new", "匯出格式改用 Parquet", "claude:s2", supersedes_id=ids["old"])
        store.set_memory_status(
            ids["old"], "superseded", reason="格式已改", run_id="r1", evidence=["claude:s2"]
        )
        remember("gone", "CI 用 Python 3.11", "codex:s1")
        store.set_memory_status(ids["gone"], "retired", reason="已升級", run_id="r1", evidence=[])
        remember("beta", "Beta 的部署要先跑 migration", "cursor:s3", project=BETA)
        store.start_run("r1")
        store.finish_run("r1", {"scan": {"sessions_new": 3}, "curate": {"llm_calls": 2}})
    finally:
        store.close()
    return store_dir, ids


def call(store_dir: Path, *arguments: str) -> tuple[int, dict[str, object]]:
    result = CliRunner().invoke(
        app, ["auto", "console", *arguments, "--store", store_dir.as_posix()]
    )
    return result.exit_code, json.loads(result.output)


def data(store_dir: Path, *arguments: str) -> object:
    code, payload = call(store_dir, *arguments)
    assert code == 0 and payload["ok"] is True, payload
    return payload["data"]


def test_overview_and_runs(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)

    overview = data(store_dir, "overview")
    assert overview["store"] == store_dir.as_posix()  # type: ignore[index]
    assert overview["counts"]["memories_active"] == 2  # type: ignore[index]
    assert overview["last_run"]["run_id"] == "r1"  # type: ignore[index]

    [run] = data(store_dir, "runs")  # type: ignore[misc]
    assert run["run_id"] == "r1" and run["finished_at"]
    assert run["report"]["curate"]["llm_calls"] == 2


def test_projects_are_listed_with_counts_most_recent_first(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)

    listed = data(store_dir, "projects")

    assert [p["project"] for p in listed] == [BETA, ALPHA]  # type: ignore[union-attr]
    alpha = listed[1]  # type: ignore[index]
    assert (alpha["name"], alpha["active"], alpha["superseded"], alpha["retired"]) == (
        "alpha", 1, 1, 1,
    )  # fmt: skip
    assert (alpha["sessions"], alpha["sessions_pending"]) == (2, 1), "one was curated"


def test_memories_by_status_and_one_memory_with_its_history(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    active = data(store_dir, "memories", "--project", ALPHA)
    everything = data(store_dir, "memories", "--project", ALPHA, "--status", "all")
    assert [m["id"] for m in active] == [ids["new"]]  # type: ignore[union-attr]
    assert {m["status"] for m in everything} == {"active", "superseded", "retired"}  # type: ignore[union-attr]

    old = data(store_dir, "memory", "--id", ids["old"])
    assert old["replaced_by"] == [ids["new"]] and old["restorable"] is False  # type: ignore[index]
    assert [e["op"] for e in old["events"]] == ["add", "superseded"]  # type: ignore[index]
    assert old["events"][1]["detail"]["reason"] == "格式已改"  # type: ignore[index]
    [evidence] = old["evidence_sessions"]  # type: ignore[index]
    assert (evidence["key"], evidence["title"]) == ("codex:s1", "匯出格式")
    new = data(store_dir, "memory", "--id", ids["new"])
    assert new["supersedes_id"] == ids["old"]  # type: ignore[index]


def test_sessions_can_be_filtered_and_opened(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    assert [s["key"] for s in data(store_dir, "sessions")] == [  # type: ignore[union-attr]
        "cursor:s3", "claude:s2", "codex:s1",
    ]  # fmt: skip
    only_cursor = data(store_dir, "sessions", "--tool", "cursor")
    assert [s["key"] for s in only_cursor] == ["cursor:s3"]  # type: ignore[union-attr]
    in_alpha = data(store_dir, "sessions", "--project", ALPHA, "--limit", "1", "--offset", "1")
    assert [s["key"] for s in in_alpha] == ["codex:s1"]  # type: ignore[union-attr]
    assert "digest" not in in_alpha[0], "lists stay small"  # type: ignore[index]

    opened = data(store_dir, "session", "--key", "codex:s1")
    assert opened["digest"].startswith("U: 要求 0") and opened["pending"] is False  # type: ignore[index]
    assert {m["id"] for m in opened["memories"]} == {ids["old"], ids["gone"]}  # type: ignore[index]


def test_retire_and_restore_by_hand_are_recorded_with_the_reason(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    retired = data(store_dir, "retire", "--id", ids["new"], "--reason", "寫錯了")
    assert retired["status"] == "retired" and retired["restorable"] is True  # type: ignore[index]
    assert retired["events"][-1]["detail"]["reason"] == "by hand: 寫錯了"  # type: ignore[index]

    restored = data(store_dir, "restore", "--id", ids["new"], "--reason", "其實是對的")
    assert restored["status"] == "active"  # type: ignore[index]
    assert [e["op"] for e in restored["events"]][-2:] == ["retired", "restore"]  # type: ignore[index]
    assert restored["last_confirmed_at"] >= retired["last_confirmed_at"]  # type: ignore[index, operator]


def test_what_cannot_be_done_is_refused_as_json(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    for arguments, message in [
        (("restore", "--id", ids["old"], "--reason", "x"), "only a retired or stale memory"),
        (("restore", "--id", ids["new"], "--reason", "x"), "only a retired or stale memory"),
        (("retire", "--id", ids["gone"], "--reason", "x"), "only an active memory"),
        (("retire", "--id", ids["new"]), "needs --reason"),
        (("retire", "--id", "m_missing", "--reason", "x"), "no memory m_missing"),
        (("memory", "--id", "m_missing"), "not found"),
        (("memories",), "needs --project"),
        (("memories", "--project", ALPHA, "--status", "odd"), "unknown status"),
        (("nonsense",), "unknown view"),
    ]:
        code, payload = call(store_dir, *arguments)
        assert code == 1 and payload["ok"] is False and message in str(payload["error"]), arguments

    code, payload = call(tmp_path / "nowhere", "overview")
    assert code == 1 and "no store" in str(payload["error"])


def test_reading_does_not_change_the_store(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)
    database = store_dir / DB_NAME
    before = database.read_bytes()

    for arguments in (("overview",), ("projects",), ("memory", "--id", ids["old"]), ("runs",)):
        data(store_dir, *arguments)

    assert database.read_bytes() == before
    store = AutoStore.open(database)
    try:
        assert console.memory(store, "m_missing") is None
    finally:
        store.close()


def test_memory_is_searched_by_every_word_across_workspaces(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    everywhere = data(store_dir, "search", "--query", "匯出 Parquet")
    one_place = data(store_dir, "search", "--query", "migration", "--project", ALPHA)
    any_status = data(store_dir, "search", "--query", "匯出格式", "--status", "all")
    literal = data(store_dir, "search", "--query", "%")

    assert [m["id"] for m in everywhere] == [ids["new"]]  # type: ignore[union-attr]
    assert one_place == [], "the match is in the other workspace"
    assert {m["id"] for m in any_status} == {ids["old"], ids["new"]}  # type: ignore[union-attr]
    assert literal == [], "a percent sign is a character, not a wildcard"
    assert call(store_dir, "search", "--query", "  ")[0] == 1


def test_sessions_are_filtered_by_words_and_by_waiting_for_curation(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)

    named = data(store_dir, "sessions", "--query", "Parquet")
    waiting = data(store_dir, "sessions", "--pending")

    assert [s["key"] for s in named] == ["codex:s1"]  # type: ignore[union-attr]
    assert {s["key"] for s in waiting} == {"claude:s2", "cursor:s3"}  # type: ignore[union-attr]


def test_editing_keeps_the_old_wording_as_superseded(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    edited = data(
        store_dir, "edit", "--id", ids["new"], "--text", "匯出一律用 Parquet", "--reason", "講清楚"
    )
    before = data(store_dir, "memory", "--id", ids["new"])

    assert edited["id"] != ids["new"] and edited["status"] == "active"  # type: ignore[index]
    assert edited["text"] == "匯出一律用 Parquet"  # type: ignore[index]
    assert edited["supersedes_id"] == ids["new"]  # type: ignore[index]
    assert edited["evidence"] == ["claude:s2"], "it rests on the same sessions"  # type: ignore[index]
    assert "講清楚" in json.dumps(edited["events"], ensure_ascii=False)  # type: ignore[index]
    assert before["status"] == "superseded"  # type: ignore[index]
    assert before["replaced_by"] == [edited["id"]]  # type: ignore[index]
    assert "講清楚" in json.dumps(before["events"], ensure_ascii=False)  # type: ignore[index]

    same = call(
        store_dir, "edit", "--id", edited["id"], "--text", "匯出一律用 Parquet", "--reason", "x"
    )  # type: ignore[index]
    not_active = call(store_dir, "edit", "--id", ids["gone"], "--text", "別的", "--reason", "x")
    assert same[0] == 1 and "did not change" in str(same[1]["error"])
    assert not_active[0] == 1 and "only an active memory" in str(not_active[1]["error"])


def test_a_memory_added_by_hand_says_so_in_its_history(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)

    added = data(
        store_dir, "add", "--project", ALPHA, "--kind", "constraint",
        "--text", "不要在 main 上直接 push", "--reason", "團隊規定",
    )  # fmt: skip

    assert (added["status"], added["kind"], added["evidence"]) == ("active", "constraint", [])  # type: ignore[index]
    assert "團隊規定" in json.dumps(added["events"], ensure_ascii=False)  # type: ignore[index]
    listed = data(store_dir, "memories", "--project", ALPHA)
    assert added["id"] in [m["id"] for m in listed]  # type: ignore[index, union-attr]
    again = call(
        store_dir, "add", "--project", ALPHA, "--kind", "constraint",
        "--text", "不要在 main 上直接 push", "--reason", "x",
    )  # fmt: skip
    wrong_kind = call(
        store_dir, "add", "--project", ALPHA, "--kind", "rumour", "--text", "x", "--reason", "x"
    )
    assert again[0] == 1 and "already says this" in str(again[1]["error"])
    assert wrong_kind[0] == 1 and "unknown kind" in str(wrong_kind[1]["error"])


def add_session(
    store_dir: Path,
    key: str,
    project: str | None,
    *,
    days_ago: float = 1,
    digest: str = "U: 問題\nA: " + "回覆" * 120,
) -> None:
    """A curated session whose age is counted from today, as the console counts it."""
    seen = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    store = AutoStore.open(store_dir / DB_NAME)
    try:
        store.upsert_session(
            source_key=key, tool="codex", session_id=key.partition(":")[2], agent="codex",
            scope="coding", project=project, title=None, digest=digest, version_no=1,
            last_activity=seen, native_summary=None,
        )  # fmt: skip
        store.mark_curated(key, 1, title="標題", summary="摘要")
    finally:
        store.close()


@pytest.fixture
def real_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """pytest's temporary directories lie under a path the run takes for scratch."""
    monkeypatch.setattr(curate_module, "EPHEMERAL_MARKERS", ())


@pytest.mark.usefixtures("real_paths")
def test_a_session_can_be_sent_back_to_curation_or_past_it(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)
    work = tmp_path / "real-workspace"
    work.mkdir()
    add_session(store_dir, "codex:live", work.as_posix())

    before = data(store_dir, "session", "--key", "codex:live")
    back = data(store_dir, "requeue", "--key", "codex:live")
    waiting = data(store_dir, "sessions", "--pending")
    passed = data(store_dir, "skip", "--key", "codex:live")

    assert before["curation"] == {"blocked": [], "note": None}  # type: ignore[index]
    assert back["pending"] is True and back["summary"] == "摘要"  # type: ignore[index]
    assert "codex:live" in {s["key"] for s in waiting}  # type: ignore[union-attr]
    assert passed["pending"] is False  # type: ignore[index]
    assert call(store_dir, "requeue", "--key", "codex:nope")[0] == 1


@pytest.mark.usefixtures("real_paths")
def test_a_session_the_run_would_pass_over_is_not_queued(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)
    work = tmp_path / "real-workspace"
    work.mkdir()
    add_session(store_dir, "codex:old", work.as_posix(), days_ago=30)
    add_session(store_dir, "codex:short", work.as_posix(), digest="U: 好\nA: 好")
    add_session(store_dir, "codex:gone", (tmp_path / "deleted").as_posix())
    add_session(store_dir, "codex:nowhere", None)

    for key, why in [
        ("codex:old", "more than 21 days ago"),
        ("codex:short", "too little to distil"),
        ("codex:gone", "scratch, benchmark or deleted"),
        ("codex:nowhere", "workspace is unknown"),
    ]:
        code, payload = call(store_dir, "requeue", "--key", key)
        shown = data(store_dir, "session", "--key", key)
        assert code == 1 and why in str(payload["error"]), key
        assert shown["pending"] is False, "refused, so nothing changed"  # type: ignore[index]
        assert any(why in one for one in shown["curation"]["blocked"])  # type: ignore[index]
    # Leaving the queue is always possible.
    assert data(store_dir, "skip", "--key", "codex:old")["pending"] is False  # type: ignore[index]


def test_sessions_are_filtered_by_scope(tmp_path: Path) -> None:
    store_dir, _ = build(tmp_path)

    assert len(data(store_dir, "sessions", "--scope", "coding")) == 3  # type: ignore[arg-type]
    assert data(store_dir, "sessions", "--scope", "openclaw") == []
    assert call(store_dir, "sessions", "--scope", "other")[0] == 1


def test_memory_text_is_kept_to_one_line_and_a_length(tmp_path: Path) -> None:
    store_dir, ids = build(tmp_path)

    added = data(
        store_dir, "add", "--project", ALPHA, "--kind", "fact",
        "--text", "第一行\n  第二行\t結尾 ", "--reason", "x",
    )  # fmt: skip
    too_long = call(store_dir, "edit", "--id", ids["new"], "--text", "長" * 241, "--reason", "x")
    blank = call(
        store_dir, "add", "--project", ALPHA, "--kind", "fact", "--text", " \n ", "--reason", "x"
    )

    assert added["text"] == "第一行 第二行 結尾"  # type: ignore[index]
    assert too_long[0] == 1 and "at most 240" in str(too_long[1]["error"])
    assert blank[0] == 1 and "empty" in str(blank[1]["error"])
    assert data(store_dir, "memory", "--id", ids["new"])["status"] == "active"  # type: ignore[index]


def test_text_another_entry_already_holds_is_refused_before_anything_changes(
    tmp_path: Path,
) -> None:
    store_dir, ids = build(tmp_path)

    # "gone" is retired and says "CI 用 Python 3.11"; neither path may bring it back.
    as_edit = call(
        store_dir, "edit", "--id", ids["new"], "--text", "CI 用 Python 3.11", "--reason", "x"
    )
    as_add = call(
        store_dir, "add", "--project", ALPHA, "--kind", "decision",
        "--text", " CI 用 Python 3.11 ", "--reason", "x",
    )  # fmt: skip

    for code, payload in (as_edit, as_add):
        assert code == 1 and f"{ids['gone']} (retired) already says this" in str(payload["error"])
    gone = data(store_dir, "memory", "--id", ids["gone"])
    untouched = data(store_dir, "memory", "--id", ids["new"])
    assert gone["status"] == "retired"  # type: ignore[index]
    assert [event["op"] for event in gone["events"]] == ["add", "retired"]  # type: ignore[index]
    assert untouched["status"] == "active"  # type: ignore[index]
