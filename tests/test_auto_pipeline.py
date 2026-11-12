"""Unattended pipeline (``auto``): scan, curate, compress/archive, export, run, hook, CLI.

Everything runs on a synthetic machine layout under ``tmp_path`` (a home with Codex, Claude
and Grok stores plus an OpenClaw state directory). The only injected fake is the LLM
``runner``: a callable that records the prompt it was given and returns canned JSON.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_handoff import (
    CODEX_ID,
    GOAL,
    LAST_REPLY,
    LAST_REQUEST,
    REMINDER,
    TURNS,
    claude_session,
    codex_session,
    grok_session,
    snapshot,
    workspace_dir,
)
from test_registry_adapters import (
    CLAUDE_ID,
    GROK_ID,
    OPENCLAW_NATIVE_ID,
    claude_lines,
    openclaw_write,
)
from test_registry_adapters import TURNS as NATIVE_TURNS
from test_session_start import codex_summary_file
from test_source_registry import rollout_lines, write_rollout
from typer.testing import CliRunner

import session2memory.auto.curate as curate_module
import session2memory.auto.run as run_module
from session2memory.adapters.codex import CodexAdapter, discover_codex_homes
from session2memory.auto.curate import (
    BATCH_CHARS,
    INSTRUCTIONS,
    MAX_ADDS_PER_BATCH,
    MAX_MEMORY_LISTED,
    MAX_SESSIONS_PER_BATCH,
    LlmReply,
    curate,
    default_runner,
    extract_json,
    is_ephemeral_project,
    parse_cli_output,
)
from session2memory.auto.export import export, project_slug
from session2memory.auto.files import (
    archive_claude,
    compress_codex_rollouts,
    purge_holding,
    restore_compressed,
)
from session2memory.auto.run import ALL_STEPS, DB_NAME, LAST_SCAN_KEY, AutoConfig, run_auto
from session2memory.auto.scan import (
    DIGEST_CHARS,
    ScanReport,
    ScanSources,
    build_digest,
    forced_reader,
    openclaw_roots,
    scan,
)
from session2memory.auto.store import AutoStore, now_iso
from session2memory.cli import app
from session2memory.models import SourceHome
from session2memory.session_start import HookConfig, run_hook
from session2memory.state.store import StateStore

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)
NESTED_ID = "01a1c0de-0002-7000-8000-00000000b002"
WORKSPACE_ID = "01a1c0de-0003-7000-8000-00000000a003"
EXTRA_ID = "01a1c0de-0004-7000-8000-00000000a004"
# Two workspaces that exist on disk; the `isolated_environment` fixture points them at
# directories under tmp_path before every test (curation skips directories that are gone).
ALPHA, BETA = "", ""
REAL_EPHEMERAL_MARKERS = curate_module.EPHEMERAL_MARKERS
PARQUET = "匯出格式採用 Parquet，分區鍵為 event_date"
AGENT_NOTE = "agent 每日巡檢 Parquet 壓縮排程"
BETA_NOTE = "beta 發票重算以 invoice_line 為最小單位"

CODEX_KEY = f"codex:{CODEX_ID}"
NESTED_KEY = f"codex:{NESTED_ID}"
WORKSPACE_KEY = f"codex:{WORKSPACE_ID}"
CLAUDE_KEY = f"claude:{CLAUDE_ID}"
GROK_KEY = f"grok:{GROK_ID}"
NATIVE_KEY = f"openclaw-native:kurisu/{OPENCLAW_NATIVE_ID}"

needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd executable required")
runner_cli = CliRunner()


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test may reach a real session store, and the caller's own agent identity is unset."""
    root = tmp_path.resolve()
    monkeypatch.setenv("HOME", (root / "home").as_posix())
    monkeypatch.setenv("OPENCLAW_STATE_DIR", (root / "openclaw" / "state").as_posix())
    for name in ("SESSION2MEMORY_READER", "CODEX_HOME", "SESSION2MEMORY_AUTO_LLM_CMD"):
        monkeypatch.delenv(name, raising=False)
    for name, folder in (("ALPHA", "alpha-etl"), ("BETA", "beta-billing")):
        workspace = root / "proj" / folder
        workspace.mkdir(parents=True)
        monkeypatch.setattr(sys.modules[__name__], name, workspace.as_posix())
    # pytest's tmp_path lives under a temporary root, which curation treats as a scratch
    # location by name; the tests that are about that rule put the real markers back.
    monkeypatch.setattr(curate_module, "EPHEMERAL_MARKERS", ())


def age(path: Path, days: float) -> None:
    moment = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (moment, moment))


class Machine:
    """One synthetic machine: a home with three agent stores and an OpenClaw state dir."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path.resolve()
        self.home = self.tmp / "home"
        self.main = self.home / ".codex" / "sessions"
        self.memory_root = self.home / ".codex" / "memories_v2"
        self.claude_root = self.home / ".claude" / "projects"
        self.grok_root = self.home / ".grok" / "sessions"
        self.state_dir = self.tmp / "openclaw" / "state"
        self.agents = self.state_dir / "agents"
        self.nested = self.agents / "kurisu" / "agent" / "codex-home" / "sessions"
        self.agent_work = self.tmp / "openclaw" / "workspaces" / "kurisu"
        self.work = workspace_dir(self.tmp)
        self.store_dir = self.tmp / "store"
        self.db = self.store_dir / DB_NAME
        for directory in (
            self.main,
            self.nested,
            self.claude_root,
            self.grok_root,
            self.agent_work,
        ):
            directory.mkdir(parents=True)
        self._opened: list[AutoStore] = []

    # ---- sessions, one per kind --------------------------------------------------------
    def codex(self) -> Path:
        return codex_session(self.main, self.work)

    def nested_codex(self, session_id: str = NESTED_ID) -> Path:
        lines = rollout_lines(session_id, self.work.as_posix(), TURNS)
        return write_rollout(self.nested, session_id, lines)

    def workspace_codex(self) -> Path:
        """A session of the main Codex home, started inside an OpenClaw agent workspace."""
        lines = rollout_lines(WORKSPACE_ID, self.agent_work.as_posix(), TURNS)
        return write_rollout(self.main, WORKSPACE_ID, lines)

    def claude(self) -> Path:
        return claude_session(self.claude_root, self.work)

    def grok(self) -> Path:
        return grok_session(self.grok_root, self.work)

    def native(self) -> Path:
        return openclaw_write(self.agents, NATIVE_TURNS)

    def five_sessions(self) -> list[Path]:
        files = [self.codex(), self.nested_codex(), self.claude(), self.grok()]
        self.native()
        return files

    # ---- pipeline pieces ---------------------------------------------------------------
    def homes(self) -> tuple[SourceHome, ...]:
        return tuple(discover_codex_homes(self.main, openclaw_state_dir=self.state_dir))

    def nested_home(self) -> SourceHome:
        return next(home for home in self.homes() if home.home_id == "codex@openclaw/kurisu")

    def sources(self) -> ScanSources:
        return ScanSources(
            codex_homes=self.homes(),
            claude_root=self.claude_root,
            grok_root=self.grok_root,
            openclaw_agents_root=self.agents,
            codex_memory_root=self.memory_root,
            openclaw_roots=openclaw_roots(self.state_dir),
        )

    def scan(self, *, since: datetime = LONG_AGO, max_bytes: int | None = None) -> ScanReport:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        state = StateStore.open(self.db)
        try:
            store = AutoStore(state._connection)
            if max_bytes is None:
                return scan(state.sources, store, self.sources(), since=since)
            return scan(state.sources, store, self.sources(), since=since, max_bytes=max_bytes)
        finally:
            state.close()

    def store(self) -> AutoStore:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        opened = AutoStore.open(self.db)
        self._opened.append(opened)
        return opened

    def config(self, **overrides: object) -> AutoConfig:
        values: dict[str, object] = {
            "store_dir": self.store_dir,
            "sources": self.sources(),
            "compress_homes": tuple(h for h in self.homes() if h.home_id != "codex@main"),
        }
        values.update(overrides)
        return AutoConfig(**values)  # type: ignore[arg-type]

    def close(self) -> None:
        for opened in self._opened:
            opened.close()


@pytest.fixture
def machine(tmp_path: Path) -> Iterator[Machine]:
    built = Machine(tmp_path)
    yield built
    built.close()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[AutoStore]:
    opened = AutoStore.open(tmp_path / "auto" / DB_NAME)
    yield opened
    opened.close()


# ---- the fake LLM ------------------------------------------------------------------------

Reply = dict[str, object] | str | Exception | Callable[[str], dict[str, object]]


class FakeRunner:
    """Records every prompt; answers with the queued replies, then with ``default``."""

    def __init__(self, *replies: Reply, default: Reply | None = None) -> None:
        self.prompts: list[str] = []
        self._replies = list(replies)
        self._default: Reply = {} if default is None else default

    def __call__(self, prompt: str) -> LlmReply:
        self.prompts.append(prompt)
        reply = self._replies.pop(0) if self._replies else self._default
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(prompt)
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return LlmReply(text=text, input_tokens=100, output_tokens=20, cost_usd=0.001)


def refs_in(prompt: str) -> list[str]:
    return re.findall(r"^### (S\d+)｜", prompt, flags=re.MULTILINE)


def ref_of(prompt: str, name: str) -> str:
    """The batch reference (``S3``) the prompt gave to the seeded session ``name``."""
    for block in prompt.split("### ")[1:]:
        if marker(name) in block:
            return block.split("｜", 1)[0]
    raise AssertionError(f"{name} is not in the prompt")


def summarise(prompt: str) -> dict[str, object]:
    refs = refs_in(prompt)
    return {
        "sessions": [
            {"ref": ref, "title": f"標題 {ref}", "summary": f"摘要 {ref}：匯出改成可增量重跑"}
            for ref in refs
        ],
        "memory": [{"op": "add", "kind": "decision", "text": PARQUET, "evidence": [refs[0]]}],
    }


def marker(name: str) -> str:
    return f"要求 {name}："


def seed(
    store: AutoStore,
    name: str,
    project: str | None = None,
    *,
    scope: str = "coding",
    digest: str | None = None,
    version: int = 1,
    minute: int = 0,
    native: str | None = None,
) -> str:
    """Register one pending session directly, with a digest long enough to be curated."""
    key = f"codex:{name}"
    body = f"U: {marker(name)}把每日匯出改成可增量重跑\nA: " + "完成冪等寫入並補上重試測試。" * 12
    store.upsert_session(
        source_key=key,
        tool="codex",
        session_id=name,
        agent="openclaw/kurisu" if scope == "openclaw" else "codex",
        scope=scope,  # type: ignore[arg-type]
        project=project or ALPHA,
        title=None,
        digest=body if digest is None else digest,
        version_no=version,
        last_activity=f"2026-11-10T01:{minute:02d}:00+00:00",
        native_summary=native,
    )
    return key


def remember(
    store: AutoStore,
    text: str,
    project: str | None = None,
    *,
    scope: str = "coding",
    kind: str = "decision",
    now: datetime | None = None,
) -> str:
    memory_id, created = store.add_memory(
        project=project or ALPHA,
        scope=scope,  # type: ignore[arg-type]
        kind=kind,
        text=text,
        evidence=["codex:earlier"],
        run_id=None,
        now=now,
    )
    assert created
    return memory_id


def pending_keys(store: AutoStore) -> list[str]:
    return sorted(session.source_key for session in store.pending_sessions())


# ---- scan --------------------------------------------------------------------------------


def test_first_scan_registers_every_tool_and_scopes_openclaw_sessions(machine: Machine) -> None:
    machine.five_sessions()
    machine.workspace_codex()

    report = machine.scan()

    assert (report.sessions_new, report.files_seen, report.read_errors) == (6, 6, 0)
    assert report.by_tool == {"codex": 3, "claude": 1, "grok": 1, "openclaw-native": 1}
    store = machine.store()
    rows = {session.source_key: session for session in store.sessions()}
    assert {key: row.scope for key, row in rows.items()} == {
        CODEX_KEY: "coding",
        CLAUDE_KEY: "coding",
        GROK_KEY: "coding",
        NESTED_KEY: "openclaw",  # the Codex home of an OpenClaw agent
        NATIVE_KEY: "openclaw",  # OpenClaw's own session store
        WORKSPACE_KEY: "openclaw",  # main Codex home, but started in an agent workspace
    }
    assert rows[NESTED_KEY].agent == "openclaw/kurisu"
    assert rows[WORKSPACE_KEY].agent == "codex"
    assert rows[CODEX_KEY].project == machine.work.as_posix()
    assert rows[WORKSPACE_KEY].project == machine.agent_work.as_posix()
    assert all(row.digest_version == 1 and row.curated_version == 0 for row in rows.values())
    assert all((row.digest or "").startswith("U: ") for row in rows.values())
    assert pending_keys(store) == sorted(rows)
    assert {s.source_key for s in store.sessions(reader="openclaw")} == {
        NESTED_KEY,
        NATIVE_KEY,
        WORKSPACE_KEY,
    }


def test_rescan_without_changes_changes_nothing(machine: Machine) -> None:
    machine.five_sessions()
    machine.scan()
    store = machine.store()
    before = store.sessions()

    report = machine.scan()

    assert (report.sessions_new, report.sessions_updated, report.sessions_unchanged) == (0, 0, 5)
    assert report.by_tool == {}
    assert store.sessions() == before


def test_appended_session_gets_a_new_digest_version_and_is_pending_again(
    machine: Machine,
) -> None:
    rollout = machine.codex()
    machine.claude()
    machine.scan()
    store = machine.store()
    for key in (CODEX_KEY, CLAUDE_KEY):
        store.mark_curated(key, 1)
    assert pending_keys(store) == []

    follow_up = "再把匯出排程改到每天 03:00 並更新操作文件"
    existing = len(rollout.read_text(encoding="utf-8").splitlines())
    more = rollout_lines(
        CODEX_ID,
        machine.work.as_posix(),
        [("user", follow_up), ("assistant", "完成: 排程已改到 03:00")],
        start=existing,
    )
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(more) + "\n")
    report = machine.scan()

    assert (report.sessions_new, report.sessions_updated, report.sessions_unchanged) == (0, 1, 1)
    row = store.get_session(CODEX_KEY)
    assert row is not None
    assert (row.digest_version, row.curated_version) == (2, 1)
    assert f"U: {follow_up}" in (row.digest or "")
    assert pending_keys(store) == [CODEX_KEY]


def test_files_older_than_since_are_not_read(machine: Machine) -> None:
    age(machine.codex(), 0.1)
    age(machine.claude(), 10)

    report = machine.scan(since=NOW - timedelta(days=1))

    assert (report.files_seen, report.sessions_new) == (1, 1)
    store = machine.store()
    assert [session.source_key for session in store.sessions()] == [CODEX_KEY]

    later = machine.scan(since=NOW - timedelta(days=30))
    assert (later.files_seen, later.sessions_new, later.sessions_unchanged) == (2, 1, 1)


def test_file_over_max_bytes_is_counted_as_skipped_and_not_registered(machine: Machine) -> None:
    small = machine.codex()
    long_turns = [turn for _ in range(20) for turn in TURNS]
    big = write_rollout(
        machine.nested, NESTED_ID, rollout_lines(NESTED_ID, machine.work.as_posix(), long_turns)
    )
    assert big.stat().st_size > small.stat().st_size

    report = machine.scan(max_bytes=small.stat().st_size)

    assert (report.files_seen, report.files_skipped_large, report.sessions_new) == (2, 1, 1)
    store = machine.store()
    assert [session.source_key for session in store.sessions()] == [CODEX_KEY]


def long_rollout(machine: Machine) -> tuple[Path, str, str, str, int]:
    first = "先把 alpha-etl 的匯出流程整理成可重跑的步驟"
    latest = "最後把重試上限改成 3 次並補上關帳月份的測試"
    final_reply = "完成: 重試上限已改為 3 次，失敗代碼用 DRIFT-409"
    middle = 30
    turns: list[tuple[str, str]] = [
        ("user", f"<environment_context>\n<cwd>{machine.work}</cwd>\n</environment_context>"),
        ("user", first),
        ("assistant", "決定: 匯出步驟拆成 extract、validate、publish"),
        ("user", "Commit"),
        ("assistant", "已建立 commit 1a2b3c4"),
    ]
    for index in range(middle):
        turns.append(("user", f"第 {index} 個中間要求：" + "調整匯出欄位對應並回報差異 " * 12))
        turns.append(("assistant", f"第 {index} 輪完成：" + "欄位對應已更新 " * 20))
    turns += [("user", f"{REMINDER}\n{latest}"), ("assistant", final_reply)]
    path = write_rollout(
        machine.main, EXTRA_ID, rollout_lines(EXTRA_ID, machine.work.as_posix(), turns)
    )
    return path, first, latest, final_reply, middle + 2


def test_digest_keeps_first_and_latest_requests_and_notes_what_it_omitted(
    machine: Machine,
) -> None:
    path, first, latest, final_reply, total_turns = long_rollout(machine)

    digest = build_digest(CodexAdapter(machine.main).read_file(path))

    lines = digest.splitlines()
    assert lines[0] == f"U: {first}"
    assert lines[-2:] == [f"U: {latest}", f"A: {final_reply}"]
    assert "environment_context" not in digest and "system-reminder" not in digest
    assert "internal harness note" not in digest
    assert "U: Commit" not in digest, "a bare acknowledgement is not a request"
    note = re.search(r"^\(中間略過 (\d+) 輪\)$", digest, flags=re.MULTILINE)
    assert note is not None
    kept = sum(line.startswith("U: ") for line in lines)
    assert kept + int(note.group(1)) == total_turns
    assert kept >= 3, "more than the first and the last turn fit"
    assert "第 29 個中間要求" in digest and "第 0 個中間要求" not in digest
    assert len(digest) <= DIGEST_CHARS


def test_digest_never_exceeds_its_character_cap(machine: Machine) -> None:
    path, first, latest, _, _ = long_rollout(machine)
    record = CodexAdapter(machine.main).read_file(path)

    for cap in range(700, 3100, 13):
        digest = build_digest(record, max_chars=cap)
        assert len(digest) <= cap, cap
        assert digest.startswith(f"U: {first}") and f"U: {latest}" in digest


def test_short_session_digest_has_every_turn_and_no_omission_note(machine: Machine) -> None:
    record = CodexAdapter(machine.main).read_file(machine.codex())

    digest = build_digest(record)

    assert digest.splitlines()[0] == f"U: {GOAL}"
    assert f"U: {LAST_REQUEST}" in digest and "DRIFT-409" in digest
    assert digest.count("U: ") == 3 and "中間略過" not in digest
    assert LAST_REPLY.split("\n")[0] in digest


def test_codex_session_with_a_rollout_summary_keeps_it_as_native_summary(
    machine: Machine,
) -> None:
    machine.codex()
    machine.claude()
    codex_summary_file(
        machine.memory_root, CODEX_ID, machine.work, "匯出增量化", hours_ago=1, name="a-export"
    )

    machine.scan()

    store = machine.store()
    codex = store.get_session(CODEX_KEY)
    claude = store.get_session(CLAUDE_KEY)
    assert codex is not None and claude is not None
    assert codex.summary_origin == "native" and codex.title == "匯出增量化"
    assert "冪等寫入" in (codex.summary or "")
    assert (claude.summary, claude.summary_origin) == (None, None)


def test_openclaw_process_is_recognised_by_env_workspace_or_codex_home(tmp_path: Path) -> None:
    state_dir = tmp_path / "openclaw" / "state"
    workspace = tmp_path / "openclaw" / "workspaces" / "kurisu" / "repo"
    agent_home = state_dir / "agents" / "kurisu" / "agent" / "codex-home"
    elsewhere = tmp_path / "work"

    assert forced_reader({}, elsewhere, state_dir) is None
    assert forced_reader({"SESSION2MEMORY_READER": "coding"}, elsewhere, state_dir) is None
    own_home = {"CODEX_HOME": (tmp_path / ".codex").as_posix()}
    assert forced_reader(own_home, elsewhere, state_dir) is None
    assert forced_reader({"SESSION2MEMORY_READER": "openclaw"}, elsewhere, state_dir) == "openclaw"
    assert forced_reader({}, workspace, state_dir) == "openclaw"
    assert forced_reader({"CODEX_HOME": agent_home.as_posix()}, elsewhere, state_dir) == "openclaw"
    lookalike = tmp_path / "openclaw" / "workspaces-old" / "x"
    assert forced_reader({}, lookalike, state_dir) is None


# ---- curate ------------------------------------------------------------------------------


def test_prompt_lists_existing_memory_and_every_digest_of_the_project(store: AutoStore) -> None:
    ids = [
        remember(store, f"環境事實第 {index} 條", kind="fact")
        for index in range(MAX_MEMORY_LISTED + 5)
    ]
    foreign = remember(store, BETA_NOTE, BETA)
    names = [f"s{index:02d}" for index in range(MAX_SESSIONS_PER_BATCH + 1)]
    for minute, name in enumerate(names):
        seed(store, name, minute=minute)
    runner = FakeRunner()

    report = curate(store, runner, max_calls=5)

    assert report.llm_calls == 2 == len(runner.prompts)
    assert [len(refs_in(prompt)) for prompt in runner.prompts] == [MAX_SESSIONS_PER_BATCH, 1]
    first = runner.prompts[0]
    listed = re.findall(r"^- (m_[0-9a-f]{10})｜fact｜", first, flags=re.MULTILINE)
    assert len(listed) == MAX_MEMORY_LISTED and set(listed) <= set(ids)
    assert foreign not in first and BETA_NOTE not in first
    assert f"專案「{ALPHA}」" in first
    for name in names:
        assert sum(marker(name) in prompt for prompt in runner.prompts) == 1
    assert report.prompt_chars == sum(len(prompt) for prompt in runner.prompts)
    assert (report.sessions_curated, report.sessions_left) == (len(names), 0)
    assert (report.input_tokens, report.output_tokens) == (200, 40)
    assert report.cost_usd == pytest.approx(0.002)


def test_prompt_size_is_bounded_by_splitting_large_digests_into_batches(
    store: AutoStore,
) -> None:
    for index in range(7):
        name = f"big{index}"
        seed(store, name, minute=index, digest=f"U: {marker(name)}" + "長" * 2990)
    runner = FakeRunner()

    report = curate(store, runner, max_calls=5)

    assert [len(refs_in(prompt)) for prompt in runner.prompts] == [6, 1]
    assert max(len(prompt) for prompt in runner.prompts) <= BATCH_CHARS + len(INSTRUCTIONS)
    assert report.sessions_curated == 7


def test_add_creates_active_memory_whose_evidence_is_the_real_source_key(
    store: AutoStore,
) -> None:
    key = seed(store, "s1")
    runner = FakeRunner(
        {
            "sessions": [{"ref": "S1", "title": "匯出增量化", "summary": "改成冪等寫入"}],
            "memory": [{"op": "add", "kind": "decision", "text": PARQUET, "evidence": ["S1"]}],
        }
    )

    report = curate(store, runner, max_calls=3, run_id="run-1", now=NOW)

    [memory] = store.memories(project=ALPHA)
    assert (memory.status, memory.kind, memory.text, memory.scope) == (
        "active",
        "decision",
        PARQUET,
        "coding",
    )
    assert memory.evidence == (key,)
    assert memory.created_at == memory.last_confirmed_at == now_iso(NOW)
    assert (report.memories_added, report.sessions_curated, report.ops_rejected) == (1, 1, 0)
    session = store.get_session(key)
    assert session is not None
    assert (session.title, session.summary, session.summary_origin) == (
        "匯出增量化",
        "改成冪等寫入",
        "llm",
    )
    assert session.curated_version == session.digest_version == 1
    assert pending_keys(store) == []
    [(op, detail, _)] = store.events(memory.memory_id)
    assert op == "add" and detail["evidence"] == [key]


def test_identical_add_in_a_later_run_confirms_instead_of_duplicating(store: AutoStore) -> None:
    first = seed(store, "s1")
    add = {"op": "add", "kind": "decision", "text": PARQUET, "evidence": ["S1"]}
    curate(store, FakeRunner({"memory": [add]}), max_calls=3, now=NOW)
    second = seed(store, "s2", minute=5)
    reworded = {**add, "text": f"  {PARQUET.replace('Parquet', 'PARQUET')}  "}

    report = curate(
        store, FakeRunner({"memory": [reworded]}), max_calls=3, now=NOW + timedelta(days=2)
    )

    assert (report.memories_added, report.memories_confirmed) == (0, 1)
    [memory] = store.memories(project=ALPHA, status=None)
    assert memory.text == PARQUET and memory.status == "active"
    assert memory.evidence == tuple(sorted((first, second)))
    assert memory.created_at == now_iso(NOW)
    assert memory.last_confirmed_at == now_iso(NOW + timedelta(days=2))
    assert [op for op, _, _ in store.events(memory.memory_id)] == ["add", "confirm"]


def test_supersede_marks_the_old_memory_and_links_the_new_one(store: AutoStore) -> None:
    old_id = remember(store, "匯出格式採用 CSV", now=NOW - timedelta(days=10))
    key = seed(store, "s1")
    runner = FakeRunner(
        {
            "memory": [
                {
                    "op": "supersede",
                    "id": old_id,
                    "text": PARQUET,
                    "evidence": ["S1"],
                    "reason": "CSV 太慢",
                }
            ]
        }
    )

    report = curate(store, runner, max_calls=3, run_id="run-2", now=NOW)

    assert old_id in runner.prompts[0] and "匯出格式採用 CSV" in runner.prompts[0]
    assert (report.memories_superseded, report.memories_added, report.ops_rejected) == (1, 0, 0)
    old = store.get_memory(old_id)
    assert old is not None and old.status == "superseded"
    [new] = store.memories(project=ALPHA)
    assert (new.text, new.kind, new.supersedes_id, new.evidence) == (
        PARQUET,
        "decision",  # inherited from the memory it replaces
        old_id,
        (key,),
    )
    op, detail, _ = store.events(old_id)[-1]
    assert op == "superseded" and detail == {"reason": "CSV 太慢", "evidence": [key]}
    assert len(store.memories(project=ALPHA, status=None)) == 2


def test_supersede_with_the_same_text_only_confirms(store: AutoStore) -> None:
    old_id = remember(store, PARQUET, now=NOW - timedelta(days=10))
    seed(store, "s1")
    operation = {"op": "supersede", "id": old_id, "text": PARQUET, "evidence": ["S1"]}

    report = curate(store, FakeRunner({"memory": [operation]}), max_calls=3, now=NOW)

    assert (report.memories_superseded, report.memories_confirmed) == (0, 1)
    [memory] = store.memories(project=ALPHA, status=None)
    assert memory.memory_id == old_id and memory.status == "active"


def test_retire_changes_status_but_keeps_the_row_and_records_an_event(store: AutoStore) -> None:
    memory_id = remember(store, "staging bucket 會在 7 天後清空")
    key = seed(store, "s1")
    operation = {"op": "retire", "id": memory_id, "evidence": ["S1"], "reason": "bucket 已移除"}

    report = curate(store, FakeRunner({"memory": [operation]}), max_calls=3, now=NOW)

    assert report.memories_retired == 1
    assert store.memories(project=ALPHA) == []
    kept = store.get_memory(memory_id)
    assert kept is not None and kept.status == "retired"
    assert kept.text == "staging bucket 會在 7 天後清空"
    op, detail, _ = store.events(memory_id)[-1]
    assert op == "retired" and detail == {"reason": "bucket 已移除", "evidence": [key]}


def test_confirm_updates_last_confirmed_at_and_adds_evidence(store: AutoStore) -> None:
    memory_id = remember(store, PARQUET, now=NOW - timedelta(days=40))
    key = seed(store, "s1")
    operation = {"op": "confirm", "id": memory_id, "evidence": ["S1"]}

    report = curate(store, FakeRunner({"memory": [operation]}), max_calls=3, now=NOW)

    assert report.memories_confirmed == 1
    memory = store.get_memory(memory_id)
    assert memory is not None
    assert memory.created_at == now_iso(NOW - timedelta(days=40))
    assert memory.last_confirmed_at == now_iso(NOW)
    assert memory.evidence == tuple(sorted(("codex:earlier", key)))


def test_invalid_operations_are_rejected_and_counted(store: AutoStore) -> None:
    foreign = remember(store, BETA_NOTE, BETA)
    retired = remember(store, "已經退役的舊規則")
    store.set_memory_status(retired, "retired", reason="old", run_id=None, evidence=[])
    key = seed(store, "s1")
    valid = {"op": "add", "kind": "pitfall", "text": "lifecycle 會刪中間檔", "evidence": ["S1"]}
    invalid: list[object] = [
        {"op": "add", "kind": "fact", "text": "引用了別批的 Session", "evidence": ["S9"]},
        {"op": "add", "kind": "fact", "text": "一真一假的引用", "evidence": ["S1", "S2"]},
        {"op": "add", "kind": "fact", "text": "直接寫 source key", "evidence": [key]},
        {"op": "add", "kind": "fact", "text": "沒有證據"},
        {"op": "confirm", "id": "m_0000000000", "evidence": ["S1"]},
        {"op": "retire", "id": foreign, "evidence": ["S1"], "reason": "別的專案"},
        {"op": "supersede", "id": foreign, "text": "改寫別的專案", "evidence": ["S1"]},
        {"op": "confirm", "id": retired, "evidence": ["S1"]},
        {"op": "retire", "evidence": ["S1"]},
        {"op": "add", "kind": "rumor", "text": "種類不存在", "evidence": ["S1"]},
        {"op": "add", "kind": "fact", "evidence": ["S1"]},
        {"op": "add", "kind": "fact", "text": "   ", "evidence": ["S1"]},
        {"op": "delete", "id": foreign, "evidence": ["S1"]},
        "not an operation",
    ]

    report = curate(store, FakeRunner({"memory": [*invalid, valid]}), max_calls=3, now=NOW)

    assert report.ops_rejected == len(invalid) == len(report.rejections)
    assert (report.memories_added, report.memories_retired, report.memories_confirmed) == (1, 0, 0)
    assert [memory.text for memory in store.memories(project=ALPHA)] == ["lifecycle 會刪中間檔"]
    untouched = store.get_memory(foreign)
    assert untouched is not None and untouched.status == "active"
    assert untouched.evidence == ("codex:earlier",)
    still_retired = store.get_memory(retired)
    assert still_retired is not None and still_retired.status == "retired"
    assert report.sessions_curated == 1 and pending_keys(store) == []


def test_adds_over_the_per_batch_limit_are_rejected(store: AutoStore) -> None:
    seed(store, "s1")
    adds = [
        {"op": "add", "kind": "fact", "text": f"環境事實 {index}", "evidence": ["S1"]}
        for index in range(MAX_ADDS_PER_BATCH + 2)
    ]

    report = curate(store, FakeRunner({"memory": adds}), max_calls=3)

    assert (report.memories_added, report.ops_rejected) == (MAX_ADDS_PER_BATCH, 2)
    assert report.rejections == ["add: over the per-batch limit"] * 2
    assert len(store.memories(project=ALPHA)) == MAX_ADDS_PER_BATCH
    assert f"最多 {MAX_ADDS_PER_BATCH} 筆 add" in INSTRUCTIONS.format(
        project="", max_adds=MAX_ADDS_PER_BATCH, memories="", sessions=""
    )


def test_native_summary_survives_a_different_model_summary(store: AutoStore) -> None:
    native = "Codex 自己寫的摘要：匯出已改成冪等寫入"
    key = seed(store, "s1", native=native, digest="U: 短\nA: 好")
    runner = FakeRunner(
        {"sessions": [{"ref": "S1", "title": "模型的標題", "summary": "模型另寫的摘要"}]}
    )

    report = curate(store, runner, max_calls=3)

    assert report.llm_calls == 1, "a session with a native summary still feeds memory"
    assert "（已有摘要）" in runner.prompts[0] and native in runner.prompts[0]
    session = store.get_session(key)
    assert session is not None
    assert (session.summary, session.summary_origin) == (native, "native")
    assert session.curated_version == 1


def test_trivial_digests_are_marked_curated_without_an_llm_call(store: AutoStore) -> None:
    key = seed(store, "s1", digest="U: 現在的分支是哪一個\nA: main")
    runner = FakeRunner()

    report = curate(store, runner, max_calls=3)

    assert runner.prompts == []
    assert (report.llm_calls, report.sessions_trivial, report.sessions_curated) == (0, 1, 0)
    session = store.get_session(key)
    assert session is not None
    assert (session.curated_version, session.summary) == (1, None)
    assert pending_keys(store) == []


def test_call_budget_leaves_the_rest_pending_for_the_next_run(store: AutoStore) -> None:
    alpha = seed(store, "a1", ALPHA)
    seed(store, "b1", BETA, minute=1)
    seed(store, "b2", BETA, minute=2)
    runner = FakeRunner()

    first = curate(store, runner, max_calls=1)

    assert (first.llm_calls, first.sessions_curated, first.sessions_left) == (1, 2, 1)
    assert f"專案「{BETA}」" in runner.prompts[0], "the project with more sessions goes first"
    assert pending_keys(store) == [alpha]

    second = curate(store, runner, max_calls=1)

    assert (second.llm_calls, second.sessions_curated, second.sessions_left) == (1, 1, 0)
    assert f"專案「{ALPHA}」" in runner.prompts[1]
    assert pending_keys(store) == []


def test_sessions_of_scratch_or_deleted_workspaces_are_skipped_without_an_llm_call(
    store: AutoStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = (tmp_path.resolve() / "proj" / "deleted-repo").as_posix()
    kept = seed(store, "kept")
    seed(store, "gone", gone, minute=1)
    runner = FakeRunner()

    report = curate(store, runner, max_calls=3)

    assert (report.llm_calls, report.sessions_curated, report.sessions_ephemeral) == (1, 1, 1)
    assert marker("kept") in runner.prompts[0] and marker("gone") not in runner.prompts[0]
    assert pending_keys(store) == []
    skipped = store.get_session("codex:gone")
    assert skipped is not None and (skipped.curated_version, skipped.summary) == (1, None)

    # With the real location markers, an existing directory under a temporary root is
    # scratch space as well.
    monkeypatch.setattr(curate_module, "EPHEMERAL_MARKERS", REAL_EPHEMERAL_MARKERS)
    assert is_ephemeral_project(ALPHA) and Path(ALPHA).is_dir()
    seed(store, "kept", version=2)
    again = curate(store, runner, max_calls=3)

    assert (again.llm_calls, again.sessions_ephemeral) == (0, 1) and len(runner.prompts) == 1
    assert store.get_session(kept).curated_version == 2  # type: ignore[union-attr]


def test_ephemeral_project_is_decided_by_location_or_by_a_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = Path(ALPHA)

    assert not is_ephemeral_project(existing.as_posix())
    assert not is_ephemeral_project(existing.as_posix() + "/")
    assert is_ephemeral_project((existing / "removed").as_posix())

    monkeypatch.setattr(curate_module, "EPHEMERAL_MARKERS", REAL_EPHEMERAL_MARKERS)
    for scratch in (
        "/tmp/bench-1",
        "/private/var/folders/0d/abc/T/run",
        "/Users/someone/.local/state/tool/run-7",
        "/Users/someone/work/scratchpad",
    ):
        assert is_ephemeral_project(scratch), scratch


def test_zero_budget_makes_no_call(store: AutoStore) -> None:
    key = seed(store, "s1")
    runner = FakeRunner()

    report = curate(store, runner, max_calls=0)

    assert runner.prompts == [] and (report.llm_calls, report.sessions_left) == (0, 1)
    assert pending_keys(store) == [key]


def test_coding_projects_are_served_before_openclaw_only_projects(store: AutoStore) -> None:
    agent_keys = [
        seed(store, f"agent{index}", ALPHA, scope="openclaw", minute=index) for index in range(3)
    ]
    seed(store, "human", BETA, minute=9)
    runner = FakeRunner()

    report = curate(store, runner, max_calls=1)

    assert len(runner.prompts) == 1 and f"專案「{BETA}」" in runner.prompts[0]
    assert report.sessions_left == 3
    assert pending_keys(store) == sorted(agent_keys)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (RuntimeError("LLM command exited 1: quota"), "call failed: LLM command exited 1: quota"),
        (subprocess.TimeoutExpired("claude", 180), "call failed"),
        ("抱歉，我無法處理這個要求。", "reply was not a JSON object"),
        ("[1, 2, 3]", "reply was not a JSON object"),
    ],
)
def test_failed_call_or_non_json_reply_leaves_sessions_pending(
    store: AutoStore, reply: Reply, expected: str
) -> None:
    key = seed(store, "s1")
    runner = FakeRunner(reply, default=summarise)

    failed = curate(store, runner, max_calls=3)

    assert (failed.llm_calls, failed.llm_failures, failed.sessions_left) == (1, 1, 1)
    assert failed.sessions_curated == 0 and failed.rejections[0].startswith(expected)
    assert pending_keys(store) == [key]
    assert store.memories() == []

    retried = curate(store, runner, max_calls=3)

    assert (retried.llm_failures, retried.sessions_curated, retried.memories_added) == (0, 1, 1)
    assert pending_keys(store) == []


def test_openclaw_only_memory_is_widened_when_a_coding_session_says_the_same(
    store: AutoStore,
) -> None:
    agent_key = seed(store, "agent", scope="openclaw")
    add = {"op": "add", "kind": "decision", "text": PARQUET, "evidence": ["S1"]}
    curate(store, FakeRunner({"memory": [add]}), max_calls=3)

    [memory] = store.memories(project=ALPHA, reader="openclaw")
    assert memory.scope == "openclaw" and memory.evidence == (agent_key,)

    human_key = seed(store, "human", minute=5)
    curate(store, FakeRunner({"memory": [add]}), max_calls=3)

    [widened] = store.memories(project=ALPHA)
    assert widened.memory_id == memory.memory_id and widened.scope == "coding"
    assert widened.evidence == tuple(sorted((agent_key, human_key)))
    assert store.memories(project=ALPHA, reader="openclaw") == []


def test_memory_backed_by_a_coding_session_in_a_mixed_batch_is_coding_scope(
    store: AutoStore,
) -> None:
    seed(store, "agent", scope="openclaw", minute=1)
    seed(store, "human", minute=2)

    def reply(prompt: str) -> dict[str, object]:
        agent, human = ref_of(prompt, "agent"), ref_of(prompt, "human")
        return {
            "memory": [
                {"op": "add", "kind": "fact", "text": "只有 agent 說的", "evidence": [agent]},
                {"op": "add", "kind": "fact", "text": "兩邊都說的", "evidence": [agent, human]},
            ]
        }

    curate(store, FakeRunner(reply), max_calls=3)

    scopes = {memory.text: memory.scope for memory in store.memories(project=ALPHA)}
    assert scopes == {"只有 agent 說的": "openclaw", "兩邊都說的": "coding"}


def test_parse_cli_output_reads_the_claude_envelope_and_passes_plain_text_through() -> None:
    envelope = json.dumps(
        {
            "type": "result",
            "result": '{"sessions": []}',
            "usage": {
                "input_tokens": 12,
                "cache_read_input_tokens": 300,
                "cache_creation_input_tokens": 40,
                "output_tokens": 7,
            },
            "total_cost_usd": 0.0012,
        }
    )

    assert parse_cli_output(envelope) == LlmReply(
        text='{"sessions": []}', input_tokens=352, output_tokens=7, cost_usd=0.0012
    )
    assert parse_cli_output("plain words, not JSON") == LlmReply(text="plain words, not JSON")
    bare = '{"sessions": [], "memory": []}'
    assert parse_cli_output(bare) == LlmReply(text=bare), "another CLI printing the object itself"
    assert parse_cli_output('{"result": "x"}') == LlmReply(text="x")


def test_extract_json_tolerates_code_fences_and_leading_prose() -> None:
    payload = {"sessions": [{"ref": "S1"}], "memory": []}
    raw = json.dumps(payload, ensure_ascii=False)

    assert extract_json(raw) == payload
    assert extract_json(f"```json\n{raw}\n```") == payload
    assert extract_json(f"```\n{raw}\n```") == payload
    assert extract_json(f"好的，整理結果如下：\n{raw}\n以上。") == payload
    assert extract_json(f"格式 {{ref}} 的說明之後才是結果 {raw}") == payload
    assert extract_json("沒有任何 JSON") is None
    assert extract_json("[1, 2]") is None
    assert extract_json('{"sessions": [') is None


def test_default_runner_pipes_the_prompt_to_the_command_and_raises_on_failure() -> None:
    assert default_runner("cat")("提示內容\n第二行") == LlmReply(text="提示內容\n第二行")
    with pytest.raises(RuntimeError, match="exited 1"):
        default_runner("false")("提示內容")


# ---- store -------------------------------------------------------------------------------


def test_mark_stale_only_touches_active_memory_older_than_the_cutoff(store: AutoStore) -> None:
    old = remember(store, "很久沒人確認的規則", now=NOW - timedelta(days=200))
    fresh = remember(store, "最近確認過的規則", now=NOW - timedelta(days=10))
    retired = remember(store, "早就退役的規則", now=NOW - timedelta(days=300))
    store.set_memory_status(retired, "retired", reason="old", run_id=None, evidence=[])

    changed = store.mark_stale(older_than_days=120, run_id="run-9", now=NOW)

    assert changed == 1
    statuses = {m.memory_id: m.status for m in store.memories(status=None)}
    assert statuses == {old: "stale", fresh: "active", retired: "retired"}
    op, detail, _ = store.events(old)[-1]
    assert op == "stale" and detail["reason"] == "not confirmed for 120 days"
    assert [op for op, _, _ in store.events(fresh)] == ["add"]
    assert [op for op, _, _ in store.events(retired)] == ["add", "retired"]
    assert store.mark_stale(older_than_days=120, run_id="run-10", now=NOW) == 0
    assert store.counts()["memories_stale"] == 1


def test_stale_memory_becomes_active_again_when_added_again(store: AutoStore) -> None:
    memory_id = remember(store, PARQUET, now=NOW - timedelta(days=200))
    store.mark_stale(older_than_days=120, run_id=None, now=NOW)

    again, created = store.add_memory(
        project=ALPHA,
        scope="coding",
        kind="decision",
        text=PARQUET,
        evidence=["codex:new"],
        run_id=None,
        now=NOW,
    )

    assert (again, created) == (memory_id, False)
    [memory] = store.memories(project=ALPHA)
    assert memory.status == "active" and memory.last_confirmed_at == now_iso(NOW)


def searchable(store: AutoStore) -> dict[str, str]:
    refs = {
        "coding": remember(store, PARQUET),
        "agent": remember(store, AGENT_NOTE, scope="openclaw", kind="procedure"),
        "beta": remember(store, BETA_NOTE, BETA),
        "retired": remember(store, "Parquet 舊規則：分區鍵用 created_at"),
    }
    store.set_memory_status(refs["retired"], "retired", reason="old", run_id=None, evidence=[])
    refs["session"] = seed(store, "s1", minute=1)
    summary = "把 PARQUET 匯出改成冪等寫入"
    store.mark_curated(refs["session"], 1, title="匯出增量化", summary=summary)
    refs["agent_session"] = seed(store, "s2", scope="openclaw", minute=2)
    store.mark_curated(refs["agent_session"], 1, title="agent 巡檢", summary="檢查 parquet 壓縮")
    seed(store, "s3", minute=3, digest="U: parquet 還沒有摘要的 Session")
    return refs


def found(hits: list[tuple[str, str, str | None, str]]) -> set[str]:
    return {ref for _, ref, _, _ in hits}


def test_search_ranks_by_matched_terms_and_ignores_case(store: AutoStore) -> None:
    refs = searchable(store)

    hits = store.search("parquet")

    assert found(hits) == {
        refs["coding"],
        refs["agent"],
        refs["session"],
        refs["agent_session"],
    }
    kinds = {ref: kind for kind, ref, _, _ in hits}
    assert kinds[refs["coding"]] == "memory:decision" and kinds[refs["session"]] == "session"
    assert all(project == ALPHA for _, _, project, _ in hits)
    # A row holding every term comes first; one holding half of them may follow.
    ranked = store.search("PARQUET Event_Date")
    assert ranked[0][1] == refs["coding"] and found(ranked) >= {refs["coding"]}
    assert store.search("  冪等寫入   匯出增量化 ")[0][1] == refs["session"]
    # A whole sentence works too: it is matched by its words and character pairs.
    assert store.search("匯出格式要用 parquet 嗎")[0][1] in {refs["coding"], refs["agent"]}
    assert store.search("parquet 完全不存在的詞語很多個字") == []
    assert store.search("") == [] and store.search("   ") == []
    assert len(store.search("parquet", limit=1)) == 1


def test_search_respects_project_filter_and_reader_scope(store: AutoStore) -> None:
    refs = searchable(store)

    assert found(store.search("invoice_line")) == {refs["beta"]}
    assert store.search("invoice_line", project=ALPHA) == []
    assert found(store.search("invoice_line", project=BETA)) == {refs["beta"]}
    assert store.search("parquet", project=BETA) == []
    assert found(store.search("parquet", reader="openclaw")) == {
        refs["agent"],
        refs["agent_session"],
    }
    assert store.search("event_date", reader="openclaw") == []
    assert store.search("冪等寫入", reader="openclaw") == []
    assert store.search("invoice_line", reader="openclaw") == []


def test_open_read_only_returns_none_for_a_missing_file_and_cannot_create_or_write(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "nowhere" / DB_NAME

    assert AutoStore.open_read_only(missing) is None
    assert not missing.exists() and not missing.parent.exists()
    assert AutoStore.open_read_only(tmp_path) is None, "a directory is not a store"

    path = tmp_path / "auto" / DB_NAME
    writable = AutoStore.open(path)
    memory_id = remember(writable, PARQUET)
    writable.close()
    before = snapshot(path.parent)
    reader = AutoStore.open_read_only(path)
    assert reader is not None
    try:
        assert [memory.memory_id for memory in reader.memories()] == [memory_id]
        with pytest.raises(sqlite3.OperationalError):
            reader.set_meta("k", "v")
        with pytest.raises(sqlite3.OperationalError):
            reader.set_memory_status(memory_id, "retired", reason="x", run_id=None, evidence=[])
    finally:
        reader.close()
    assert snapshot(path.parent) == before


# ---- files -------------------------------------------------------------------------------


def inflate(path: Path) -> bytes:
    return subprocess.run(["zstd", "-dc", path.as_posix()], capture_output=True, check=True).stdout


def write_zst(path: Path, content: bytes) -> None:
    packed = subprocess.run(["zstd", "-c"], input=content, capture_output=True, check=True).stdout
    path.write_bytes(packed)


class ClaudeTree:
    def __init__(self, machine: Machine) -> None:
        self.root = machine.claude_root
        self.archive = machine.store_dir / "archive" / "claude"
        self.cold = machine.claude()
        self.subagent = self.cold.parent / CLAUDE_ID / "subagents" / "agent-a1.jsonl"
        self.subagent.parent.mkdir(parents=True)
        self.subagent.write_text("\n".join(claude_lines(NATIVE_TURNS)) + "\n", encoding="utf-8")
        self.recent = self.cold.with_name("c1a0de00-2222-4aaa-8aaa-0000000000a2.jsonl")
        self.recent.write_text("\n".join(claude_lines(NATIVE_TURNS)) + "\n", encoding="utf-8")
        age(self.cold, 20)
        age(self.subagent, 15)
        age(self.recent, 1)

    def target(self, path: Path) -> Path:
        return self.archive / path.relative_to(self.root).with_name(path.name + ".zst")


@needs_zstd
def test_archive_claude_copies_only_cold_transcripts_and_leaves_originals_alone(
    machine: Machine,
) -> None:
    tree = ClaudeTree(machine)
    store = machine.store()
    originals = {path: path.read_bytes() for path in (tree.cold, tree.subagent, tree.recent)}
    before = snapshot(tree.root)

    report = archive_claude(store, tree.root, tree.archive, now=NOW, min_age_days=10)

    assert (report.done, report.skipped_recent, report.failed) == (2, 1, 0)
    assert snapshot(tree.root) == before
    assert {path: path.read_bytes() for path in originals} == originals
    for path in (tree.cold, tree.subagent):
        assert inflate(tree.target(path)) == originals[path]
    assert not tree.target(tree.recent).exists()
    assert sorted(snapshot(tree.archive)) == sorted(
        tree.target(path).relative_to(tree.archive).as_posix()
        for path in (tree.cold, tree.subagent)
    )
    assert report.source_bytes == len(originals[tree.cold]) + len(originals[tree.subagent])
    assert report.target_bytes == sum(
        tree.target(path).stat().st_size for path in (tree.cold, tree.subagent)
    )


@needs_zstd
def test_archive_claude_is_idempotent_and_rearchives_a_changed_file(machine: Machine) -> None:
    tree = ClaudeTree(machine)
    store = machine.store()
    archive_claude(store, tree.root, tree.archive, now=NOW, min_age_days=10)
    archived = snapshot(tree.archive)

    again = archive_claude(store, tree.root, tree.archive, now=NOW, min_age_days=10)

    assert (again.done, again.skipped_done, again.skipped_recent) == (0, 2, 1)
    assert snapshot(tree.archive) == archived

    with tree.cold.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(claude_lines([("user", "之後又補了一句")], start=40)) + "\n")
    age(tree.cold, 12)
    changed = archive_claude(store, tree.root, tree.archive, now=NOW, min_age_days=10)

    assert (changed.done, changed.skipped_done) == (1, 1)
    assert inflate(tree.target(tree.cold)) == tree.cold.read_bytes()

    tree.target(tree.subagent).unlink()
    restored = archive_claude(store, tree.root, tree.archive, now=NOW, min_age_days=10)

    assert (restored.done, restored.skipped_done) == (1, 1), "a lost archive is written again"
    assert inflate(tree.target(tree.subagent)) == tree.subagent.read_bytes()


@needs_zstd
def test_archive_claude_dry_run_changes_nothing(machine: Machine) -> None:
    tree = ClaudeTree(machine)
    store = machine.store()
    before = snapshot(tree.root)

    report = archive_claude(store, tree.root, tree.archive, now=NOW, min_age_days=10, dry_run=True)

    assert (report.done, report.skipped_recent, report.target_bytes) == (2, 1, 0)
    assert not tree.archive.exists() and snapshot(tree.root) == before
    assert store.file_action(tree.cold) is None


class CodexTree:
    """An OpenClaw agent's Codex home with a cold, a recent and a busy rollout."""

    COLD = "01a1c0de-0011-7000-8000-00000000c011"
    RECENT = "01a1c0de-0012-7000-8000-00000000c012"
    BUSY = "01a1c0de-0013-7000-8000-00000000c013"

    def __init__(self, machine: Machine) -> None:
        self.machine = machine
        self.home = machine.nested_home()
        self.holding = machine.store_dir / "holding"
        self.cold = machine.nested_codex(self.COLD)
        self.recent = machine.nested_codex(self.RECENT)
        self.busy = machine.nested_codex(self.BUSY)
        locks = self.home.root.parent / "thread-writer-locks"
        locks.mkdir()
        (locks / f"{self.BUSY}.lock").touch()
        age(self.cold, 20)
        age(self.recent, 1)
        age(self.busy, 20)
        self.raw = self.cold.read_bytes()
        self.mtime_ns = self.cold.stat().st_mtime_ns
        self.zst = self.cold.with_name(self.cold.name + ".zst")
        self.held = (
            self.holding / "codex@openclaw_kurisu" / self.cold.relative_to(self.home.root)
        )

    def compress(self, store: AutoStore, *, dry_run: bool = False) -> dict[str, object]:
        return compress_codex_rollouts(
            store, (self.home,), self.holding, now=NOW, min_age_days=14, dry_run=dry_run
        ).as_dict()

    def messages(self, path: Path) -> list[tuple[str, str]]:
        adapter = CodexAdapter(self.home.root, homes=[self.home])
        record = adapter.read_file(path, home=self.home)
        assert record.session_id == self.COLD and record.agent == "openclaw/kurisu"
        return [(message.role, message.text) for message in record.messages]


def after_hold() -> datetime:
    """A moment past the holding period of a compression done just now (wall clock)."""
    return datetime.now(UTC) + timedelta(days=4)


@needs_zstd
def test_cold_rollout_is_compressed_in_place_and_the_original_is_held(machine: Machine) -> None:
    tree = CodexTree(machine)
    store = machine.store()
    messages = tree.messages(tree.cold)
    untouched = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (tree.recent, tree.busy)
    }

    report = tree.compress(store)

    assert (report["done"], report["skipped_recent"], report["skipped_busy"]) == (1, 1, 1)
    assert report["failed"] == 0 and report["source_bytes"] == len(tree.raw)
    assert not tree.cold.exists() and tree.zst.is_file()
    assert tree.zst.stat().st_mtime_ns == tree.mtime_ns
    assert report["target_bytes"] == tree.zst.stat().st_size < len(tree.raw)
    assert inflate(tree.zst) == tree.raw
    assert tree.held.read_bytes() == tree.raw
    for path, (content, mtime_ns) in untouched.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == (content, mtime_ns)
        assert not path.with_name(path.name + ".zst").exists()
    assert tree.messages(tree.zst) == messages and len(messages) >= len(TURNS)

    again = tree.compress(store)

    assert (again["done"], again["examined"], again["skipped_busy"]) == (0, 1, 1)


@needs_zstd
def test_compress_dry_run_changes_nothing(machine: Machine) -> None:
    tree = CodexTree(machine)
    store = machine.store()
    before = snapshot(machine.agents)

    report = tree.compress(store, dry_run=True)

    assert (report["done"], report["target_bytes"]) == (1, 0)
    assert snapshot(machine.agents) == before and not tree.holding.exists()
    assert store.file_action(tree.cold) is None


@needs_zstd
def test_held_original_is_purged_only_after_the_holding_period(machine: Machine) -> None:
    tree = CodexTree(machine)
    store = machine.store()
    tree.compress(store)

    early = purge_holding(store, now=datetime.now(UTC), hold_days=3)

    assert (early.examined, early.purged) == (0, 0) and tree.held.exists()

    rehearsal = purge_holding(store, now=after_hold(), hold_days=3, dry_run=True)

    assert (rehearsal.purged, rehearsal.purged_bytes) == (1, len(tree.raw))
    assert tree.held.exists()

    report = purge_holding(store, now=after_hold(), hold_days=3)

    assert (report.purged, report.purged_bytes, report.failed) == (1, len(tree.raw), 0)
    assert not tree.held.exists() and inflate(tree.zst) == tree.raw
    assert purge_holding(store, now=after_hold(), hold_days=3).examined == 0
    assert restore_compressed(store, tree.cold) is False, "nothing left to restore from"


@needs_zstd
@pytest.mark.parametrize("damage", ["missing", "other-content", "garbage"])
def test_held_original_is_kept_when_the_compressed_file_is_missing_or_altered(
    machine: Machine, damage: str
) -> None:
    tree = CodexTree(machine)
    store = machine.store()
    tree.compress(store)
    if damage == "missing":
        tree.zst.unlink()
    elif damage == "other-content":
        write_zst(tree.zst, b'{"type": "session_meta"}\n')
    else:
        tree.zst.write_bytes(b"not a zstd frame")

    report = purge_holding(store, now=after_hold(), hold_days=3)

    assert (report.examined, report.purged, report.failed) == (1, 0, 1)
    assert tree.cold.as_posix() in report.errors[0]
    assert tree.held.read_bytes() == tree.raw
    assert restore_compressed(store, tree.cold) is True
    assert tree.cold.read_bytes() == tree.raw and not tree.zst.exists()


@needs_zstd
def test_resumed_session_makes_the_held_original_safe_to_purge(machine: Machine) -> None:
    tree = CodexTree(machine)
    store = machine.store()
    tree.compress(store)
    # Codex re-inflates the rollout when the session is resumed, then appends to it.
    tree.zst.unlink()
    tree.cold.write_bytes(tree.raw + b'{"type": "response_item", "ordinal": 99}\n')
    resumed = tree.cold.read_bytes()

    report = purge_holding(store, now=after_hold(), hold_days=3)

    assert (report.purged, report.failed) == (1, 0)
    assert not tree.held.exists() and tree.cold.read_bytes() == resumed


@needs_zstd
def test_restore_puts_the_original_back(machine: Machine) -> None:
    tree = CodexTree(machine)
    store = machine.store()
    messages = tree.messages(tree.cold)
    tree.compress(store)

    assert restore_compressed(store, tree.cold) is True

    assert tree.cold.read_bytes() == tree.raw and tree.cold.stat().st_mtime_ns == tree.mtime_ns
    assert not tree.zst.exists() and not tree.held.exists()
    assert tree.messages(tree.cold) == messages
    assert restore_compressed(store, tree.cold) is False
    assert restore_compressed(store, tree.recent) is False, "never compressed"
    assert purge_holding(store, now=after_hold(), hold_days=3).examined == 0


# ---- export ------------------------------------------------------------------------------


def exportable(store: AutoStore) -> dict[str, str]:
    refs = {
        "coding": remember(store, PARQUET, now=NOW),
        "agent": remember(store, AGENT_NOTE, scope="openclaw", kind="procedure", now=NOW),
        "beta": remember(store, BETA_NOTE, BETA, kind="fact", now=NOW),
        "retired": remember(store, "已退役：匯出格式採用 CSV", now=NOW),
    }
    store.set_memory_status(refs["retired"], "retired", reason="old", run_id=None, evidence=[])
    refs["session"] = seed(store, "s1", minute=1)
    store.mark_curated(refs["session"], 1, title="匯出增量化", summary="改成冪等寫入，剩重試測試")
    refs["agent_session"] = seed(store, "s2", scope="openclaw", minute=2)
    store.mark_curated(refs["agent_session"], 1, summary="agent 檢查了壓縮排程")
    seed(store, "s3", minute=3)  # not summarised yet: nothing to export
    return refs


def test_export_writes_project_and_session_markdown_with_front_matter(
    store: AutoStore, tmp_path: Path
) -> None:
    refs = exportable(store)
    root = tmp_path / "memory"

    report = export(store, root)

    assert report.as_dict() == {
        "projects": 2,
        "sessions": 2,
        "files_written": 4,
        "files_unchanged": 0,
        "files_removed": 0,
    }
    assert sorted(snapshot(root)) == [
        f"projects/{project_slug(ALPHA)}.md",
        f"projects/{project_slug(BETA)}.md",
        "sessions/2026-11/codex--s1.md",
        "sessions/2026-11/codex--s2.md",
    ]
    assert re.fullmatch(r"proj-alpha-etl-[0-9a-f]{8}", project_slug(ALPHA))
    project = (root / "projects" / f"{project_slug(ALPHA)}.md").read_text(encoding="utf-8")
    assert project.splitlines()[:7] == [
        "---",
        "type: project_memory",
        f"project: {ALPHA}",
        "memories: 2",
        "---",
        "",
        f"# 專案記憶：{ALPHA}",
    ]
    assert "## 決定" in project and "## 做法" in project
    assert f"- {PARQUET}（{refs['coding']}；確認於 2026-11-13；來源 codex:earlier）" in project
    assert AGENT_NOTE in project and "CSV" not in project and BETA_NOTE not in project
    session = (root / "sessions" / "2026-11" / "codex--s1.md").read_text(encoding="utf-8")
    assert session.splitlines() == [
        "---",
        "type: session_summary",
        "source: codex:s1",
        "tool: codex",
        "scope: coding",
        f"project: {ALPHA}",
        "last_activity: 2026-11-10T01:01:00+00:00",
        "summary_origin: llm",
        "---",
        "",
        "# 匯出增量化",
        "",
        "改成冪等寫入，剩重試測試",
    ]
    untitled = (root / "sessions" / "2026-11" / "codex--s2.md").read_text(encoding="utf-8")
    assert "scope: openclaw" in untitled and "# codex session s2" in untitled


def test_second_export_without_changes_writes_no_file(store: AutoStore, tmp_path: Path) -> None:
    exportable(store)
    root = tmp_path / "memory"
    export(store, root)
    before = snapshot(root)

    again = export(store, root)

    assert (again.files_written, again.files_unchanged) == (0, 4)
    assert snapshot(root) == before

    remember(store, "新增的一條限制", kind="constraint", now=NOW)
    changed = export(store, root)

    assert (changed.files_written, changed.files_unchanged) == (1, 3)


def test_openclaw_reader_exports_only_openclaw_content(store: AutoStore, tmp_path: Path) -> None:
    exportable(store)
    root = tmp_path / "agent-memory"

    report = export(store, root, reader="openclaw")

    assert (report.projects, report.sessions, report.files_written) == (1, 1, 2)
    assert sorted(snapshot(root)) == [
        f"projects/{project_slug(ALPHA)}.md",
        "sessions/2026-11/codex--s2.md",
    ]
    everything = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.md"))
    assert AGENT_NOTE in everything and "memories: 1" in everything
    for hidden in (PARQUET, BETA_NOTE, "冪等寫入", "scope: coding"):
        assert hidden not in everything


# ---- run_auto ----------------------------------------------------------------------------


def stamp_recent(files: list[Path]) -> None:
    for path in files:
        age(path, 1 / 48)  # half an hour before NOW


def outcome(report: dict[str, object], step: str) -> dict[str, object]:
    """What a step reported, apart from how long it took."""
    result = report[step]
    assert isinstance(result, dict) and isinstance(result["seconds"], float), step
    return {key: value for key, value in result.items() if key != "seconds"}


def index_command(marker_file: Path) -> tuple[str, ...]:
    script = (
        "import os, pathlib, sys; "
        "pathlib.Path(sys.argv[2]).write_text(sys.argv[1] + '|' + os.environ.get('S2M_MARK', ''))"
    )
    return (sys.executable, "-c", script, "{memory_dir}", marker_file.as_posix())


def test_run_reports_every_step_and_a_second_run_reuses_the_watermark(machine: Machine) -> None:
    stamp_recent(machine.five_sessions())
    runner = FakeRunner(default=summarise)
    config = machine.config()

    report = run_auto(config, runner=runner, now=NOW)

    for step in ALL_STEPS:
        result = report[step]
        assert isinstance(result, dict) and isinstance(result["seconds"], float), step
        assert "error" not in result, (step, result)
    assert report["started_at"] == now_iso(NOW) and report["dry_run"] is False
    scanned, curated, exported = report["scan"], report["curate"], report["export"]
    assert isinstance(scanned, dict) and isinstance(curated, dict) and isinstance(exported, dict)
    assert (scanned["sessions_new"], scanned["read_errors"]) == (5, 0)
    assert scanned["since"] == now_iso(NOW - timedelta(days=30))
    # One call for the workspace's four sessions; the OpenClaw-native one was started in a
    # directory that does not exist on this machine, so there is nothing to remember for it.
    assert len(runner.prompts) == 1 and len(refs_in(runner.prompts[0])) == 4
    assert (curated["llm_calls"], curated["sessions_curated"], curated["sessions_ephemeral"]) == (
        1,
        4,
        1,
    )
    assert (curated["memories_added"], curated["memories_stale"]) == (1, 0)
    assert (exported["projects"], exported["sessions"], exported["files_written"]) == (1, 4, 5)
    assert outcome(report, "index") == {"skipped": "no index command configured"}
    totals = report["totals"]
    assert isinstance(totals, dict)
    assert (totals["sessions"], totals["sessions_pending"], totals["memories_active"]) == (5, 0, 1)
    project_file = config.memory_dir / "projects" / f"{project_slug(machine.work.as_posix())}.md"
    assert PARQUET in project_file.read_text(encoding="utf-8")
    store = machine.store()
    last = store.last_run()
    assert last is not None and last[0] == report["run_id"] and last[2]["totals"] == totals
    assert store.get_meta(LAST_SCAN_KEY) == now_iso(NOW)
    [memory] = store.memories()
    assert memory.project == machine.work.as_posix() and len(memory.evidence) == 1
    assert memory.evidence[0] in {CODEX_KEY, NESTED_KEY, CLAUDE_KEY, GROK_KEY}

    later = NOW + timedelta(hours=1)
    second = run_auto(config, runner=runner, now=later)

    rescanned, recurated, reexported = second["scan"], second["curate"], second["export"]
    assert isinstance(rescanned, dict) and isinstance(recurated, dict)
    assert isinstance(reexported, dict)
    assert rescanned["since"] == now_iso(NOW - timedelta(hours=2)), "watermark minus the overlap"
    # The four files changed inside the overlap are looked at again; the older native
    # session is not even listed.
    assert (rescanned["files_seen"], rescanned["sessions_unchanged"]) == (4, 4)
    assert (rescanned["sessions_new"], rescanned["sessions_updated"]) == (0, 0)
    assert (recurated["llm_calls"], recurated["sessions_curated"]) == (0, 0)
    assert len(runner.prompts) == 1
    assert (reexported["files_written"], reexported["files_unchanged"]) == (0, 5)
    assert second["run_id"] != report["run_id"]
    assert store.get_meta(LAST_SCAN_KEY) == now_iso(later)


def test_dry_run_creates_no_memory_archive_or_holding_and_makes_no_llm_call(
    machine: Machine,
) -> None:
    files = machine.five_sessions()
    stamp_recent(files)
    age(files[1], 20)  # the agent's rollout is cold enough to compress
    age(files[2], 20)  # the Claude transcript is cold enough to archive
    marker_file = machine.tmp / "indexed.txt"
    before = {root: snapshot(root) for root in (machine.home, machine.agents)}
    runner = FakeRunner(default=summarise)
    config = machine.config(dry_run=True, index_command=index_command(marker_file))

    report = run_auto(config, runner=runner, now=NOW)

    assert runner.prompts == []
    assert report["dry_run"] is True
    assert outcome(report, "curate") == {"skipped": "dry run", "sessions_pending": 5}
    assert outcome(report, "export") == {"skipped": "dry run"}
    assert outcome(report, "index") == {"skipped": "dry run"}
    assert not config.memory_dir.exists() and not marker_file.exists()
    assert not (machine.store_dir / "archive").exists() and not config.holding_dir.exists()
    assert {root: snapshot(root) for root in before} == before
    # A rehearsal works on an in-memory copy: not even the database is created.
    assert sorted(path.name for path in machine.store_dir.iterdir()) == [".lock"]
    scanned = report["scan"]
    assert isinstance(scanned, dict) and scanned["sessions_new"] == 5
    compressed, archived = report["compress"], report["archive"]
    assert isinstance(compressed, dict) and isinstance(archived, dict)
    if shutil.which("zstd") is not None:
        assert (compressed["done"], archived["done"]) == (1, 1)
        assert (compressed["target_bytes"], archived["target_bytes"]) == (0, 0)


def test_second_run_is_refused_while_the_lock_is_held(machine: Machine) -> None:
    machine.five_sessions()
    runner = FakeRunner(default=summarise)
    config = machine.config()
    machine.store_dir.mkdir(parents=True)

    with (machine.store_dir / ".lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = run_auto(config, runner=runner, now=NOW)

    assert report["skipped"] == "another run holds the lock"
    assert "run_id" not in report and not set(ALL_STEPS) & set(report)
    assert runner.prompts == [] and not machine.db.exists()

    released = run_auto(config, runner=runner, now=NOW)

    assert "skipped" not in released and "run_id" in released and len(runner.prompts) == 1


def test_failing_step_is_reported_under_its_name_and_later_steps_still_run(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp_recent(machine.five_sessions())
    marker_file = machine.tmp / "indexed.txt"
    config = machine.config(
        steps=("scan", "bogus", "curate", "export", "index"),
        index_command=index_command(marker_file),
        index_env={"S2M_MARK": "from-config"},
    )

    def broken_export(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("disk full")

    monkeypatch.setattr(run_module, "export", broken_export)

    report = run_auto(config, runner=FakeRunner(ValueError("model exploded")), now=NOW)

    curated, unknown, exported = report["curate"], report["bogus"], report["export"]
    assert isinstance(curated, dict) and isinstance(unknown, dict) and isinstance(exported, dict)
    # A model call that blows up is a failed call, not a failed step.
    assert curated["llm_failures"] == curated["llm_calls"] >= 1 and curated["memories_added"] == 0
    assert unknown["error"] == "unknown step bogus"
    assert exported["error"] == "RuntimeError: disk full" and "seconds" in exported
    assert "compress" not in report and "archive" not in report
    indexed, totals = report["index"], report["totals"]
    assert isinstance(indexed, dict) and isinstance(totals, dict)
    assert indexed["exit_code"] == 0 and indexed["stderr_tail"] == ""
    assert marker_file.read_text() == f"{config.memory_dir.as_posix()}|from-config"
    assert totals["sessions_pending"] == 4, "the failed batch waits for the next run"
    assert "finished_at" in report


def test_index_command_failure_is_reported_not_raised(machine: Machine) -> None:
    config = machine.config(steps=("index",), index_command=("false",))
    missing = machine.config(steps=("index",), index_command=("s2m-no-such-indexer",))

    failed = run_auto(config, now=NOW)["index"]
    absent = run_auto(missing, now=NOW)["index"]

    assert isinstance(failed, dict) and failed["exit_code"] == 1
    assert isinstance(absent, dict) and "s2m-no-such-indexer" in str(absent["error"])


@needs_zstd
def test_run_compresses_and_archives_cold_files_without_changing_session_identity(
    machine: Machine,
) -> None:
    files = machine.five_sessions()
    stamp_recent(files)
    rollout, transcript = files[1], files[2]
    age(rollout, 20)
    age(transcript, 20)
    original = rollout.read_bytes()
    config = machine.config()

    report = run_auto(config, runner=FakeRunner(default=summarise), now=NOW)

    compressed, archived = report["compress"], report["archive"]
    assert isinstance(compressed, dict) and isinstance(archived, dict)
    assert (compressed["done"], compressed["failed"]) == (1, 0)
    assert compressed["purge"]["purged"] == 0  # type: ignore[index]
    assert (archived["done"], archived["failed"]) == (1, 0)
    assert not rollout.exists() and inflate(rollout.with_name(rollout.name + ".zst")) == original
    held = config.holding_dir / "codex@openclaw_kurisu" / rollout.relative_to(machine.nested)
    assert held.read_bytes() == original
    archive = config.archive_dir / transcript.relative_to(machine.claude_root)
    assert inflate(archive.with_name(archive.name + ".zst")) == transcript.read_bytes()
    assert (machine.main / rollout.relative_to(machine.nested).parent).is_dir()
    assert not list(machine.main.rglob("*.zst")), "the main Codex home is left to Codex"

    rescan = machine.scan()

    assert (rescan.sessions_new, rescan.sessions_updated, rescan.sessions_unchanged) == (0, 0, 5)


# ---- hook --------------------------------------------------------------------------------


def hook_config(machine: Machine, auto_db: Path | None) -> HookConfig:
    return HookConfig(
        agent="claude",
        session_roots={"codex": machine.main, "claude": machine.claude_root},
        codex_memory_root=machine.memory_root,
        claude_projects_root=machine.claude_root,
        ledger_path=machine.tmp / "state" / "ledger.json",
        temp_roots=(),
        auto_db=auto_db,
    )


def test_hook_injects_this_projects_distilled_memory_only(machine: Machine) -> None:
    store = machine.store()
    work = machine.work.as_posix()
    remember(store, PARQUET, work)
    remember(store, "lifecycle 會在 7 天後刪除中間檔", work, kind="pitfall")
    remember(store, BETA_NOTE, BETA)
    retired = remember(store, "已退役：匯出格式採用 CSV", work)
    store.set_memory_status(retired, "retired", reason="old", run_id=None, evidence=[])
    before = snapshot(machine.store_dir)
    payload = {"session_id": "new-session", "cwd": work, "source": "startup"}

    result = run_hook(hook_config(machine, machine.db), payload, {}, now=NOW)

    assert result.context is not None
    assert "## 這個專案的記憶" in result.context
    assert f"- [決定] {PARQUET}" in result.context
    assert "- [坑] lifecycle 會在 7 天後刪除中間檔" in result.context
    assert BETA_NOTE not in result.context and "CSV" not in result.context
    assert snapshot(machine.store_dir) == before, "the hook only reads the store"

    elsewhere = {**payload, "cwd": (machine.tmp / "work" / "unrelated").as_posix()}
    assert run_hook(hook_config(machine, machine.db), elsewhere, {}, now=NOW).context is None


def test_hook_is_silent_when_the_auto_store_is_missing(machine: Machine) -> None:
    payload = {"session_id": "new", "cwd": machine.work.as_posix(), "source": "startup"}
    missing = machine.tmp / "no-store" / DB_NAME

    result = run_hook(hook_config(machine, missing), payload, {}, now=NOW)

    assert result.context is None
    assert not missing.parent.exists()
    assert run_hook(hook_config(machine, None), payload, {}, now=NOW).context is None


# ---- CLI ---------------------------------------------------------------------------------


def cli(*arguments: str) -> tuple[int, str]:
    result = runner_cli.invoke(app, ["auto", *arguments])
    return result.exit_code, result.output


@pytest.fixture
def filled(machine: Machine) -> dict[str, str]:
    """A store with one coding and one OpenClaw-only memory of the same workspace."""
    store = machine.store()
    refs = {
        "coding": remember(store, PARQUET),
        "agent": remember(store, AGENT_NOTE, scope="openclaw", kind="procedure"),
        "store": machine.store_dir.as_posix(),
    }
    machine.close()
    return refs


def test_cli_status_exits_1_without_a_store(machine: Machine) -> None:
    code, output = cli("status", "--store", machine.store_dir.as_posix())

    assert code == 1 and "no store yet" in output
    assert not machine.store_dir.exists()
    assert cli("search", "parquet", "--store", machine.store_dir.as_posix())[0] == 1
    assert not machine.store_dir.exists()


def test_cli_search_and_memory_respect_the_reader(filled: dict[str, str]) -> None:
    store = ["--store", filled["store"]]

    code, everything = cli("search", "parquet", *store)
    _, agent_only = cli("search", "parquet", "--reader", "openclaw", *store)
    _, listed = cli("memory", "--workspace", ALPHA, *store)
    _, agent_listed = cli("memory", "--workspace", ALPHA, "--reader", "openclaw", *store)

    assert code == 0
    assert PARQUET in everything and AGENT_NOTE in everything
    assert f"[memory:decision] {filled['coding']}｜{ALPHA}" in everything
    assert AGENT_NOTE in agent_only and PARQUET not in agent_only
    assert sorted(listed.splitlines()) == sorted(
        [
            f"{filled['agent']}｜active｜procedure｜{AGENT_NOTE}",
            f"{filled['coding']}｜active｜decision｜{PARQUET}",
        ]
    )
    assert agent_listed.splitlines() == [f"{filled['agent']}｜active｜procedure｜{AGENT_NOTE}"]
    assert cli("search", "event_date", "--reader", "openclaw", *store) == (0, "no match\n")
    assert cli("memory", "--workspace", BETA, *store) == (0, "no memory for this workspace\n")
    assert cli("search", "parquet", "--reader", "root", *store)[0] != 0
    assert cli("memory", "--workspace", ALPHA, "--reader", "root", *store)[0] != 0


def as_openclaw_workspace(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = machine.state_dir.parent / "workspaces" / "x"
    workspace.mkdir(parents=True)
    monkeypatch.chdir(workspace)


def as_openclaw_env(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION2MEMORY_READER", "openclaw")


def as_openclaw_codex_home(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", machine.nested.parent.as_posix())


@pytest.mark.parametrize(
    "become_agent", [as_openclaw_workspace, as_openclaw_env, as_openclaw_codex_home]
)
def test_cli_forces_openclaw_scope_for_an_openclaw_agent_even_with_reader_coding(
    machine: Machine,
    filled: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    become_agent: Callable[[Machine, pytest.MonkeyPatch], None],
) -> None:
    store = ["--store", filled["store"]]
    become_agent(machine, monkeypatch)

    _, searched = cli("search", "parquet", "--reader", "coding", *store)
    _, default = cli("search", "parquet", *store)
    _, listed = cli("memory", "--workspace", ALPHA, "--reader", "coding", *store)
    _, retire = cli(
        "memory", "--workspace", ALPHA, "--retire", filled["coding"], "--reader", "coding", *store
    )

    for output in (searched, default, listed):
        assert AGENT_NOTE in output and PARQUET not in output
    assert retire == "no active memory with that id\n"
    reader = AutoStore.open_read_only(machine.db)
    assert reader is not None
    try:
        kept = reader.get_memory(filled["coding"])
    finally:
        reader.close()
    assert kept is not None and kept.status == "active"


def test_cli_memory_retire_retires_and_an_openclaw_reader_cannot_retire_coding_memory(
    machine: Machine, filled: dict[str, str]
) -> None:
    store = ["--store", filled["store"]]
    workspace = ["memory", "--workspace", ALPHA]

    refused = cli(*workspace, "--retire", filled["coding"], "--reader", "openclaw", *store)
    assert refused == (0, "no active memory with that id\n")
    assert PARQUET in cli(*workspace, *store)[1]

    assert cli(*workspace, "--retire", filled["coding"], *store) == (0, "retired\n")
    assert cli(*workspace, "--retire", filled["coding"], *store) == (
        0,
        "no active memory with that id\n",
    )
    assert cli(*workspace, "--retire", "m_0000000000", *store)[1] == (
        "no active memory with that id\n"
    )
    assert PARQUET not in cli(*workspace, *store)[1]
    everything = cli(*workspace, "--all", *store)[1]
    assert f"{filled['coding']}｜retired｜decision｜{PARQUET}" in everything
    assert cli("search", "event_date", *store) == (0, "no match\n")

    own = cli(*workspace, "--retire", filled["agent"], "--reader", "openclaw", *store)
    assert own == (0, "retired\n"), "an agent may retire what agents produced"
    reader = AutoStore.open_read_only(machine.db)
    assert reader is not None
    try:
        op, detail, _ = reader.events(filled["coding"])[-1]
    finally:
        reader.close()
    assert op == "retired" and detail["reason"] == "retired by hand"


def test_cli_run_rejects_an_unknown_step(machine: Machine) -> None:
    code, output = cli("run", "--step", "bogus", "--store", machine.store_dir.as_posix())

    assert code == 2 and "unknown step bogus" in output
    assert not machine.store_dir.exists()


def test_cli_dry_run_scan_reads_only_the_redirected_home(machine: Machine) -> None:
    machine.five_sessions()
    assert Path("~/.codex/sessions").expanduser() == machine.main
    store = machine.store_dir.as_posix()

    code, output = cli("run", "--dry-run", "--step", "scan", "--json", "--store", store)

    assert code == 0, output
    report = json.loads(output)
    assert set(report) == {"started_at", "dry_run", "run_id", "scan", "totals", "finished_at"}
    assert report["dry_run"] is True
    assert (report["scan"]["sessions_new"], report["scan"]["files_seen"]) == (5, 5)
    assert report["scan"]["by_tool"] == {
        "codex": 2,
        "claude": 1,
        "grok": 1,
        "openclaw-native": 1,
    }
    # Nothing of a rehearsal is kept, so there is still no store to report on.
    assert sorted(path.name for path in machine.store_dir.iterdir()) == [".lock"]
    assert cli("status", "--store", store)[0] == 1


def test_cli_run_without_openclaw_skips_agent_homes_and_native_sessions(machine: Machine) -> None:
    machine.five_sessions()
    arguments = ["run", "--dry-run", "--step", "scan", "--no-openclaw"]

    code, output = cli(*arguments, "--store", machine.store_dir.as_posix())

    assert code == 0, output
    lines = dict(line.split(": ", 1) for line in output.splitlines())
    scanned = json.loads(lines["scan"])
    assert scanned["sessions_new"] == 3
    assert scanned["by_tool"] == {"codex": 1, "claude": 1, "grok": 1}


# ---- fixes found in review ---------------------------------------------------------------


def test_hook_gives_an_openclaw_agent_only_openclaw_memory(machine: Machine) -> None:
    from dataclasses import replace

    store = machine.store()
    work = machine.work.as_posix()
    remember(store, PARQUET, work)
    remember(store, AGENT_NOTE, work, scope="openclaw", kind="procedure")
    codex_summary_file(
        machine.memory_root, "t1", machine.work, "別的工具的記錄", hours_ago=1, name="a"
    )
    payload = {"session_id": "new", "cwd": work, "source": "startup"}
    config = replace(hook_config(machine, machine.db), openclaw_state_dir=machine.tmp / "oc-state")

    person = run_hook(config, payload, {}, now=NOW)
    agent = run_hook(config, payload, {"SESSION2MEMORY_READER": "openclaw"}, now=NOW)

    assert person.context is not None and PARQUET in person.context
    assert AGENT_NOTE in person.context and "別的工具的記錄" in person.context
    assert agent.context is not None and AGENT_NOTE in agent.context
    assert PARQUET not in agent.context, "memory other tools contributed to is withheld"
    assert "別的工具的記錄" not in agent.context and "上一個 Session 的交接" not in agent.context
    assert any("openclaw reader" in note for note in agent.notes)


def test_export_removes_files_whose_memory_is_gone(store: AutoStore, tmp_path: Path) -> None:
    refs = exportable(store)
    root = tmp_path / "memory"
    export(store, root)
    beta_file = root / "projects" / f"{project_slug(BETA)}.md"
    stray = root / "sessions" / "2026-01" / "codex--gone.md"
    stray.parent.mkdir(parents=True)
    stray.write_text("left over from an earlier export", encoding="utf-8")
    keep = root / "notes.txt"
    keep.write_text("not ours", encoding="utf-8")
    store.set_memory_status(refs["beta"], "retired", reason="old", run_id=None, evidence=[])

    report = export(store, root)

    assert report.files_removed == 2 and report.files_written == 0
    assert not beta_file.exists() and not stray.exists()
    assert keep.exists(), "only the generated projects/ and sessions/ trees are pruned"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefgh12345678",
        "ghp_" + "a1B2" * 6,
        "github_pat_" + "x" * 24,
        "AKIA" + "ABCDEFGH12345678",
        "Bearer " + "abcDEF123._-" * 3,
        "-----BEGIN OPENSSH PRIVATE KEY-----",
    ],
)
def test_secrets_never_reach_stored_memory(secret: str) -> None:
    from session2memory.auto.curate import scrub_secrets

    scrubbed = scrub_secrets(f"部署時使用 {secret} 這組憑證")

    assert secret not in scrubbed and "[REDACTED]" in scrubbed
    assert scrub_secrets("匯出格式採用 Parquet，task-123 與 sk 無關") == (
        "匯出格式採用 Parquet，task-123 與 sk 無關"
    )


def test_reply_to_a_bare_acknowledgement_does_not_replace_the_real_reply(
    tmp_path: Path,
) -> None:
    cwd = workspace_dir(tmp_path)
    turns = [
        ("user", GOAL),
        ("assistant", "已改成冪等寫入，還缺重試測試"),
        ("user", "Commit"),
        ("assistant", "已 commit 到分支"),
    ]
    path = codex_session(tmp_path / "codex", cwd, turns)

    digest = build_digest(CodexAdapter(path.parent).read_file(path))

    assert "已改成冪等寫入，還缺重試測試" in digest
    assert "已 commit 到分支" not in digest and "Commit" not in digest
