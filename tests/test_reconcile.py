"""The reconcile step: contradictions and repeats inside a project's whole memory."""

from __future__ import annotations

from datetime import timedelta

from test_auto_pipeline import NOW, FakeRunner, Machine
from test_auto_pipeline import machine as machine  # noqa: F401 - pytest fixture
from test_auto_pipeline import store as store  # noqa: F401 - pytest fixture

from session2memory.auto.reconcile import build_prompt, reconcile
from session2memory.auto.run import ALL_STEPS, run_auto
from session2memory.auto.scan import ScanSources
from session2memory.auto.store import AutoStore, MemoryRow, Scope

ALPHA = "/work/alpha"
OLD = "匯出格式用 CSV"
NEW = "匯出格式改用 Parquet，不再產生 CSV"


def remember(
    store: AutoStore,  # noqa: F811 - the fixture
    text: str,
    *,
    days: int = 0,
    kind: str = "decision",
    project: str = ALPHA,
    scope: Scope = "coding",
    evidence: str = "codex:s1",
) -> str:
    memory_id, _ = store.add_memory(
        project=project,
        scope=scope,
        kind=kind,
        text=text,
        evidence=[evidence],
        run_id=None,
        now=NOW + timedelta(days=days),
    )
    return memory_id


def active(store: AutoStore, project: str = ALPHA) -> list[MemoryRow]:  # noqa: F811
    return store.memories(project=project)


def test_an_older_memory_gives_way_to_the_newer_one_it_contradicts(store: AutoStore) -> None:  # noqa: F811
    old = remember(store, OLD)
    new = remember(store, NEW, days=3, evidence="claude:s2")
    runner = FakeRunner(
        {"memory": [{"op": "retire", "id": old, "because": [new], "reason": "格式已改"}]}
    )

    report = reconcile(store, runner, max_calls=3, run_id="r1", now=NOW)

    assert (report.llm_calls, report.memories_retired, report.ops_rejected) == (1, 1, 0)
    assert old in runner.prompts[0] and new in runner.prompts[0]
    assert [m.memory_id for m in active(store)] == [new]
    retired = store.get_memory(old)
    assert retired is not None and retired.status == "retired", "kept, only no longer active"
    op, detail, _ = store.events(old)[-1]
    assert op == "retired" and new in detail["reason"] and detail["evidence"] == ["claude:s2"]


def test_a_newer_memory_is_never_retired_in_favour_of_an_older_one(store: AutoStore) -> None:  # noqa: F811
    old = remember(store, OLD)
    new = remember(store, NEW, days=3)
    wrong = [
        {"op": "retire", "id": new, "because": [old]},
        {"op": "retire", "id": old},
        {"op": "retire", "id": old, "because": [old]},
        {"op": "retire", "id": "m_unknown", "because": [new]},
        {"op": "drop", "id": old},
        "not an object",
    ]

    report = reconcile(store, FakeRunner({"memory": wrong}), max_calls=3, now=NOW)

    assert (report.memories_retired, report.ops_rejected) == (0, 6)
    assert {m.memory_id for m in active(store)} == {old, new}


def test_repeats_are_merged_into_one_memory(store: AutoStore) -> None:  # noqa: F811
    first = remember(store, "部署前要先跑 migration", kind="procedure")
    second = remember(
        store, "上線前記得執行資料庫 migration", kind="procedure", days=1, evidence="claude:s2"
    )
    other = remember(store, "CI 用 Python 3.12", kind="fact")
    merged_text = "部署前先執行資料庫 migration"
    runner = FakeRunner(
        {
            "memory": [
                {"op": "merge", "ids": [first, second], "kind": "procedure", "text": merged_text}
            ]
        }
    )

    report = reconcile(store, runner, max_calls=3, now=NOW)

    assert (report.memories_merged, report.ops_rejected) == (2, 0)
    texts = {m.text: m for m in active(store)}
    assert set(texts) == {merged_text, "CI 用 Python 3.12"}
    assert texts[merged_text].evidence == ("claude:s2", "codex:s1"), "evidence of both"
    assert texts[merged_text].supersedes_id == first
    assert {store.get_memory(i).status for i in (first, second)} == {"superseded"}  # type: ignore[union-attr]
    assert store.get_memory(other).status == "active"  # type: ignore[union-attr]


def test_merging_into_the_wording_of_one_entry_keeps_that_entry(store: AutoStore) -> None:  # noqa: F811
    keep = remember(store, NEW, scope="openclaw")
    repeat = remember(store, "輸出一律 Parquet", days=1, evidence="claude:s2")
    runner = FakeRunner({"memory": [{"op": "merge", "ids": [keep, repeat], "text": NEW}]})

    reconcile(store, runner, max_calls=3, now=NOW)

    [only] = active(store)
    assert only.memory_id == keep and only.evidence == ("claude:s2", "codex:s1")
    assert only.scope == "coding", "what a coding session contributed stays readable"


def test_one_call_cannot_retire_more_than_half_of_what_it_saw(store: AutoStore) -> None:  # noqa: F811
    ids = [remember(store, f"決定 {index}", days=index) for index in range(4)]
    newest = ids[-1]
    everything = [{"op": "retire", "id": i, "because": [newest]} for i in ids[:-1]]

    report = reconcile(store, FakeRunner({"memory": everything}), max_calls=3, now=NOW)

    assert (report.memories_retired, report.ops_rejected) == (2, 1)
    assert len(active(store)) == 2


def test_a_memory_cannot_give_way_to_one_that_was_itself_retired(store: AutoStore) -> None:  # noqa: F811
    a = remember(store, "A")
    b = remember(store, "B", days=1)
    c = remember(store, "C", days=2)
    remember(store, "D", days=3)
    chain = [
        {"op": "retire", "id": b, "because": [c]},
        {"op": "retire", "id": a, "because": [b]},
    ]

    report = reconcile(store, FakeRunner({"memory": chain}), max_calls=3, now=NOW)

    assert (report.memories_retired, report.ops_rejected) == (1, 1)
    assert store.get_memory(a).status == "active"  # type: ignore[union-attr]


def test_a_project_is_only_looked_at_again_after_its_memory_changed(store: AutoStore) -> None:  # noqa: F811
    remember(store, OLD)
    remember(store, NEW, days=3)
    runner = FakeRunner()

    first = reconcile(store, runner, max_calls=3, now=NOW)
    second = reconcile(store, runner, max_calls=3, now=NOW)
    remember(store, "CI 用 Python 3.12", kind="fact")
    third = reconcile(store, runner, max_calls=3, now=NOW)

    assert (first.llm_calls, first.projects_checked) == (1, 1)
    assert (second.llm_calls, second.projects_unchanged) == (0, 1)
    assert third.llm_calls == 1 and len(runner.prompts) == 2


def test_budget_scopes_single_memories_and_failed_calls(store: AutoStore) -> None:  # noqa: F811
    for project in ("/work/a", "/work/b"):
        remember(store, OLD, project=project)
        remember(store, NEW, days=1, project=project)
    remember(store, "只有一筆", project="/work/single")
    remember(store, OLD, project="/work/agents", scope="openclaw")
    remember(store, NEW, project="/work/agents", scope="openclaw")

    limited = reconcile(store, FakeRunner(), max_calls=1, now=NOW, scopes=("coding",))
    assert (limited.llm_calls, limited.projects_left) == (1, 1), "agents and single skipped"

    failing = FakeRunner(RuntimeError("boom"), "no json here")
    failed = reconcile(store, failing, max_calls=5, now=NOW)
    assert failed.llm_failures == 2 and failed.projects_checked == 0
    retried = reconcile(store, FakeRunner(), max_calls=5, now=NOW)
    assert retried.llm_calls == 2, "a failed check is tried again"


def test_prompt_lists_id_kind_date_and_text(store: AutoStore) -> None:  # noqa: F811
    memory_id = remember(store, OLD)
    prompt = build_prompt(ALPHA, active(store))
    assert f"- {memory_id}｜decision｜{NOW.date().isoformat()}｜{OLD}" in prompt


def test_the_run_reconciles_after_curating_and_can_turn_it_off(machine: Machine) -> None:  # noqa: F811
    assert ALL_STEPS.index("curate") < ALL_STEPS.index("reconcile") < ALL_STEPS.index("export")
    opened = machine.store()
    old = remember(opened, OLD, project=machine.work.as_posix())
    new = remember(opened, NEW, days=1, project=machine.work.as_posix())
    opened.close()
    machine._opened.clear()
    steps = ("reconcile",)
    reply = {"memory": [{"op": "retire", "id": old, "because": [new]}]}

    off = run_auto(
        machine.config(steps=steps, sources=ScanSources(), max_reconcile_calls=0),
        runner=FakeRunner(reply),
        now=NOW,
    )
    rehearsal = run_auto(
        machine.config(steps=steps, sources=ScanSources(), dry_run=True),
        runner=FakeRunner(reply),
        now=NOW,
    )
    done = run_auto(
        machine.config(steps=steps, sources=ScanSources()), runner=FakeRunner(reply), now=NOW
    )

    assert "skipped" in off["reconcile"] and "skipped" in rehearsal["reconcile"]  # type: ignore[operator]
    assert done["reconcile"]["memories_retired"] == 1  # type: ignore[index]
    assert done["totals"]["memories_retired"] == 1  # type: ignore[index]
