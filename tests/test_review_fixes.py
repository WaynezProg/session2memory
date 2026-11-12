"""Regression tests for the defects found in code review of the unattended pipeline."""

from __future__ import annotations

import json
import os
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from test_auto_pipeline import NOW, PARQUET, FakeRunner, remember, seed
from test_auto_pipeline import store as store  # noqa: F401 - pytest fixture
from test_registry_adapters import MUSE_ID, muse_record
from test_source_registry import DATE, S1, S3, rollout_lines, write_rollout

from session2memory.adapters.codex import CodexAdapter
from session2memory.adapters.muse import MuseAdapter
from session2memory.auto.curate import build_prompt, curate
from session2memory.auto.files import compress_codex_rollouts
from session2memory.auto.run import LAST_SCAN_KEY, AutoConfig, run_auto
from session2memory.auto.scan import ScanSources
from session2memory.auto.store import AutoStore
from session2memory.hook_install import update_settings
from session2memory.models import SourceHome
from session2memory.pipeline import run_pipeline
from session2memory.state.store import StateStore

needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd executable required")
TURNS = [("user", "把 alpha-etl 的每日匯出改成可增量重跑"), ("assistant", f"決定: {PARQUET}")]


def age(path: Path, days: float) -> None:
    moment = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (moment, moment))


# ---- scope can only widen ----------------------------------------------------------------


def test_openclaw_session_superseding_a_coding_memory_keeps_it_coding(store: AutoStore) -> None:
    original = remember(store, PARQUET)
    seed(store, "agent", scope="openclaw")
    reply = {
        "memory": [
            {
                "op": "supersede",
                "id": original,
                "kind": "decision",
                "text": "匯出格式改用 ORC，分區鍵不變",
                "evidence": ["S1"],
                "reason": "agent 改了格式",
            }
        ]
    }

    report = curate(store, FakeRunner(default=reply), max_calls=2, now=NOW)

    assert report.memories_superseded == 1
    replacement = [m for m in store.memories() if "ORC" in m.text]
    assert [m.scope for m in replacement] == ["coding"]
    assert store.memories(reader="openclaw") == [], "an openclaw reader must not get it"


def test_coding_session_confirming_an_openclaw_memory_widens_it(store: AutoStore) -> None:
    memory = remember(store, "agent 每日 09:30 產生交接單", scope="openclaw", kind="procedure")
    seed(store, "human")  # a coding session
    reply = {"memory": [{"op": "confirm", "id": memory, "evidence": ["S1"]}]}

    curate(store, FakeRunner(default=reply), max_calls=2, now=NOW)

    assert store.get_memory(memory).scope == "coding"  # type: ignore[union-attr]


# ---- summaries ---------------------------------------------------------------------------


def session_row(store: AutoStore, version: int, native: str | None, digest: str) -> None:
    store.upsert_session(
        source_key="codex:t1",
        tool="codex",
        session_id="t1",
        agent="codex",
        scope="coding",
        project="/proj/alpha",
        title=None,
        digest=digest,
        version_no=version,
        last_activity=NOW.isoformat(timespec="seconds"),
        native_summary=native,
    )


def test_native_summary_goes_stale_when_the_session_continues(store: AutoStore) -> None:
    session_row(store, 1, "Codex 的摘要：完成冪等寫入", "U: 舊要求\nA: 舊回覆")
    store.mark_curated("codex:t1", 1)
    session_row(store, 2, None, "U: 舊要求\nA: 舊回覆\nU: 新增的要求要處理重試\nA: 已加上重試")

    row = store.get_session("codex:t1")
    assert row is not None and row.summary_origin == "stale"
    prompt = build_prompt("/proj/alpha", [], [row])
    assert "新增的要求要處理重試" in prompt and "（已有摘要）" not in prompt

    store.mark_curated("codex:t1", 2, summary="更新後的摘要：已加上重試")
    updated = store.get_session("codex:t1")
    assert updated is not None
    assert (updated.summary, updated.summary_origin) == ("更新後的摘要：已加上重試", "llm")


def test_session_the_model_skipped_stays_pending(store: AutoStore) -> None:
    key = seed(store, "s1")

    report = curate(store, FakeRunner(default="{}"), max_calls=2, now=NOW)

    assert (report.sessions_unsummarised, report.sessions_curated) == (1, 0)
    assert [session.source_key for session in store.pending_sessions()] == [key]


# ---- hook install ------------------------------------------------------------------------


def test_install_leaves_other_hooks_of_the_same_group_alone(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    exe = tmp_path / "bin" / "session2memory"
    update_settings(settings, executable=exe, apply=True)
    data = json.loads(settings.read_text(encoding="utf-8"))
    unrelated = {"type": "command", "command": "echo unrelated"}
    data["hooks"]["SessionStart"][0]["hooks"].append(unrelated)
    settings.write_text(json.dumps(data), encoding="utf-8")

    moved = update_settings(settings, executable=tmp_path / "other" / "session2memory", apply=True)
    after_move = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    removed = update_settings(settings, remove=True, apply=True)
    after_remove = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["SessionStart"]

    assert moved.action == "replace" and removed.action == "remove"
    assert [group["hooks"] for group in after_move][0] == [unrelated]
    assert len(after_move) == 2 and "other/session2memory" in after_move[1]["hooks"][0]["command"]
    assert after_remove == [{"matcher": "startup|clear", "hooks": [unrelated]}]


# ---- registry keeps up with compression --------------------------------------------------


@needs_zstd
def test_compressed_rollout_is_registered_so_evidence_still_resolves(tmp_path: Path) -> None:
    root = tmp_path / "agents" / "kurisu" / "agent" / "codex-home" / "sessions"
    home = SourceHome("codex@openclaw/kurisu", "codex", "openclaw/kurisu", root)
    path = write_rollout(root, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    state = StateStore.open(tmp_path / "state.db")
    adapter = CodexAdapter(root, homes=[home])
    run_pipeline(
        adapters={"codex": adapter},
        output_dir=tmp_path / "out",
        date=DATE,
        source_roots={"codex": root},
        dry_run=False,
        state_store=state,
    )
    candidate = state.list_candidates_for_date(DATE)[0].candidate_id
    age(path, 30)

    report = compress_codex_rollouts(
        AutoStore(state._connection),
        (home,),
        tmp_path / "holding",
        now=NOW,
        min_age_days=14,
        registry=state.sources,
    )

    packed = path.with_name(path.name + ".zst")
    assert report.done == 1 and packed.exists() and not path.exists()
    states = {loc.path: loc.state for loc in state.sources.locations(f"codex:{S1}")}
    assert states == {path: "missing", packed: "present"}
    resolved = state.sources.resolve_candidate(candidate, readers={"codex": adapter.read_session})
    assert resolved.ok and resolved.path == packed and PARQUET in (resolved.text or "")


# ---- Muse frames -------------------------------------------------------------------------


def test_muse_messages_inside_one_frame_get_distinct_indexes(tmp_path: Path) -> None:
    path = tmp_path / "2026" / "11" / "10" / MUSE_ID / "session.jsonl"
    path.parent.mkdir(parents=True)
    first = muse_record(
        1, "runtime.session", {"kind": "run", "event": {"kind": "started", "prompt": "要求一"}}
    )
    second = muse_record(
        2,
        "runtime.session",
        {"kind": "run", "event": {"kind": "assistant_message_committed", "text": "決定: 回覆一"}},
    )
    frame = {
        "retained_frame": "x",
        "children": [
            {"child_index": 0, "record_json": json.dumps(first, ensure_ascii=False)},
            {"child_index": 1, "record_json": json.dumps(second, ensure_ascii=False)},
        ],
    }
    path.write_text(json.dumps(frame, ensure_ascii=False) + "\n", encoding="utf-8")

    messages = MuseAdapter(tmp_path).read_file(path).messages

    assert [m.text for m in messages] == ["要求一", "決定: 回覆一"]
    assert len({m.index for m in messages}) == 2
    assert len({m.raw_pointer.message_start for m in messages}) == 2


# ---- scan --------------------------------------------------------------------------------


def machine_config(tmp_path: Path, root: Path) -> AutoConfig:
    home = SourceHome("codex@main", "codex", "codex", root)
    return AutoConfig(
        store_dir=tmp_path / "store", sources=ScanSources(codex_homes=(home,)), steps=("scan",)
    )


def test_watermark_does_not_pass_a_source_that_failed_to_read(tmp_path: Path) -> None:
    root = tmp_path / "codex" / "sessions"
    good = write_rollout(root, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    bad = good.with_name(good.name.replace(S1, S3))
    bad.write_text("{not json\n", encoding="utf-8")
    config = machine_config(tmp_path, root)

    first = run_auto(config, now=NOW)

    scanned = first["scan"]
    assert isinstance(scanned, dict)
    assert (scanned["read_errors"], scanned["sessions_new"]) == (1, 1)
    opened = AutoStore.open(config.db_path)
    assert opened.get_meta(LAST_SCAN_KEY) is None, "the failed file must be looked at again"
    opened.close()

    bad.write_text("\n".join(rollout_lines(S3, "/proj/alpha-etl", TURNS)) + "\n", "utf-8")
    second = run_auto(config, now=NOW + timedelta(hours=6))

    rescanned = second["scan"]
    assert isinstance(rescanned, dict) and rescanned["read_errors"] == 0
    assert rescanned["sessions_new"] == 1
    opened = AutoStore.open(config.db_path)
    assert opened.get_meta(LAST_SCAN_KEY) is not None
    opened.close()


def test_resumed_codex_session_is_dated_by_its_latest_activity(tmp_path: Path) -> None:
    root = tmp_path / "codex" / "sessions"
    path = write_rollout(root, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    age(path, 0.05)  # started on DATE (2026-11-10), last written about an hour before NOW
    config = machine_config(tmp_path, root)

    run_auto(config, now=NOW)

    opened = AutoStore.open(config.db_path)
    row = opened.get_session(f"codex:{S1}")
    opened.close()
    assert row is not None and row.last_activity is not None
    assert row.last_activity[:10] == NOW.date().isoformat() != DATE


# ---- import with per-agent homes only ----------------------------------------------------


def test_import_reads_agent_homes_even_when_the_main_home_is_absent(tmp_path: Path) -> None:
    nested = tmp_path / "agents" / "kurisu" / "agent" / "codex-home" / "sessions"
    write_rollout(nested, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    missing_main = tmp_path / "home" / ".codex" / "sessions"
    homes = [
        SourceHome("codex@main", "codex", "codex", missing_main),
        SourceHome("codex@openclaw/kurisu", "codex", "openclaw/kurisu", nested),
    ]

    sessions, _ = run_pipeline(
        adapters={"codex": CodexAdapter(missing_main, homes=homes)},
        output_dir=tmp_path / "out",
        date=DATE,
        source_roots={"codex": missing_main},
        dry_run=False,
        state_store=StateStore.open(tmp_path / "state.db"),
    )

    assert sessions == 1
