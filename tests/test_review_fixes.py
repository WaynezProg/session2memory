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


# ---- second review round -----------------------------------------------------------------

TOKEN = "ghp_" + "a1B2" * 6


def test_secrets_are_scrubbed_before_anything_is_stored_or_sent(store: AutoStore) -> None:
    store.upsert_session(
        source_key="codex:t9",
        tool="codex",
        session_id="t9",
        agent="codex",
        scope="coding",
        project="/proj/alpha",
        title=f"deploy with {TOKEN}",
        digest=f"U: 用 {TOKEN} 部署\nA: 已部署",
        version_no=1,
        last_activity=NOW.isoformat(timespec="seconds"),
        native_summary=f"Codex 摘要：token 是 {TOKEN}",
    )

    row = store.get_session("codex:t9")
    assert row is not None
    for value in (row.title, row.digest, row.summary):
        assert value is not None and TOKEN not in value and "[REDACTED]" in value
    assert TOKEN not in build_prompt("/proj/alpha", [], [row])
    memory_id, _ = store.add_memory(
        project="/proj/alpha",
        scope="coding",
        kind="fact",
        text=f"金鑰 {TOKEN}",
        evidence=["codex:t9"],
        run_id=None,
    )
    assert TOKEN not in store.get_memory(memory_id).text  # type: ignore[union-attr]


@needs_zstd
def test_compressed_copy_is_never_more_readable_than_its_source(tmp_path: Path) -> None:
    from session2memory.auto.files import archive_claude

    projects = tmp_path / "claude"
    source = projects / "-proj-alpha" / "c1.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"type": "user"}\n', encoding="utf-8")
    source.chmod(0o600)
    age(source, 30)
    opened = AutoStore.open(tmp_path / "state.db")
    previous = os.umask(0o022)
    try:
        report = archive_claude(opened, projects, tmp_path / "archive", now=NOW, min_age_days=10)
    finally:
        os.umask(previous)
        opened.close()

    archived = tmp_path / "archive" / "-proj-alpha" / "c1.jsonl.zst"
    assert report.done == 1
    assert archived.stat().st_mode & 0o777 == 0o600


@needs_zstd
def test_review_preview_follows_a_rollout_compressed_in_place(tmp_path: Path) -> None:
    import subprocess

    from session2memory.review import _evidence_preview

    path = write_rollout(tmp_path, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    evidence = {"tool": "codex", "source_path": path.as_posix(), "message_start": 3}
    plain = _evidence_preview(evidence)
    subprocess.run(["zstd", "-q", "--rm", str(path)], check=True)

    assert not path.exists()
    assert _evidence_preview(evidence) == plain and PARQUET in plain


def test_claude_sessions_of_a_lookalike_directory_are_not_handed_off(tmp_path: Path) -> None:
    from test_handoff import claude_session

    from session2memory.handoff_find import recent_sessions

    root = tmp_path / "claude"
    real = (tmp_path / "work" / "foo" / "bar").resolve()
    lookalike = (tmp_path / "work" / "foo-bar").resolve()
    real.mkdir(parents=True)
    lookalike.mkdir(parents=True)
    claude_session(root, real)  # both paths encode to the same directory name

    assert [f.record.cwd for f in recent_sessions("claude", root, real)] == [real]
    assert recent_sessions("claude", root, lookalike) == []


def test_muse_framed_and_plain_records_never_share_an_index(tmp_path: Path) -> None:
    path = tmp_path / "2026" / "11" / "10" / MUSE_ID / "session.jsonl"
    path.parent.mkdir(parents=True)

    def turn(sequence: int, text: str) -> dict[str, object]:
        event: dict[str, object] = {"kind": "assistant_message_committed", "text": text}
        return muse_record(sequence, "runtime.session", {"kind": "run", "event": event})

    frame = {
        "retained_frame": "x",
        "children": [{"child_index": 0, "record_json": json.dumps(turn(1, "決定: 框內"))}],
    }
    filler = [json.dumps(muse_record(i, "runtime.session", {"kind": "x"})) for i in range(2, 1002)]
    lines = [json.dumps(frame), *filler, json.dumps(turn(2000, "決定: 第一千零二行"))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    messages = MuseAdapter(tmp_path).read_file(path).messages

    assert [m.text for m in messages] == ["決定: 框內", "決定: 第一千零二行"]
    assert [m.index for m in messages] == [1, 1002]


# ---- third review round ------------------------------------------------------------------


def test_session_scope_never_narrows_from_coding_to_openclaw(store: AutoStore) -> None:
    key = seed(store, "shared")  # first seen as a coding session
    store.mark_curated(key, 1, summary="coding 工作的摘要")
    row = store.get_session(key)
    assert row is not None
    store.upsert_session(
        source_key=key,
        tool=row.tool,
        session_id=row.session_id,
        agent="openclaw/kurisu",
        scope="openclaw",  # the same session later turns up in an agent's home
        project=row.project,
        title=None,
        digest="U: 追加的內容\nA: 回覆",
        version_no=2,
        last_activity=NOW.isoformat(timespec="seconds"),
    )

    assert store.get_session(key).scope == "coding"  # type: ignore[union-attr]
    assert store.search("coding 工作的摘要", reader="openclaw") == []
    assert store.sessions(reader="openclaw") == []


def test_whole_private_key_block_is_scrubbed_even_when_cut_short() -> None:
    from session2memory.auto.secrets import scrub_secrets

    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" * 3
    block = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{body}\n-----END OPENSSH PRIVATE KEY-----"

    whole = scrub_secrets(f"金鑰如下：\n{block}\n之後的說明")
    cut = scrub_secrets(f"金鑰如下：\n{block[:90]}")

    assert body not in whole and "END OPENSSH" not in whole and "之後的說明" in whole
    assert "MIIEvQ" not in cut and cut.startswith("金鑰如下：")


def test_digest_scrubs_before_clipping(tmp_path: Path) -> None:
    from session2memory.auto.scan import build_digest

    body = "QUJDREVGR0g=" * 200
    key = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"
    turns = [("user", f"把這把金鑰裝到伺服器上，內容是 {key} 麻煩了"), ("assistant", "已處理")]
    path = write_rollout(tmp_path, S1, rollout_lines(S1, "/proj/alpha-etl", turns))

    digest = build_digest(CodexAdapter(tmp_path).read_file(path))

    assert "QUJDREVGR0g" not in digest and "[REDACTED]" in digest


def test_exported_session_files_do_not_collide(store: AutoStore, tmp_path: Path) -> None:
    from session2memory.auto.export import export

    for native in ("a/b_c", "a_b/c"):
        store.upsert_session(
            source_key=f"openclaw-native:{native}",
            tool="openclaw-native",
            session_id=native,
            agent="openclaw/a",
            scope="openclaw",
            project="/proj/alpha",
            title=None,
            digest="U: x",
            version_no=1,
            last_activity=NOW.isoformat(timespec="seconds"),
        )
        store.mark_curated(f"openclaw-native:{native}", 1, summary=f"摘要 {native}")

    report = export(store, tmp_path / "memory")

    written = sorted((tmp_path / "memory" / "sessions").rglob("*.md"))
    assert report.sessions == 2 and len(written) == 2
    assert {"摘要 a/b_c", "摘要 a_b/c"} == {
        next(line for line in p.read_text(encoding="utf-8").splitlines() if line.startswith("摘要"))
        for p in written
    }


def test_native_summary_written_after_the_scan_is_adopted_later(tmp_path: Path) -> None:
    from test_session_start import codex_summary_file

    root = tmp_path / "codex" / "sessions"
    memory_root = tmp_path / "codex" / "memories_v2"
    path = write_rollout(root, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    age(path, 2)
    home = SourceHome("codex@main", "codex", "codex", root)
    config = AutoConfig(
        store_dir=tmp_path / "store",
        sources=ScanSources(codex_homes=(home,), codex_memory_root=memory_root),
        steps=("scan",),
    )
    run_auto(config, now=NOW)
    # Codex writes its summary a day later; the transcript itself does not change.
    codex_summary_file(
        memory_root, S1, Path("/proj/alpha-etl"), "匯出增量化", hours_ago=1, name="s"
    )

    report = run_auto(config, now=NOW + timedelta(days=3))

    scanned = report["scan"]
    assert isinstance(scanned, dict) and scanned["native_summaries_adopted"] == 1
    opened = AutoStore.open(config.db_path)
    row = opened.get_session(f"codex:{S1}")
    opened.close()
    assert row is not None and row.summary_origin == "native" and row.title == "匯出增量化"


def test_review_preview_reads_openclaw_native_and_muse_through_their_adapters(
    tmp_path: Path,
) -> None:
    from test_registry_adapters import OPENCLAW_NATIVE_ID, openclaw_write
    from test_registry_adapters import TURNS as NATIVE_TURNS

    from session2memory.adapters.openclaw_native import OpenClawNativeAdapter
    from session2memory.review import _evidence_preview

    database = openclaw_write(tmp_path / "agents", NATIVE_TURNS)
    record = next(iter(OpenClawNativeAdapter(tmp_path / "agents").iter_sessions(DATE)))
    message = record.messages[1]
    evidence = {
        "tool": "openclaw-native",
        "session_id": record.session_id,
        "source_path": database.as_posix(),
        "message_start": message.index,
        "message_end": message.index,
    }

    assert record.session_id == f"kurisu/{OPENCLAW_NATIVE_ID}"
    assert _evidence_preview(evidence) == message.text
    assert _evidence_preview({**evidence, "message_start": 9999, "message_end": 9999}) == (
        "[source range unavailable]"
    )


# ---- fourth review round -----------------------------------------------------------------


def test_older_copy_in_another_home_does_not_become_the_current_version(tmp_path: Path) -> None:
    main = tmp_path / "home" / ".codex" / "sessions"
    nested = tmp_path / "agents" / "kurisu" / "agent" / "codex-home" / "sessions"
    longer = [*TURNS, ("user", "再加上重試機制"), ("assistant", "完成: 已加上重試")]
    write_rollout(main, S1, rollout_lines(S1, "/proj/alpha-etl", longer))
    write_rollout(nested, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))  # an earlier snapshot
    state = StateStore.open(tmp_path / "state.db")
    full = CodexAdapter(main).read_file(next(main.rglob("*.jsonl")))
    partial = CodexAdapter(nested).read_file(next(nested.rglob("*.jsonl")))

    first = state.sources.observe(full, workspace_id=None)
    second = state.sources.observe(partial, workspace_id=None)

    assert (first.reason, second.reason) == ("first-seen", "older-copy")
    assert not second.has_new_content
    assert state.sources.versions(f"codex:{S1}") == [(1, "first-seen")]
    assert len(state.sources.locations(f"codex:{S1}")) == 2


def test_memory_normalised_text_is_built_from_scrubbed_text(store: AutoStore) -> None:
    memory_id, _ = store.add_memory(
        project="/proj/alpha",
        scope="coding",
        kind="fact",
        text=f"部署金鑰 {TOKEN}",
        evidence=["codex:t9"],
        run_id=None,
    )

    row = store._connection.execute(
        "SELECT text, text_norm FROM auto_memories WHERE memory_id=?", (memory_id,)
    ).fetchone()
    assert TOKEN not in row[0] and TOKEN.lower() not in row[1]
    again, created = store.add_memory(
        project="/proj/alpha",
        scope="coding",
        kind="fact",
        text=f"部署金鑰 {TOKEN}",
        evidence=["codex:t9"],
        run_id=None,
    )
    assert (again, created) == (memory_id, False)


def test_review_preview_matches_evidence_by_digest_not_by_renumbered_index(
    tmp_path: Path,
) -> None:
    from session2memory.review import _evidence_preview

    path = tmp_path / "2026" / "11" / "10" / MUSE_ID / "session.jsonl"
    path.parent.mkdir(parents=True)

    def turn(sequence: int, text: str) -> dict[str, object]:
        event: dict[str, object] = {"kind": "assistant_message_committed", "text": text}
        return muse_record(sequence, "runtime.session", {"kind": "run", "event": event})

    filler = json.dumps(muse_record(1, "runtime.session", {"kind": "x"}))
    path.write_text(
        "\n".join(
            [filler, json.dumps(turn(2, "決定: 用 Parquet")), json.dumps(turn(3, "決定: 改用 ORC"))]
        )
        + "\n",
        encoding="utf-8",
    )
    target = MuseAdapter(tmp_path).read_file(path).messages[0]
    evidence = {
        "tool": "muse",
        "session_id": MUSE_ID,
        "source_path": path.as_posix(),
        "message_start": target.index,
        "message_end": target.index,
        "digest": target.raw_pointer.digest,
    }
    assert _evidence_preview(evidence) == "決定: 用 Parquet"

    # Muse drops the leading record: every index shifts down by one.
    path.write_text(
        "\n".join([json.dumps(turn(2, "決定: 用 Parquet")), json.dumps(turn(3, "決定: 改用 ORC"))])
        + "\n",
        encoding="utf-8",
    )
    assert _evidence_preview(evidence) == "決定: 用 Parquet"
    path.write_text(json.dumps(turn(3, "決定: 改用 ORC")) + "\n", encoding="utf-8")
    assert _evidence_preview(evidence).startswith("[source unavailable")


# ---- fifth review round ------------------------------------------------------------------


@needs_zstd
def test_held_original_that_grew_after_compression_is_never_purged(tmp_path: Path) -> None:
    from session2memory.auto.files import purge_holding

    root = tmp_path / "agents" / "kurisu" / "agent" / "codex-home" / "sessions"
    home = SourceHome("codex@openclaw/kurisu", "codex", "openclaw/kurisu", root)
    path = write_rollout(root, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    age(path, 30)
    opened = AutoStore.open(tmp_path / "state.db")
    holding = tmp_path / "holding"
    compress_codex_rollouts(opened, (home,), holding, now=NOW, min_age_days=14)
    held = next(holding.rglob("*.jsonl"))
    # A writer that still had the original open appends to it after the move, and Codex
    # later re-inflates the session from the compressed file.
    with held.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "response_item", "ordinal": 99, "payload": {}}\n')
    path.write_text("re-inflated by codex\n", encoding="utf-8")

    report = purge_holding(opened, now=NOW + timedelta(days=30), hold_days=3)
    opened.close()

    assert report.purged == 0 and report.failed == 1
    assert held.exists(), "the only copy of the appended conversation must survive"
    assert "changed after compression" in report.errors[0]


def test_coding_location_found_later_widens_the_session_and_its_memory(tmp_path: Path) -> None:
    main = tmp_path / "home" / ".codex" / "sessions"
    state_dir = tmp_path / "openclaw" / "state"
    nested = state_dir / "agents" / "kurisu" / "agent" / "codex-home" / "sessions"
    work = tmp_path / "work" / "alpha"
    work.mkdir(parents=True)
    lines = rollout_lines(S1, work.as_posix(), TURNS)
    write_rollout(nested, S1, lines)  # the agent's copy is scanned first
    agent_home = SourceHome("codex@openclaw/kurisu", "codex", "openclaw/kurisu", nested)
    main_home = SourceHome("codex@main", "codex", "codex", main)

    def config(*homes: SourceHome) -> AutoConfig:
        return AutoConfig(
            store_dir=tmp_path / "store", sources=ScanSources(codex_homes=homes), steps=("scan",)
        )

    run_auto(config(agent_home), now=NOW)
    opened = AutoStore.open(tmp_path / "store" / "session2memory.db")
    key = f"codex:{S1}"
    assert opened.get_session(key).scope == "openclaw"  # type: ignore[union-attr]
    memory_id, _ = opened.add_memory(
        project=work.as_posix(),
        scope="openclaw",
        kind="decision",
        text=PARQUET,
        evidence=[key],
        run_id=None,
    )
    opened.close()

    own = write_rollout(main, S1, lines)  # the same thread in the person's own home
    moment = (NOW + timedelta(minutes=30)).timestamp()
    os.utime(own, (moment, moment))
    run_auto(config(agent_home, main_home), now=NOW + timedelta(hours=1))

    opened = AutoStore.open(tmp_path / "store" / "session2memory.db")
    assert opened.get_session(key).scope == "coding"  # type: ignore[union-attr]
    assert opened.get_memory(memory_id).scope == "coding"  # type: ignore[union-attr]
    assert opened.memories(reader="openclaw") == []
    opened.close()


def test_rewinding_the_only_copy_is_a_rewrite_not_an_older_copy(tmp_path: Path) -> None:
    longer = [*TURNS, ("user", "再加上重試機制"), ("assistant", "完成: 已加上重試")]
    path = write_rollout(tmp_path, S1, rollout_lines(S1, "/proj/alpha-etl", longer))
    state = StateStore.open(tmp_path / "state.db")
    adapter = CodexAdapter(tmp_path)
    state.sources.observe(adapter.read_file(path), workspace_id=None)
    path.write_text("\n".join(rollout_lines(S1, "/proj/alpha-etl", TURNS)) + "\n", encoding="utf-8")

    rewound = state.sources.observe(adapter.read_file(path), workspace_id=None)

    assert rewound.reason == "rewrite" and rewound.has_new_content
    assert state.sources.versions(f"codex:{S1}")[-1] == (2, "rewrite")


def test_hook_install_keeps_settings_private(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"TOKEN": "secret"}}), encoding="utf-8")
    settings.chmod(0o600)
    previous = os.umask(0o022)
    try:
        update_settings(settings, executable=tmp_path / "bin" / "session2memory", apply=True)
        update_settings(settings, remove=True, apply=True)
    finally:
        os.umask(previous)

    assert settings.stat().st_mode & 0o777 == 0o600
    assert json.loads(settings.read_text(encoding="utf-8")) == {"env": {"TOKEN": "secret"}}


# ---- sixth review round ------------------------------------------------------------------


@needs_zstd
def test_second_compression_never_overwrites_an_original_still_held(tmp_path: Path) -> None:
    root = tmp_path / "agents" / "kurisu" / "agent" / "codex-home" / "sessions"
    home = SourceHome("codex@openclaw/kurisu", "codex", "openclaw/kurisu", root)
    path = write_rollout(root, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    age(path, 30)
    opened = AutoStore.open(tmp_path / "state.db")
    holding = tmp_path / "holding"
    compress_codex_rollouts(opened, (home,), holding, now=NOW, min_age_days=14)
    held = next(holding.rglob("*.jsonl"))
    with held.open("a", encoding="utf-8") as handle:  # a late writer: only copy of this line
        handle.write('{"type": "response_item", "ordinal": 99, "payload": {}}\n')
    precious = held.read_bytes()
    # Codex re-inflates the session from the compressed file; it goes cold again.
    path.with_name(path.name + ".zst").unlink()
    again_plain = "\n".join(rollout_lines(S1, "/proj/alpha-etl", TURNS)) + "\n"
    path.write_text(again_plain, encoding="utf-8")
    age(path, 20)

    again = compress_codex_rollouts(opened, (home,), holding, now=NOW, min_age_days=14)
    opened.close()

    assert (again.done, again.skipped_busy) == (0, 1)
    assert held.read_bytes() == precious and path.exists()


def test_coding_copy_with_more_content_also_widens_memory_already_derived(tmp_path: Path) -> None:
    main = tmp_path / "home" / ".codex" / "sessions"
    agents = tmp_path / "openclaw" / "state" / "agents"
    nested = agents / "kurisu" / "agent" / "codex-home" / "sessions"
    work = tmp_path / "work" / "alpha"
    work.mkdir(parents=True)
    write_rollout(nested, S1, rollout_lines(S1, work.as_posix(), TURNS))
    agent_home = SourceHome("codex@openclaw/kurisu", "codex", "openclaw/kurisu", nested)
    main_home = SourceHome("codex@main", "codex", "codex", main)
    store_dir = tmp_path / "store"

    def config(*homes: SourceHome) -> AutoConfig:
        return AutoConfig(
            store_dir=store_dir, sources=ScanSources(codex_homes=homes), steps=("scan",)
        )

    run_auto(config(agent_home), now=NOW)
    key = f"codex:{S1}"
    opened = AutoStore.open(store_dir / "session2memory.db")
    memory_id, _ = opened.add_memory(
        project=work.as_posix(),
        scope="openclaw",
        kind="decision",
        text=PARQUET,
        evidence=[key],
        run_id=None,
    )
    opened.close()
    longer = [*TURNS, ("user", "再加上重試機制"), ("assistant", "完成: 已加上重試")]
    own = write_rollout(main, S1, rollout_lines(S1, work.as_posix(), longer))
    moment = (NOW + timedelta(minutes=30)).timestamp()
    os.utime(own, (moment, moment))

    report = run_auto(config(agent_home, main_home), now=NOW + timedelta(hours=1))

    scanned = report["scan"]
    assert isinstance(scanned, dict) and scanned["sessions_updated"] == 1
    opened = AutoStore.open(store_dir / "session2memory.db")
    assert opened.get_session(key).scope == "coding"  # type: ignore[union-attr]
    assert opened.get_memory(memory_id).scope == "coding"  # type: ignore[union-attr]
    assert opened.memories(reader="openclaw") == []
    opened.close()


def test_file_going_back_to_earlier_content_is_a_new_rewrite(tmp_path: Path) -> None:
    longer = [*TURNS, ("user", "再加上重試機制"), ("assistant", "完成: 已加上重試")]
    first = "\n".join(rollout_lines(S1, "/proj/alpha-etl", TURNS)) + "\n"
    path = write_rollout(tmp_path, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    state = StateStore.open(tmp_path / "state.db")
    adapter = CodexAdapter(tmp_path)
    state.sources.observe(adapter.read_file(path), workspace_id=None)
    second = "\n".join(rollout_lines(S1, "/proj/alpha-etl", longer)) + "\n"
    path.write_text(second, encoding="utf-8")
    state.sources.observe(adapter.read_file(path), workspace_id=None)
    path.write_text(first, encoding="utf-8")

    back = state.sources.observe(adapter.read_file(path), workspace_id=None)
    same = state.sources.observe(adapter.read_file(path), workspace_id=None)

    assert (back.reason, back.has_new_content) == ("rewrite", True)
    assert [reason for _, reason in state.sources.versions(f"codex:{S1}")] == [
        "first-seen",
        "append",
        "rewrite",
    ]
    assert same.reason == "unchanged"


@needs_zstd
def test_review_preview_uses_the_archive_once_claude_deleted_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_registry_adapters import claude_lines

    from session2memory.auto.files import archive_claude
    from session2memory.review import _evidence_preview

    projects = tmp_path / "claude"
    source = projects / "-proj-alpha" / "c1.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(claude_lines(TURNS)) + "\n", encoding="utf-8")
    age(source, 30)
    store_dir = tmp_path / "store"
    opened = AutoStore.open(store_dir / "session2memory.db")
    archive_claude(opened, projects, store_dir / "archive" / "claude", now=NOW, min_age_days=10)
    opened.close()
    from session2memory.adapters.claude import ClaudeAdapter

    record = ClaudeAdapter(projects)._read_file(source)
    message = record.messages[0]
    evidence = {
        "tool": "claude",
        "session_id": record.session_id,
        "source_path": source.as_posix(),
        "message_start": message.index,
        "digest": message.raw_pointer.digest,
    }
    before = _evidence_preview(evidence)
    source.unlink()  # Claude Code's own cleanup
    monkeypatch.setenv("SESSION2MEMORY_AUTO_STORE", store_dir.as_posix())

    assert _evidence_preview(evidence) == before and "[source" not in before

    # Evidence of a later version of the session is not in that archive: say so rather
    # than show the older text as if it were the evidence.
    newer = {**evidence, "digest": "sha256:" + "0" * 64}
    assert _evidence_preview(newer).startswith("[source unavailable")

    monkeypatch.setenv("SESSION2MEMORY_AUTO_STORE", (tmp_path / "elsewhere").as_posix())
    assert _evidence_preview(evidence).startswith("[source unavailable")


# ---- seventh review round ----------------------------------------------------------------


def test_returning_to_a_version_twice_keeps_the_latest_matching_version(tmp_path: Path) -> None:
    longer = [*TURNS, ("user", "再加上重試機制"), ("assistant", "完成: 已加上重試")]
    first = "\n".join(rollout_lines(S1, "/proj/alpha-etl", TURNS)) + "\n"
    second = "\n".join(rollout_lines(S1, "/proj/alpha-etl", longer)) + "\n"
    path = write_rollout(tmp_path, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    state = StateStore.open(tmp_path / "state.db")
    adapter = CodexAdapter(tmp_path)

    def observe(content: str) -> str:
        path.write_text(content, encoding="utf-8")
        return state.sources.observe(adapter.read_file(path), workspace_id=None).reason

    reasons = [observe(first), observe(second), observe(first), observe(first), observe(second)]

    # A, B, back to A (rewrite), A again (unchanged), then B again must be new content.
    assert reasons == ["first-seen", "append", "rewrite", "unchanged", "rewrite"]


def test_openclaw_handoff_prefers_the_most_recently_active_session(tmp_path: Path) -> None:
    import sqlite3

    from test_registry_adapters import openclaw_write

    from session2memory.handoff_find import recent_sessions

    work = tmp_path / "work" / "alpha"
    work.mkdir(parents=True)
    agents = tmp_path / "agents"
    for index in range(6):
        database = openclaw_write(agents, TURNS, session_id=f"s{index}")
    connection = sqlite3.connect(database)
    session_event = json.dumps({"type": "session", "id": "ev-session", "cwd": work.as_posix()})
    connection.execute("UPDATE transcript_events SET event_json=? WHERE seq=0", (session_event,))
    for index in range(6):
        connection.execute(
            "UPDATE session_windows SET created_at=?, transcript_updated_at=? WHERE session_id=?",
            (1000 + index, 1000 + index, f"s{index}"),
        )
    # The oldest session is the one that was resumed last.
    connection.execute(
        "UPDATE session_windows SET transcript_updated_at=9999 WHERE session_id='s0'"
    )
    connection.commit()
    connection.close()

    found = recent_sessions("openclaw-native", agents, work, limit=10)

    assert found and "kurisu/s0" in [f.record.session_id for f in found]



# ---- eighth review round -----------------------------------------------------------------


def test_request_wrapped_in_the_users_own_tag_reaches_the_digest(tmp_path: Path) -> None:
    from session2memory.auto.scan import build_digest

    turns = [("user", "<task>請將匯出格式改成 Parquet 並補上測試</task>"), ("assistant", "已完成")]
    path = write_rollout(tmp_path, S1, rollout_lines(S1, "/proj/alpha-etl", turns))

    digest = build_digest(CodexAdapter(tmp_path).read_file(path))

    assert "請將匯出格式改成 Parquet 並補上測試" in digest


def test_handoff_that_does_not_fit_is_left_undelivered(tmp_path: Path) -> None:
    from test_session_start import Bench

    bench = Bench(tmp_path)
    bench.codex()
    for index in range(12):
        codex_summary = bench.memory_root / "rollout_summaries" / f"s{index:02d}.md"
        codex_summary.parent.mkdir(parents=True, exist_ok=True)
        codex_summary.write_text(
            f"thread_id: t{index}\nupdated_at: 2026-11-13T0{index % 10}:00:00+00:00\n"
            f"cwd: {bench.cwd.as_posix()}\n\n## 第 {index} 件事的標題需要一點長度\n\n內容\n",
            encoding="utf-8",
        )

    tight = bench.run("claude", session_id="tight", max_chars=1000)

    assert tight.context is not None and len(tight.context) <= 1000
    assert "上一個 Session 的交接" not in tight.context
    assert any("not delivered" in note for note in tight.notes)
    assert not bench.ledger.exists(), "nothing was handed off, so nothing is recorded"

    roomy = bench.run("claude", session_id="roomy", max_chars=4000)
    assert roomy.context is not None and "上一個 Session 的交接" in roomy.context
    assert "目標: " in roomy.context and bench.ledger.exists()


def test_dry_run_does_not_touch_an_existing_store_directory(tmp_path: Path) -> None:
    root = tmp_path / "codex" / "sessions"
    write_rollout(root, S1, rollout_lines(S1, "/proj/alpha-etl", TURNS))
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    store_dir.chmod(0o755)
    home = SourceHome("codex@main", "codex", "codex", root)
    config = AutoConfig(
        store_dir=store_dir,
        sources=ScanSources(codex_homes=(home,)),
        steps=("scan",),
        dry_run=True,
    )

    report = run_auto(config, now=NOW)

    scanned = report["scan"]
    assert isinstance(scanned, dict) and scanned["sessions_new"] == 1
    assert store_dir.stat().st_mode & 0o777 == 0o755
    assert list(store_dir.iterdir()) == []
