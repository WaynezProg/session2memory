"""Source registry: stable session identity, locators, versions and the location manifest.

Fixtures mirror the on-disk shape of real Codex rollouts (``session_meta``, ``response_item``
messages with ``ordinal``, interleaved non-message records); all content is synthetic.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory.adapters.codex import CodexAdapter, discover_codex_homes
from session2memory.cli import app
from session2memory.models import SourceHome
from session2memory.pipeline import run_pipeline
from session2memory.state.fingerprint import source_file_fingerprint
from session2memory.state.store import StateStore

DATE = "2026-11-10"
ALPHA, BETA = "/proj/alpha-etl", "/proj/beta-billing"
S1 = "01a1c0de-0001-7000-8000-00000000a001"
S2 = "01a1c0de-0002-7000-8000-00000000b002"
S3 = "01a1c0de-0003-7000-8000-00000000a003"
DECISION = "決定: alpha 匯出格式採用 Parquet，分區鍵為 event_date"

needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd executable required")


def rollout_lines(
    session_id: str, cwd: str, turns: list[tuple[str, str]], *, start: int = 0
) -> list[str]:
    records: list[dict[str, object]] = []
    ordinal = start
    if start == 0:
        records.append(
            {
                "type": "session_meta",
                "ordinal": 0,
                "payload": {"id": session_id, "cwd": cwd, "timestamp": f"{DATE}T01:00:00Z"},
            }
        )
        ordinal = 1
    for role, text in turns:
        records.append(
            {
                "type": "response_item",
                "ordinal": ordinal,
                "payload": {"type": "message", "role": role, "content": [{"text": text}]},
            }
        )
        ordinal += 1
        if role == "assistant":  # real rollouts are mostly non-message records
            records.append(
                {
                    "type": "response_item",
                    "ordinal": ordinal,
                    "payload": {"type": "custom_tool_call_output", "output": "rows=10000 ok " * 20},
                }
            )
            ordinal += 1
    return [json.dumps(record, ensure_ascii=False) for record in records]


def write_rollout(
    root: Path, session_id: str, lines: list[str], *, compress: bool = False
) -> Path:
    year, month, day = DATE.split("-")
    path = root / year / month / day / f"rollout-{DATE}T01-00-00-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return compress_in_place(path) if compress else path


def compress_in_place(path: Path) -> Path:
    """What Codex's native maintenance does: ``x.jsonl`` becomes ``x.jsonl.zst``."""
    subprocess.run(["zstd", "-q", "--rm", "-f", str(path)], check=True)
    return path.with_name(path.name + ".zst")


S1_TURNS = [
    ("user", "目標：把 alpha-etl 的每日匯出改成可增量重跑。"),
    ("assistant", f"{DECISION}\n完成: 匯出 job 已改為冪等寫入"),
    ("assistant", "坑: staging bucket 的 lifecycle 會在 7 天後刪除中間檔"),
]
S2_TURNS = [
    ("user", "目標：beta-billing 發票重算要支援部分退款。"),
    ("assistant", "決定: beta 退款重算以 invoice_line 為最小單位"),
]
S3_TURNS = [
    ("user", "接手 alpha-etl 的監控告警。"),
    ("assistant", f"{DECISION}\n限制: 告警通道只能用內部 webhook"),
]


class Bench:
    def __init__(self, tmp_path: Path) -> None:
        self.main = tmp_path / "home" / ".codex" / "sessions"
        self.state_dir = tmp_path / "openclaw" / "state"
        self.nested = self.state_dir / "agents" / "kurisu" / "agent" / "codex-home" / "sessions"
        self.main.mkdir(parents=True)
        self.nested.mkdir(parents=True)
        self.output = tmp_path / "out"
        self.store = StateStore.open(tmp_path / "state.db")
        self.adapter = CodexAdapter(
            self.main, homes=discover_codex_homes(self.main, openclaw_state_dir=self.state_dir)
        )

    def run(self) -> tuple[int, int]:
        return run_pipeline(
            adapters={"codex": self.adapter},
            output_dir=self.output,
            date=DATE,
            source_roots={"codex": self.main},
            dry_run=False,
            state_store=self.store,
        )

    def candidate_id(self, text_prefix: str) -> str:
        matches = [
            stored.candidate_id
            for stored in self.store.list_candidates_for_date(DATE)
            if stored.candidate.text.startswith(text_prefix)
        ]
        assert len(matches) == 1, matches
        return matches[0]

    def resolve(self, candidate_id: str):  # type: ignore[no-untyped-def]
        return self.store.sources.resolve_candidate(
            candidate_id, readers={"codex": self.adapter.read_session}
        )

    def count(self, table: str) -> int:
        row = self.store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(row[0])


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


# ---- Codex adapter ---------------------------------------------------------------------


@needs_zstd
def test_codex_adapter_reads_compressed_rollout_like_the_plain_one(tmp_path: Path) -> None:
    lines = rollout_lines(S1, ALPHA, S1_TURNS)
    plain = CodexAdapter(tmp_path / "a").read_file(write_rollout(tmp_path / "a", S1, lines))
    packed_path = write_rollout(tmp_path / "b", S1, lines, compress=True)
    assert packed_path.name.endswith(".jsonl.zst")

    records = list(CodexAdapter(tmp_path / "b").iter_sessions(DATE))

    assert [record.session_id for record in records] == [S1]
    assert [message.text for message in records[0].messages] == [m.text for m in plain.messages]
    assert records[0].content_digest == plain.content_digest
    assert records[0].record_count == plain.record_count == len(lines)


def test_codex_messages_carry_native_ordinal_locators(tmp_path: Path) -> None:
    record = CodexAdapter(tmp_path).read_file(
        write_rollout(tmp_path, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    )

    assert [message.locator for message in record.messages] == ["ord:1", "ord:2", "ord:4"]
    assert record.source_key == f"codex:{S1}"


def test_codex_rollout_without_ordinal_falls_back_to_line_locator() -> None:
    record = CodexAdapter(Path("tests/fixtures/codex")).read_file(
        Path("tests/fixtures/codex/2026/05/22/codex.jsonl")
    )

    assert record.messages[0].locator is None
    assert record.messages[0].raw_pointer.message_start == 2


def test_non_session_files_in_a_codex_home_are_ignored(tmp_path: Path) -> None:
    write_rollout(tmp_path, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    year, month, day = DATE.split("-")
    day_dir = tmp_path / year / month / day
    (day_dir / "image.png").write_bytes(b"\x89PNG" + b"0" * 64)
    (day_dir / "state_5.sqlite").write_bytes(b"SQLite format 3\x00" + b"0" * 64)

    adapter = CodexAdapter(tmp_path)

    assert [record.session_id for record in adapter.iter_sessions(DATE)] == [S1]
    assert adapter.skipped == []


def test_discover_codex_homes_finds_main_and_per_agent_homes(bench: Bench) -> None:
    (bench.state_dir / "agents" / "no-codex" / "agent").mkdir(parents=True)

    homes = discover_codex_homes(bench.main, openclaw_state_dir=bench.state_dir)

    assert homes == [
        SourceHome("codex@main", "codex", "codex", bench.main),
        SourceHome("codex@openclaw/kurisu", "codex", "openclaw/kurisu", bench.nested),
    ]
    assert discover_codex_homes(bench.main) == homes[:1]


def test_streaming_fingerprint_matches_previous_text_digest(tmp_path: Path) -> None:
    from session2memory.models import digest_text

    path = tmp_path / "session.jsonl"
    path.write_text('{"text": "決定：保留既有 fingerprint"}\n', encoding="utf-8")

    digest, mtime_ns = source_file_fingerprint(path)

    assert digest == digest_text(path.read_text(encoding="utf-8"))
    assert mtime_ns == path.stat().st_mtime_ns


# ---- registry through the pipeline -----------------------------------------------------


def test_first_import_registers_sources_per_home_and_agent(bench: Bench) -> None:
    write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    write_rollout(bench.main, S2, rollout_lines(S2, BETA, S2_TURNS))
    write_rollout(bench.nested, S3, rollout_lines(S3, ALPHA, S3_TURNS))

    sessions, _ = bench.run()

    sources = bench.store.sources
    assert sessions == 3
    assert sources.source_keys() == [f"codex:{S1}", f"codex:{S2}", f"codex:{S3}"]
    assert sources.source_keys(agent="openclaw/kurisu") == [f"codex:{S3}"]
    assert sources.source_keys(agent="codex") == [f"codex:{S1}", f"codex:{S2}"]
    assert sources.versions(f"codex:{S1}") == [(1, "first-seen")]
    assert [loc.home_id for loc in sources.locations(f"codex:{S3}")] == ["codex@openclaw/kurisu"]


def test_same_decision_from_two_agents_keeps_one_candidate_per_source(bench: Bench) -> None:
    """Candidate identity includes the source message digest, so the same sentence said in
    two sessions stays two candidates (merging them is the review layer's job). Each one
    must point at its own session."""
    write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    write_rollout(bench.nested, S3, rollout_lines(S3, ALPHA, S3_TURNS))

    bench.run()

    ids = [
        stored.candidate_id
        for stored in bench.store.list_candidates_for_date(DATE)
        if stored.candidate.text.startswith("alpha 匯出格式採用 Parquet")
    ]
    assert len(ids) == 2
    assert {bench.resolve(candidate_id).source_key for candidate_id in ids} == {
        f"codex:{S1}",
        f"codex:{S3}",
    }


def test_projects_do_not_leak_into_each_other(bench: Bench) -> None:
    write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    write_rollout(bench.main, S2, rollout_lines(S2, BETA, S2_TURNS))
    bench.run()

    by_workspace: dict[str, list[str]] = {}
    for stored in bench.store.list_candidates_for_date(DATE):
        by_workspace.setdefault(stored.candidate.workspace_id, []).append(stored.candidate.text)

    assert len(by_workspace) == 2
    for texts in by_workspace.values():
        assert not (any("alpha" in t for t in texts) and any("beta" in t for t in texts))
    alpha_ws = next(ws for ws, texts in by_workspace.items() if any("alpha" in t for t in texts))
    assert bench.store.sources.source_keys(workspace_id=alpha_ws) == [f"codex:{S1}"]


def test_rerun_without_changes_adds_nothing(bench: Bench) -> None:
    write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    bench.run()
    before = {t: bench.count(t) for t in ("candidates", "source_versions", "source_messages")}

    sessions, _ = bench.run()

    assert sessions == 0
    assert {t: bench.count(t) for t in before} == before


def test_append_creates_a_new_version_with_only_the_new_messages(bench: Bench) -> None:
    path = write_rollout(bench.main, S2, rollout_lines(S2, BETA, S2_TURNS))
    bench.run()
    messages_before = bench.count("source_messages")
    existing = len(path.read_text(encoding="utf-8").splitlines())
    done = [("assistant", "完成: 退款重算的 retry 已實作")]
    extra = rollout_lines(S2, BETA, done, start=existing)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(extra) + "\n")

    bench.run()

    assert bench.store.sources.versions(f"codex:{S2}") == [(1, "first-seen"), (2, "append")]
    assert bench.count("source_messages") == messages_before + 1
    bench.candidate_id("退款重算的 retry 已實作")
    bench.candidate_id("beta 退款重算以 invoice_line")  # still exactly one


def test_rewritten_history_is_recorded_as_rewrite(bench: Bench) -> None:
    path = write_rollout(bench.main, S2, rollout_lines(S2, BETA, S2_TURNS))
    bench.run()
    changed = [("user", "目標改了。"), ("assistant", "決定: beta 改以 invoice 為最小單位")]
    path.write_text("\n".join(rollout_lines(S2, BETA, changed)) + "\n", encoding="utf-8")

    bench.run()

    assert bench.store.sources.versions(f"codex:{S2}")[-1] == (2, "rewrite")


def test_copy_of_the_same_thread_in_another_home_is_an_alias_not_a_second_import(
    bench: Bench,
) -> None:
    """A T3/Paperclip provider directory holding the same Codex thread must not double-count."""
    lines = rollout_lines(S1, ALPHA, S1_TURNS)
    write_rollout(bench.main, S1, lines)
    bench.run()
    candidates_before = bench.count("candidates")
    write_rollout(bench.nested, S1, lines)

    sessions, _ = bench.run()

    sources = bench.store.sources
    assert sessions == 0
    assert bench.count("sources") == 1
    assert bench.count("candidates") == candidates_before
    assert sources.versions(f"codex:{S1}") == [(1, "first-seen")]
    assert sorted(loc.home_id or "" for loc in sources.locations(f"codex:{S1}")) == [
        "codex@main",
        "codex@openclaw/kurisu",
    ]


def test_task_and_run_references_point_at_the_session_without_importing_it_again(
    bench: Bench,
) -> None:
    write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    bench.run()
    sources = bench.store.sources

    sources.add_ref(parent_kind="t3.thread", parent_id="t3-thread-1", source_key=f"codex:{S1}")
    sources.add_ref(parent_kind="paperclip.run", parent_id="hb-42", source_key=f"codex:{S1}")
    sources.add_ref(parent_kind="paperclip.run", parent_id="hb-42", source_key=f"codex:{S1}")

    assert sources.refs_for(f"codex:{S1}") == [
        ("paperclip.run", "hb-42"),
        ("t3.thread", "t3-thread-1"),
    ]
    assert bench.count("sources") == 1


# ---- evidence survives compression / archival ------------------------------------------


def test_evidence_resolves_to_the_exact_source_message(bench: Bench) -> None:
    path = write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    bench.run()

    resolved = bench.resolve(bench.candidate_id("alpha 匯出格式採用 Parquet"))

    assert resolved.ok
    assert (resolved.source_key, resolved.locator, resolved.path) == (f"codex:{S1}", "ord:2", path)
    assert resolved.text is not None and DECISION in resolved.text


@needs_zstd
def test_evidence_survives_native_compression_of_the_original_file(bench: Bench) -> None:
    """Codex renames ``x.jsonl`` to ``x.jsonl.zst``: the old path is gone, the evidence is not."""
    path = write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    bench.run()
    candidate_id = bench.candidate_id("alpha 匯出格式採用 Parquet")
    candidates_before = bench.count("candidates")

    packed = compress_in_place(path)
    assert not path.exists()
    sessions, _ = bench.run()

    assert sessions == 0, "compressed form of a known session is not new work"
    assert bench.count("candidates") == candidates_before
    locations = bench.store.sources.locations(f"codex:{S1}")
    states = {loc.path: (loc.state, loc.encoding) for loc in locations}
    assert states == {path: ("missing", "plain"), packed: ("present", "zstd")}
    resolved = bench.resolve(candidate_id)
    assert resolved.ok and resolved.path == packed
    assert resolved.text is not None and DECISION in resolved.text


@needs_zstd
def test_evidence_resolves_from_an_archived_copy_and_after_restore(
    bench: Bench, tmp_path: Path
) -> None:
    lines = rollout_lines(S1, ALPHA, S1_TURNS)
    path = write_rollout(bench.main, S1, lines)
    original_bytes = path.read_bytes()
    bench.run()
    candidate_id = bench.candidate_id("alpha 匯出格式採用 Parquet")
    # Archive: a compressed copy registered as another location, then the original removed.
    archive_home = SourceHome("codex@archive", "codex", "codex", tmp_path / "archive")
    archived = write_rollout(archive_home.root, S1, lines, compress=True)
    bench.store.sources.observe(
        bench.adapter.read_file(archived, home=archive_home), workspace_id=None
    )
    path.unlink()

    from_archive = bench.resolve(candidate_id)
    assert from_archive.ok and from_archive.path == archived

    # Restore to the original path, byte-identical; a re-import must not duplicate anything.
    subprocess.run(["zstd", "-q", "-d", "-f", "-o", str(path), str(archived)], check=True)
    assert path.read_bytes() == original_bytes
    candidates_before = bench.count("candidates")
    sessions, _ = bench.run()
    assert sessions == 0
    assert bench.count("candidates") == candidates_before
    assert bench.store.sources.versions(f"codex:{S1}") == [(1, "first-seen")]
    assert bench.resolve(candidate_id).path == path


def test_resolution_fails_loudly_when_no_location_is_left(bench: Bench) -> None:
    path = write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    bench.run()
    candidate_id = bench.candidate_id("alpha 匯出格式採用 Parquet")
    path.unlink()

    resolved = bench.resolve(candidate_id)

    assert not resolved.ok
    assert resolved.text is None
    assert resolved.error is not None and "no present location" in resolved.error


def test_resolution_rejects_a_location_whose_message_changed(bench: Bench) -> None:
    path = write_rollout(bench.main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    bench.run()
    candidate_id = bench.candidate_id("alpha 匯出格式採用 Parquet")
    tampered = [(role, text.replace("Parquet", "CSV")) for role, text in S1_TURNS]
    path.write_text("\n".join(rollout_lines(S1, ALPHA, tampered)) + "\n", encoding="utf-8")

    assert not bench.resolve(candidate_id).ok


# ---- CLI -------------------------------------------------------------------------------


def test_import_scans_openclaw_codex_homes_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = tmp_path / "codex" / "sessions"
    state_dir = tmp_path / "openclaw-state"
    nested = state_dir / "agents" / "kurisu" / "agent" / "codex-home" / "sessions"
    write_rollout(main, S1, rollout_lines(S1, ALPHA, S1_TURNS))
    write_rollout(nested, S3, rollout_lines(S3, ALPHA, S3_TURNS))
    monkeypatch.setenv("OPENCLAW_STATE_DIR", state_dir.as_posix())
    runner = CliRunner()
    base = ["import", "--date", DATE, "--tool", "codex", "--no-agentic-os",
            "--source-root", f"codex={main.as_posix()}"]

    default = runner.invoke(app, [*base, "--output", (tmp_path / "out-a").as_posix()])
    opted_in = runner.invoke(
        app, [*base, "--output", (tmp_path / "out-b").as_posix(), "--include-openclaw-codex-homes"]
    )

    assert default.exit_code == 0 and "sessions=1" in default.output
    assert opted_in.exit_code == 0 and "sessions=2" in opted_in.output
    store = StateStore.open(tmp_path / "out-b" / "session2memory.db")
    try:
        assert [home.home_id for home in store.sources.list_homes()] == [
            "codex@main",
            "codex@openclaw/kurisu",
        ]
    finally:
        store.close()
