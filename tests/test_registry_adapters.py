"""Claude Code, OpenClaw native sqlite, Grok and Muse adapters wired into the source registry.

Fixtures mirror the on-disk shape observed on real installs (field names, nesting, the
non-message records that surround messages); all content is synthetic.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import pytest
from typer.testing import CliRunner

from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.grok import GrokAdapter
from session2memory.adapters.muse import MuseAdapter
from session2memory.adapters.openclaw_native import OpenClawNativeAdapter
from session2memory.cli import app
from session2memory.models import SessionRecord
from session2memory.pipeline import PipelineAdapter, run_pipeline
from session2memory.source_readers import default_readers
from session2memory.state.store import StateStore

DATE = "2026-11-10"
STAMP = f"{DATE}T02:00:00Z"
CWD = "/proj/alpha-etl"
GOAL = "目標：替 alpha-etl 加上 schema 漂移偵測。"
DECISION = "決定: schema 漂移偵測以欄位雜湊比對，容忍新增欄位"
PITFALL = "坑: 事件來源 B 的時間戳是本地時區，需先轉 UTC"
APPENDED = "完成: 漂移偵測已接到匯出 job 的 pre-check"
TURNS = [
    ("user", GOAL),
    ("assistant", f"{DECISION}\n驗證: 以 5 份歷史 schema 回放"),
    ("assistant", PITFALL),
]
MORE = [("user", "接上 pre-check 了嗎？"), ("assistant", APPENDED)]

needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd executable required")


# ---- Claude Code -----------------------------------------------------------------------

CLAUDE_ID = "c1a0de00-1111-4aaa-8aaa-0000000000a1"


def claude_lines(turns: list[tuple[str, str]], *, start: int = 0) -> list[str]:
    lines: list[str] = []
    for offset, (role, text) in enumerate(turns):
        index = start + offset
        content: object = text if role == "user" else [{"type": "text", "text": text}]
        lines.append(
            json.dumps(
                {
                    "type": role,
                    "uuid": f"uuid-{index:04d}",
                    "parentUuid": f"uuid-{index - 1:04d}" if index else None,
                    "sessionId": CLAUDE_ID,
                    "cwd": CWD,
                    "timestamp": STAMP,
                    "isSidechain": False,
                    "message": {"role": role, "content": content},
                },
                ensure_ascii=False,
            )
        )
        if role == "assistant":  # non-message records sit between messages in real files
            lines.append(
                json.dumps({"type": "custom-title", "sessionId": CLAUDE_ID, "customTitle": "t"})
            )
            # Request copies repeat a turn without a uuid; they must not become messages.
            lines.append(
                json.dumps(
                    {
                        "type": "api-request-blob",
                        "sessionId": CLAUDE_ID,
                        "message": {"role": "assistant", "content": [{"text": text}]},
                    },
                    ensure_ascii=False,
                )
            )
    return lines


def claude_build(root: Path) -> Path:
    path = root / "-proj-alpha-etl" / f"{CLAUDE_ID}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(claude_lines(TURNS)) + "\n", encoding="utf-8")
    return path


def claude_append(root: Path) -> None:
    path = root / "-proj-alpha-etl" / f"{CLAUDE_ID}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(claude_lines(MORE, start=len(TURNS))) + "\n")


# ---- OpenClaw native sqlite ------------------------------------------------------------

OPENCLAW_NATIVE_ID = "explicit-handoff-0001"
OPENCLAW_SCHEMA = """
CREATE TABLE session_windows (
  session_id TEXT PRIMARY KEY, session_key TEXT, status TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, transcript_updated_at INTEGER);
CREATE TABLE transcript_events (
  session_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT, created_at INTEGER NOT NULL,
  event_zstd BLOB, event_utf8_bytes INTEGER, message_role TEXT,
  PRIMARY KEY (session_id, seq),
  CHECK ((event_json IS NOT NULL AND event_zstd IS NULL)
      OR (event_json IS NULL AND event_zstd IS NOT NULL AND event_utf8_bytes IS NOT NULL)));
CREATE TABLE transcript_event_identities (
  session_id TEXT NOT NULL, event_id TEXT NOT NULL, seq INTEGER NOT NULL, event_type TEXT,
  parent_id TEXT, created_at INTEGER NOT NULL, PRIMARY KEY (session_id, event_id));
"""
DAY_MS = 1794276000000  # 2026-11-10T02:00:00Z


def openclaw_db(root: Path, agent: str = "kurisu") -> Path:
    return root / agent / "agent" / "openclaw-agent.sqlite"


def openclaw_write(
    root: Path,
    turns: list[tuple[str, str]],
    *,
    agent: str = "kurisu",
    session_id: str = OPENCLAW_NATIVE_ID,
    compress_every: int = 0,
) -> Path:
    path = openclaw_db(root, agent)
    fresh = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    if fresh:
        connection.executescript(OPENCLAW_SCHEMA)
    known = connection.execute(
        "SELECT 1 FROM session_windows WHERE session_id=?", (session_id,)
    ).fetchone()
    events: list[tuple[str, str | None, dict[str, object]]] = []
    if known is None:
        connection.execute(
            "INSERT INTO session_windows VALUES (?, ?, 'done', ?, ?, ?)",
            (session_id, f"agent:{agent}:explicit:{session_id}", DAY_MS, DAY_MS, DAY_MS),
        )
        events.append(
            ("ev-session", None, {"type": "session", "id": "ev-session", "cwd": CWD, "version": 3})
        )
    row = connection.execute(
        "SELECT coalesce(max(seq), -1) FROM transcript_events WHERE session_id=?", (session_id,)
    ).fetchone()
    seq = int(row[0])
    for role, text in turns:
        event_id = f"ev-{seq + 1 + len(events):05d}"
        content: object = text if role == "user" else [{"type": "text", "text": text}]
        events.append(
            (
                event_id,
                role,
                {
                    "type": "message",
                    "id": event_id,
                    "timestamp": STAMP,
                    "message": {"role": role, "content": content, "idempotencyKey": event_id},
                },
            )
        )
        if role == "assistant":
            tool_id = f"ev-{seq + 1 + len(events):05d}"
            events.append(
                (
                    tool_id,
                    "toolResult",
                    {
                        "type": "message",
                        "id": tool_id,
                        "message": {"role": "toolResult", "content": [{"text": "rows=1 ok"}]},
                    },
                )
            )
    for event_id, role, event in events:
        seq += 1
        raw = json.dumps(event, ensure_ascii=False)
        if compress_every and seq % compress_every == 0:
            blob = subprocess.run(
                ["zstd", "-c"], input=raw.encode("utf-8"), capture_output=True, check=True
            ).stdout
            connection.execute(
                "INSERT INTO transcript_events (session_id, seq, created_at, event_zstd, "
                "event_utf8_bytes, message_role) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, seq, DAY_MS, blob, len(raw.encode("utf-8")), role),
            )
        else:
            connection.execute(
                "INSERT INTO transcript_events (session_id, seq, event_json, created_at, "
                "message_role) VALUES (?, ?, ?, ?, ?)",
                (session_id, seq, raw, DAY_MS, role),
            )
        connection.execute(
            "INSERT INTO transcript_event_identities VALUES (?, ?, ?, ?, NULL, ?)",
            (session_id, event_id, seq, event["type"], DAY_MS),
        )
    connection.commit()
    connection.close()
    return path


def openclaw_build(root: Path) -> Path:
    return openclaw_write(root, TURNS)


def openclaw_append(root: Path) -> None:
    openclaw_write(root, MORE)


# ---- Grok ------------------------------------------------------------------------------

GROK_ID = "01a1a9f8-0213-74b0-b0a1-000000000001"


def grok_lines(turns: list[tuple[str, str]]) -> list[str]:
    lines = []
    for role, text in turns:
        if role == "user":
            lines.append({"type": "user", "content": [{"type": "text", "text": text}]})
            continue
        lines.append({"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "x"})
        lines.append({"type": "assistant", "content": text, "tool_calls": []})
        lines.append({"type": "tool_result", "tool_call_id": "call_1", "content": "rows=1 ok"})
    return [json.dumps(line, ensure_ascii=False) for line in lines]


def grok_dir(root: Path) -> Path:
    return root / quote(CWD, safe="") / GROK_ID


def grok_build(root: Path) -> Path:
    directory = grok_dir(root)
    directory.mkdir(parents=True)
    system = json.dumps({"type": "system", "content": "You are Grok."})
    (directory / "chat_history.jsonl").write_text(
        "\n".join([system, *grok_lines(TURNS)]) + "\n", encoding="utf-8"
    )
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": GROK_ID, "cwd": CWD},
                "created_at": STAMP,
                "last_active_at": STAMP,
                "session_summary": "synthetic",
            }
        ),
        encoding="utf-8",
    )
    (directory / "events.jsonl").write_text('{"type": "turn_started"}\n', encoding="utf-8")
    return directory / "chat_history.jsonl"


def grok_append(root: Path) -> None:
    with (grok_dir(root) / "chat_history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n".join(grok_lines(MORE)) + "\n")


# ---- Muse ------------------------------------------------------------------------------

MUSE_ID = "3a9a8293-6e92-4793-909f-000000000001"


def muse_record(sequence: int, payload_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"rec-{sequence:04d}",
        "sequence": sequence,
        "record_type": "event",
        "payload_type": payload_type,
        "payload_schema_version": 1,
        "schema_version": 1,
        "durability": "durable",
        "causation_id": None,
        "recorded_at": STAMP,
        "stream": {"kind": "session", "id": MUSE_ID},
        "payload": payload,
    }


def muse_lines(turns: list[tuple[str, str]], *, start: int = 0) -> list[str]:
    records: list[dict[str, object]] = []
    sequence = start
    for role, text in turns:
        sequence += 1
        if role == "user":
            event: dict[str, object] = {"kind": "started", "prompt": text}
            payload: dict[str, object] = {"kind": "run", "event": event}
            records.append(muse_record(sequence, "runtime.session", payload))
            continue
        event = {"kind": "assistant_message_committed", "message_id": "m", "text": text}
        records.append(muse_record(sequence, "runtime.session", {"kind": "run", "event": event}))
        sequence += 1
        usage: dict[str, object] = {"kind": "model_completed", "usage": {"input_tokens": 1}}
        records.append(muse_record(sequence, "runtime.session", {"kind": "run", "event": usage}))
    return [json.dumps(record, ensure_ascii=False) for record in records]


def muse_path(root: Path) -> Path:
    year, month, day = DATE.split("-")
    return root / year / month / day / MUSE_ID / "session.jsonl"


def muse_build(root: Path) -> Path:
    path = muse_path(root)
    path.parent.mkdir(parents=True)
    frame_child = muse_record(1, "runtime.session.permission_format_declared", {"kind": "x"})
    frame = {
        "retained_frame": "session_permission_transaction",
        "frame_schema_version": 1,
        "children": [{"child_index": 0, "record_json": json.dumps(frame_child)}],
    }
    metadata = muse_record(
        2, "runtime.session.metadata", {"kind": "metadata", "record": {"workspace_root": CWD}}
    )
    lines = [json.dumps(frame), json.dumps(metadata), *muse_lines(TURNS, start=2)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # A projection directory Muse keeps beside the dated tree must not be scanned.
    hidden = root / ".msp-view-g6" / "a" / "b" / MUSE_ID
    hidden.mkdir(parents=True)
    (hidden / "session.jsonl").write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    return path


def muse_append(root: Path) -> None:
    with muse_path(root).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(muse_lines(MORE, start=50)) + "\n")


# ---- shared cases ----------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    tool: str
    build: Callable[[Path], Path]
    append: Callable[[Path], None]
    adapter: Callable[[Path], PipelineAdapter]
    session_id: str
    agent: str
    home_id: str
    decision_locator: str


CASES = [
    Case("claude", claude_build, claude_append, ClaudeAdapter, CLAUDE_ID, "claude",
         "claude@main", "uuid:uuid-0001"),
    Case("openclaw-native", openclaw_build, openclaw_append, OpenClawNativeAdapter,
         f"kurisu/{OPENCLAW_NATIVE_ID}", "openclaw/kurisu", "openclaw@kurisu", "event:ev-00002"),
    Case("grok", grok_build, grok_append, GrokAdapter, GROK_ID, "grok", "grok@main", "msg:3"),
    Case("muse", muse_build, muse_append, MuseAdapter, MUSE_ID, "muse", "muse@main",
         "rec:rec-0004"),
]
case_param = pytest.mark.parametrize("case", CASES, ids=[case.tool for case in CASES])


class Bench:
    def __init__(self, tmp_path: Path, case: Case) -> None:
        self.case = case
        self.root = tmp_path / "source"
        self.root.mkdir()
        self.output = tmp_path / "out"
        self.store = StateStore.open(tmp_path / "state.db")
        self.adapter = case.adapter(self.root)
        self.key = f"{case.tool}:{case.session_id}"

    def records(self) -> list[SessionRecord]:
        return list(self.adapter.iter_sessions(DATE))

    def run(self) -> tuple[int, int]:
        return run_pipeline(
            adapters={self.case.tool: self.adapter},
            output_dir=self.output,
            date=DATE,
            source_roots={self.case.tool: self.root},
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

    def count(self, table: str) -> int:
        row = self.store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(row[0])


@case_param
def test_adapter_reads_messages_with_native_identity(tmp_path: Path, case: Case) -> None:
    bench = Bench(tmp_path, case)
    path = case.build(bench.root)

    records = bench.records()

    assert len(records) == 1
    record = records[0]
    assert (record.tool, record.session_id) == (case.tool, case.session_id)
    assert record.source_path == path
    assert (record.agent, record.home_id, record.cwd) == (case.agent, case.home_id, Path(CWD))
    conversation = [(m.role, m.text) for m in record.messages if m.role != "system"]
    assert conversation == TURNS
    assert all(message.locator for message in record.messages)
    decision = next(m for m in record.messages if m.text.startswith(DECISION))
    assert decision.locator == case.decision_locator
    assert record.content_digest and record.record_count > len(record.messages)
    assert getattr(bench.adapter, "skipped", []) == []


@case_param
def test_import_registers_one_source_and_rerun_adds_nothing(tmp_path: Path, case: Case) -> None:
    bench = Bench(tmp_path, case)
    case.build(bench.root)

    first, _ = bench.run()
    counts = {t: bench.count(t) for t in ("candidates", "source_messages", "source_versions")}
    second, _ = bench.run()

    sources = bench.store.sources
    assert (first, second) == (1, 0)
    assert sources.source_keys() == [bench.key]
    assert sources.source_keys(agent=case.agent) == [bench.key]
    assert sources.versions(bench.key) == [(1, "first-seen")]
    assert [loc.home_id for loc in sources.locations(bench.key)] == [case.home_id]
    assert {t: bench.count(t) for t in counts} == counts
    bench.candidate_id("schema 漂移偵測以欄位雜湊比對")
    bench.candidate_id("事件來源 B 的時間戳")


@case_param
def test_append_is_a_new_version_with_only_the_new_messages(tmp_path: Path, case: Case) -> None:
    bench = Bench(tmp_path, case)
    case.build(bench.root)
    bench.run()
    messages_before = bench.count("source_messages")

    case.append(bench.root)
    sessions, _ = bench.run()

    assert sessions == 1
    assert bench.store.sources.versions(bench.key) == [(1, "first-seen"), (2, "append")]
    assert bench.count("source_messages") == messages_before + len(MORE)
    bench.candidate_id("漂移偵測已接到匯出 job")
    bench.candidate_id("schema 漂移偵測以欄位雜湊比對")  # still exactly one


@case_param
def test_evidence_resolves_by_session_id_and_locator(tmp_path: Path, case: Case) -> None:
    bench = Bench(tmp_path, case)
    path = case.build(bench.root)
    bench.run()

    resolved = bench.store.sources.resolve_candidate(
        bench.candidate_id("schema 漂移偵測以欄位雜湊比對"), readers=default_readers()
    )

    assert resolved.ok, resolved.error
    assert (resolved.source_key, resolved.locator, resolved.path) == (
        bench.key,
        case.decision_locator,
        path,
    )
    assert resolved.text is not None and DECISION in resolved.text


# ---- source-specific behaviour ---------------------------------------------------------


def test_claude_subagent_transcripts_stay_excluded(tmp_path: Path) -> None:
    path = claude_build(tmp_path)
    sidechain = path.parent / CLAUDE_ID / "subagents" / "agent-a1.jsonl"
    sidechain.parent.mkdir(parents=True)
    sidechain.write_text("\n".join(claude_lines(TURNS)) + "\n", encoding="utf-8")

    assert [r.source_path for r in ClaudeAdapter(tmp_path).iter_sessions(DATE)] == [path]


@needs_zstd
def test_openclaw_native_decodes_event_zstd_rows_like_plain_rows(tmp_path: Path) -> None:
    plain_root, packed_root = tmp_path / "plain", tmp_path / "packed"
    openclaw_write(plain_root, TURNS + MORE)
    openclaw_write(packed_root, TURNS + MORE, compress_every=2)
    packed_rows = sqlite3.connect(openclaw_db(packed_root)).execute(
        "SELECT count(*) FROM transcript_events WHERE event_zstd IS NOT NULL"
    ).fetchone()[0]

    plain = next(iter(OpenClawNativeAdapter(plain_root).iter_sessions(DATE)))
    packed = next(iter(OpenClawNativeAdapter(packed_root).iter_sessions(DATE)))

    assert packed_rows >= 3
    assert [(m.locator, m.text) for m in packed.messages] == [
        (m.locator, m.text) for m in plain.messages
    ]
    assert packed.content_digest == plain.content_digest


def test_openclaw_native_keeps_same_named_sessions_of_two_agents_apart(tmp_path: Path) -> None:
    openclaw_write(tmp_path, TURNS, agent="kurisu", session_id="main")
    openclaw_write(tmp_path, [("user", "另一個角色的工作")], agent="rem", session_id="main")

    records = list(OpenClawNativeAdapter(tmp_path).iter_sessions(DATE))

    assert sorted(record.session_id for record in records) == ["kurisu/main", "rem/main"]
    assert {record.agent for record in records} == {"openclaw/kurisu", "openclaw/rem"}
    assert len({record.source_key for record in records}) == 2


def test_openclaw_native_never_writes_to_the_store(tmp_path: Path) -> None:
    path = openclaw_build(tmp_path)
    before = path.read_bytes()

    adapter = OpenClawNativeAdapter(tmp_path)
    list(adapter.iter_sessions(DATE))
    adapter.read_session(path, f"kurisu/{OPENCLAW_NATIVE_ID}")

    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_openclaw_native_evidence_fails_once_the_rows_are_deleted(tmp_path: Path) -> None:
    bench = Bench(tmp_path, CASES[1])
    path = openclaw_build(bench.root)
    bench.run()
    candidate_id = bench.candidate_id("schema 漂移偵測以欄位雜湊比對")
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM transcript_events")
    connection.execute("DELETE FROM session_windows")
    connection.commit()
    connection.close()

    resolved = bench.store.sources.resolve_candidate(candidate_id, readers=default_readers())

    assert not resolved.ok
    assert resolved.error is not None and "no present location" in resolved.error


def test_grok_falls_back_to_the_directory_name_for_cwd(tmp_path: Path) -> None:
    path = grok_build(tmp_path)
    (path.parent / "summary.json").unlink()

    record = GrokAdapter(tmp_path).read_file(path)

    assert record.cwd == Path(CWD)
    assert record.session_id == GROK_ID


def test_grok_rewound_history_is_recorded_as_rewrite(tmp_path: Path) -> None:
    bench = Bench(tmp_path, CASES[2])
    path = grok_build(bench.root)
    bench.run()
    rewound = [("user", GOAL), ("assistant", "決定: 改用欄位名稱比對")]
    path.write_text("\n".join(grok_lines(rewound)) + "\n", encoding="utf-8")

    bench.run()

    assert bench.store.sources.versions(bench.key)[-1] == (2, "rewrite")


def test_muse_ignores_projection_directories_and_unwraps_frames(tmp_path: Path) -> None:
    path = muse_build(tmp_path)

    adapter = MuseAdapter(tmp_path)
    records = list(adapter.iter_sessions(DATE))

    assert [record.source_path for record in records] == [path]
    assert records[0].started_at is not None
    assert adapter.skipped == []


# ---- CLI -------------------------------------------------------------------------------


def test_new_tools_are_opt_in_and_importable_by_name(tmp_path: Path) -> None:
    grok_root, muse_root = tmp_path / "grok", tmp_path / "muse"
    grok_root.mkdir()
    muse_root.mkdir()
    grok_build(grok_root)
    muse_build(muse_root)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["import", "--date", DATE, "--no-agentic-os", "--tool", "grok", "--tool", "muse",
         "--source-root", f"grok={grok_root.as_posix()}",
         "--source-root", f"muse={muse_root.as_posix()}",
         "--output", (tmp_path / "out").as_posix()],
    )

    assert result.exit_code == 0, result.output
    assert "tools=2 sessions=2" in result.output


def test_default_import_does_not_include_opt_in_tools() -> None:
    from session2memory.cli import OPT_IN_TOOLS, P0_TOOLS, _selected_tools

    assert _selected_tools(None, {}) == list(P0_TOOLS)
    assert not set(OPT_IN_TOOLS) & set(P0_TOOLS)
    assert _selected_tools(["openclaw-native"], {}) == ["openclaw-native"]
