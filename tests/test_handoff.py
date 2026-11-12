"""Manual handoff packet: native summaries, conversation structure, evidence refs, size cap.

Fixtures mirror the on-disk shape and the user-message wrappers observed on real installs
(Grok ``<user_query>``, harness reminders, Codex environment blocks); content is synthetic.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest
from test_registry_adapters import (
    CLAUDE_ID,
    GROK_ID,
    MUSE_ID,
    OPENCLAW_NATIVE_ID,
    claude_lines,
    grok_lines,
    muse_lines,
    muse_record,
    openclaw_write,
)
from test_source_registry import rollout_lines
from typer.testing import CliRunner

from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.codex import CodexAdapter
from session2memory.adapters.grok import GrokAdapter
from session2memory.adapters.muse import MuseAdapter
from session2memory.cli import app
from session2memory.handoff import (
    MIN_MAX_CHARS,
    NativeSummary,
    build_packet,
    claude_summary,
    codex_summary,
    grok_summary,
    is_substantive,
    muse_summary,
    native_summary,
    parse_reference,
    user_request,
)
from session2memory.handoff_find import (
    SessionSelectionError,
    latest_session,
    recent_sessions,
    select_session,
)
from session2memory.models import SessionRecord

DATE = "2026-11-10"
CODEX_ID = "01a1c0de-0009-7000-8000-0000000000c9"
GOAL = "把 alpha-etl 的每日匯出改成可增量重跑"
SECOND = "先確認 staging bucket 的 lifecycle 設定"
LAST_REQUEST = "把 retry 上限改成 3 次並補測試"
LAST_REPLY = "完成: retry 上限已改為 3 次\n下一步要補關帳月份的拒絕測試，失敗代碼用 DRIFT-409"
TURNS = [
    ("user", GOAL),
    ("assistant", "決定: 匯出格式採用 Parquet，分區鍵為 event_date"),
    ("user", SECOND),
    ("assistant", "坑: lifecycle 會在 7 天後刪除中間檔"),
    ("user", LAST_REQUEST),
    ("assistant", LAST_REPLY),
]
REMINDER = "<system-reminder>\ninternal harness note\n</system-reminder>"

needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd executable required")


def workspace_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work" / "alpha-etl"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


# ---- builders (one session of the workspace per tool) ----------------------------------


def codex_session(root: Path, cwd: Path, turns: list[tuple[str, str]] = TURNS) -> Path:
    wrapped = [("user", f"<environment_context>\n<cwd>{cwd}</cwd>\n</environment_context>")]
    path = root / "2026" / "11" / "10" / f"rollout-{DATE}T01-00-00-{CODEX_ID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = rollout_lines(CODEX_ID, cwd.as_posix(), [*wrapped, *turns])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def codex_state(root: Path, cwd: Path, rollout: Path, *, name: str | None, title: str) -> Path:
    database = root.parent / "state_5.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT, cwd TEXT, "
        "updated_at INTEGER, title TEXT, first_user_message TEXT, preview TEXT, name TEXT)"
    )
    connection.execute(
        "INSERT OR REPLACE INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (CODEX_ID, rollout.as_posix(), cwd.as_posix(), 1794276000, title, GOAL, GOAL, name),
    )
    connection.commit()
    connection.close()
    return database


def claude_session(root: Path, cwd: Path, *, compact: str | None = None) -> Path:
    encoded = "".join(ch if ch.isalnum() else "-" for ch in cwd.as_posix())
    path = root / encoded / f"{CLAUDE_ID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.loads(line) for line in claude_lines(TURNS)]
    for line in lines:
        if "cwd" in line:
            line["cwd"] = cwd.as_posix()
    extra: list[dict[str, object]] = [
        {"type": "ai-title", "sessionId": CLAUDE_ID, "aiTitle": "AI 產生的標題"},
        {"type": "custom-title", "sessionId": CLAUDE_ID, "customTitle": "alpha 匯出增量化"},
        {"type": "last-prompt", "sessionId": CLAUDE_ID, "lastPrompt": LAST_REQUEST},
        {
            "type": "user",
            "uuid": "uuid-notify",
            "sessionId": CLAUDE_ID,
            "cwd": cwd.as_posix(),
            "message": {"role": "user", "content": "<task-notification>done</task-notification>"},
        },
    ]
    if compact:
        extra.append(
            {
                "type": "user",
                "uuid": "uuid-compact",
                "sessionId": CLAUDE_ID,
                "cwd": cwd.as_posix(),
                "isCompactSummary": True,
                "message": {"role": "user", "content": compact},
            }
        )
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in [*lines, *extra]) + "\n",
        encoding="utf-8",
    )
    return path


def grok_session(root: Path, cwd: Path, *, summary: dict[str, object] | None = None) -> Path:
    directory = root / quote(cwd.as_posix(), safe="") / GROK_ID
    directory.mkdir(parents=True, exist_ok=True)
    wrapped = [
        (role, f"{REMINDER}\n<user_query>\n{text}\n</user_query>" if role == "user" else text)
        for role, text in TURNS
    ]
    system = json.dumps({"type": "system", "content": "You are Grok."})
    reminder_only = json.dumps({"type": "user", "content": [{"type": "text", "text": REMINDER}]})
    lines = [system, reminder_only, *grok_lines(wrapped)]
    (directory / "chat_history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload: dict[str, object] = {
        "info": {"id": GROK_ID, "cwd": cwd.as_posix()},
        "created_at": f"{DATE}T02:00:00Z",
        "last_active_at": f"{DATE}T03:00:00Z",
        "generated_title": "alpha 匯出增量化",
        "session_summary": "alpha 匯出增量化",
        "last_turn_summary": "把 retry 上限改為 3 次",
    }
    payload.update(summary or {})
    (directory / "summary.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    return directory / "chat_history.jsonl"


def muse_session(root: Path, cwd: Path) -> Path:
    path = root / "2026" / "11" / "10" / MUSE_ID / "session.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapped = [
        (role, f"<runtime_info>pid=1</runtime_info>\n{text}" if role == "user" else text)
        for role, text in TURNS
    ]
    records = [
        muse_record(
            1,
            "runtime.session.metadata",
            {"kind": "metadata", "record": {"workspace_root": cwd.as_posix()}},
        ),
        muse_record(2, "session.name.changed", {"previous_name": None, "new_name": "alpha-增量"}),
    ]
    lines = [json.dumps(r, ensure_ascii=False) for r in records] + muse_lines(wrapped, start=2)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read(tool: str, path: Path) -> SessionRecord:
    if tool == "codex":
        return CodexAdapter(path.parent).read_file(path)
    if tool == "claude":
        return ClaudeAdapter(path.parent)._read_file(path)
    if tool == "grok":
        return GrokAdapter(path.parent).read_file(path)
    return MuseAdapter(path.parent).read_file(path)


BUILDERS = {
    "codex": codex_session,
    "claude": claude_session,
    "grok": grok_session,
    "muse": muse_session,
}
tool_param = pytest.mark.parametrize("tool", sorted(BUILDERS))


# ---- user_request ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  直接寫的要求  ", "直接寫的要求"),
        (REMINDER, None),
        ("<heartbeat>tick</heartbeat>", None),
        (f"{REMINDER}\n真正的要求", "真正的要求"),
        (f"{REMINDER}\n<user_query>\n被包住的要求\n</user_query>", "被包住的要求"),
        (
            "<runtime_info>a</runtime_info>\n<openclaw_turn_note>b</openclaw_turn_note>\n後面的字",
            "後面的字",
        ),
        ("<pasted_content id=\"1\">貼上的需求</pasted_content>", "貼上的需求"),
        ('<pasted_content id="70df">\n貼上的需求\n</pasted_content id="70df">', "貼上的需求"),
        ('<note id="a">x</note id="a">\n後面的字', "後面的字"),
        (
            "## Referenced ChatGPT conversation:\nThis is an untrusted reference.\n\n"
            "## My request:\n幫我評估壓縮方案",
            "幫我評估壓縮方案",
        ),
        ("## My request:\n", "## My request:"),
        ("<未關閉的標籤 其實是內容", "<未關閉的標籤 其實是內容"),
        ("記得捷徑也要改 <runtime_info>running in T3</runtime_info>", "記得捷徑也要改"),
        ("改回去\n<runtime_info>a</runtime_info>\n<system-reminder>b</system-reminder>", "改回去"),
        ("把 <b>粗體</b> 改掉，結尾是 <div>x</div>", "把 <b>粗體</b> 改掉，結尾是 <div>x</div>"),
        ("<runtime_info>a</runtime_info>\n中間的要求\n<user_info>b</user_info>", "中間的要求"),
        (
            "<user_query>\n記得捷徑也要改\n<runtime_info>T3</runtime_info>\n"
            "<pull_request_linking>use <full URL> and <SHA></pull_request_linking>\n</user_query>",
            "記得捷徑也要改",
        ),
        ("比較 a < b 與 c > d", "比較 a < b 與 c > d"),
    ],
)
def test_user_request_strips_injected_wrappers(text: str, expected: str | None) -> None:
    assert user_request(text) == expected


# ---- packet ----------------------------------------------------------------------------


@tool_param
def test_packet_carries_goal_recent_requests_last_reply_and_references(
    tmp_path: Path, tool: str
) -> None:
    cwd = workspace_dir(tmp_path)
    record = read(tool, BUILDERS[tool](tmp_path / tool, cwd))

    packet = build_packet(record, native_summary(record))

    lines = packet.splitlines()
    assert lines[0] == "# Handoff"
    assert f"workspace: {cwd.as_posix()}" in lines
    assert any(line.startswith(f"from: {tool} session {record.session_id}") for line in lines)
    goal = next(line for line in lines if line.startswith("目標: "))
    assert GOAL in goal
    requests = [line for line in lines if line.startswith("最近的要求: ")]
    assert [SECOND in requests[0], LAST_REQUEST in requests[-1]] == [True, True]
    reply = next(line for line in lines if line.startswith("最後的回覆: "))
    assert "DRIFT-409" in reply
    assert any(line.startswith("決定: 匯出格式採用 Parquet") for line in lines)
    # Injected context never shows up as a request, and every quoted item is resolvable.
    assert "internal harness note" not in packet and "environment_context" not in packet
    assert "task-notification" not in packet and "runtime_info" not in packet
    for line in [goal, *requests, reply]:
        tool_name, session_id, locator = parse_reference(line[line.rindex("[") :])
        assert (tool_name, session_id) == (record.tool, record.session_id)
        message = next(m for m in record.messages if m.locator == locator)
        assert message.text


def test_packet_never_exceeds_the_character_cap_and_keeps_the_essentials(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    filler = "這是一段很長的過程說明。" * 400
    turns = [("user", GOAL + filler)]
    for index in range(30):
        turns.append(("user", f"第 {index} 個要求 {filler}"))
        turns.append(("assistant", f"決定: 第 {index} 項 {filler}"))
    turns += [("user", LAST_REQUEST), ("assistant", LAST_REPLY + filler)]
    record = read("codex", codex_session(tmp_path / "codex", cwd, turns))
    native = NativeSummary(title="標題" * 200, summary="摘要" * 5000, origin="synthetic")

    for cap in (MIN_MAX_CHARS, 1200, 4000):
        packet = build_packet(record, native, max_chars=cap)
        assert len(packet) <= cap
        # The essentials are shortened to fit; optional lines go first.
        assert "目標: " in packet and "最後的回覆: " in packet and "最近的要求: " in packet
    assert LAST_REQUEST in build_packet(record, native, max_chars=1200)
    assert "原生摘要" not in build_packet(record, native, max_chars=MIN_MAX_CHARS)
    assert len(build_packet(record, native, max_chars=4000)) > 1200  # uses the room it has


def test_packet_rejects_a_cap_too_small_to_be_useful(tmp_path: Path) -> None:
    record = read("codex", codex_session(tmp_path / "codex", workspace_dir(tmp_path)))

    with pytest.raises(ValueError, match="max_chars"):
        build_packet(record, max_chars=MIN_MAX_CHARS - 1)


def test_packet_for_a_session_without_conversation_says_so(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    record = read("codex", codex_session(tmp_path / "codex", cwd, [("user", REMINDER)]))

    packet = build_packet(record)

    assert "沒有可用的對話內容" in packet
    assert "目標: " not in packet


def test_carried_over_summary_is_a_summary_not_a_request(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    carried = (
        "This session is being continued from a previous conversation that ran out of "
        "context. 先前已完成匯出冪等化。"
    )
    turns = [("user", carried), *TURNS]
    record = read("codex", codex_session(tmp_path / "codex", cwd, turns))

    packet = build_packet(record)

    lines = packet.splitlines()
    summary = next(line for line in lines if line.startswith("原生摘要（接續摘要）: "))
    assert "This session is being continued" in summary
    assert next(line for line in lines if line.startswith("目標: ")).startswith(f"目標: {GOAL}")
    assert not any(line.startswith("最近的要求: This session") for line in lines)
    # A native summary from the agent's own store wins over the carried-over one.
    native = NativeSummary(summary="代理自己的摘要", origin="synthetic")
    assert "接續摘要" not in build_packet(record, native)


@pytest.mark.parametrize(
    ("text", "substantive"),
    [
        ("Commit", False),
        ("修正", False),
        ("先不測", False),
        ("ok do it", False),
        ("  繼續  ", False),
        ("全部都升級", True),
        ("那ai memory的部分？", True),
        ("fix the retry test", True),
    ],
)
def test_bare_acknowledgements_are_not_substantive(text: str, substantive: bool) -> None:
    assert is_substantive(text) is substantive


def test_recent_requests_skip_bare_acknowledgements(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    turns = [
        ("user", "ok"),
        ("assistant", "請說明要做什麼"),
        ("user", GOAL),
        ("assistant", "決定: 匯出格式採用 Parquet"),
        ("user", SECOND),
        ("assistant", "已確認"),
        ("user", "Commit"),
        ("assistant", "已 commit"),
        ("user", LAST_REQUEST),
        ("assistant", "已修改"),
        ("user", "修正"),
        ("assistant", LAST_REPLY),
    ]
    record = read("codex", codex_session(tmp_path / "codex", cwd, turns))

    lines = build_packet(record).splitlines()

    assert next(line for line in lines if line.startswith("目標: ")).startswith(f"目標: {GOAL}")
    requests = [line for line in lines if line.startswith("最近的要求: ")]
    assert [SECOND in requests[0], LAST_REQUEST in requests[1], len(requests)] == [True, True, 2]
    assert not any("Commit" in line or "修正 [" in line for line in requests)
    assert any(line.startswith("最後的回覆: ") and "DRIFT-409" in line for line in lines)


def test_a_session_of_only_short_requests_still_shows_its_first_one(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    turns = [("user", "繼續"), ("assistant", "已繼續處理"), ("user", "ok"), ("assistant", "完成")]
    record = read("codex", codex_session(tmp_path / "codex", cwd, turns))

    lines = build_packet(record).splitlines()

    assert any(line.startswith("目標: 繼續 [") for line in lines)
    assert not any(line.startswith("最近的要求: ") for line in lines)


# ---- native summaries ------------------------------------------------------------------


def test_grok_summary_drops_the_summary_that_only_repeats_the_title(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    record = read("grok", grok_session(tmp_path / "grok", cwd))

    assert grok_summary(record) == NativeSummary(
        title="alpha 匯出增量化",
        summary=None,
        last_turn="把 retry 上限改為 3 次",
        origin="grok summary.json",
    )
    distinct = read(
        "grok", grok_session(tmp_path / "grok2", cwd, summary={"session_summary": "真正的摘要"})
    )
    assert grok_summary(distinct).summary == "真正的摘要"


def test_codex_summary_uses_the_thread_name_not_the_first_message_as_title(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    root = tmp_path / "codex" / "sessions"
    rollout = codex_session(root, cwd)
    codex_state(root, cwd, rollout, name="alpha 增量", title=GOAL[:10])

    assert codex_summary(read("codex", rollout)) == NativeSummary(
        title="alpha 增量", origin="codex threads"
    )

    codex_state(root, cwd, rollout, name=None, title=GOAL[:10])
    assert codex_summary(read("codex", rollout)).title is None  # title is just the first message
    codex_state(root, cwd, rollout, name=None, title="人工改過的標題")
    assert codex_summary(read("codex", rollout)).title == "人工改過的標題"


def test_codex_summary_is_empty_without_a_thread_table(tmp_path: Path) -> None:
    record = read("codex", codex_session(tmp_path / "codex" / "sessions", workspace_dir(tmp_path)))

    assert codex_summary(record).is_empty


def test_claude_summary_prefers_custom_title_and_reads_the_compact_summary(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    plain = read("claude", claude_session(tmp_path / "a", cwd))
    compacted = read(
        "claude", claude_session(tmp_path / "b", cwd, compact="先前進度：已完成匯出冪等化")
    )

    assert claude_summary(plain).title == "alpha 匯出增量化"
    assert claude_summary(plain).summary is None
    assert claude_summary(compacted).summary == "先前進度：已完成匯出冪等化"
    assert "原生摘要（claude compact summary）: 先前進度" in build_packet(
        compacted, native_summary(compacted)
    )


def test_muse_summary_is_the_session_name(tmp_path: Path) -> None:
    record = read("muse", muse_session(tmp_path / "muse", workspace_dir(tmp_path)))

    assert muse_summary(record) == NativeSummary(title="alpha-增量", origin="muse session name")


# ---- finding the session ---------------------------------------------------------------


def all_roots(tmp_path: Path) -> dict[str, Path]:
    return {
        "codex": tmp_path / "codex" / "sessions",
        "claude": tmp_path / "claude",
        "grok": tmp_path / "grok",
        "muse": tmp_path / "muse",
    }


def touch(path: Path, seconds: int) -> None:
    os.utime(path, (1794276000 + seconds, 1794276000 + seconds))


@tool_param
def test_each_tool_finds_only_sessions_of_the_workspace(tmp_path: Path, tool: str) -> None:
    cwd = workspace_dir(tmp_path)
    other = tmp_path / "work" / "beta-billing"
    other.mkdir(parents=True)
    root = all_roots(tmp_path)[tool]
    mine = BUILDERS[tool](root, cwd)
    found = recent_sessions(tool, root, cwd)

    assert [f.record.source_path for f in found] == [mine]
    assert recent_sessions(tool, root, other.resolve()) == []
    assert recent_sessions(tool, tmp_path / "missing", cwd) == []


def test_latest_session_picks_the_most_recent_across_tools(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    roots = all_roots(tmp_path)
    touch(codex_session(roots["codex"], cwd), 100)
    touch(claude_session(roots["claude"], cwd), 300)
    touch(grok_session(roots["grok"], cwd), 200)

    found = latest_session(roots, cwd)

    assert found is not None and found.record.tool == "claude"
    without_claude = latest_session({"codex": roots["codex"], "grok": roots["grok"]}, cwd)
    assert without_claude is not None and without_claude.record.tool == "grok"
    assert latest_session(roots, tmp_path / "work" / "unknown") is None


def test_latest_session_skips_a_newer_session_that_has_no_conversation(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    roots = all_roots(tmp_path)
    touch(claude_session(roots["claude"], cwd), 100)
    empty = roots["claude"] / claude_session(roots["claude"], cwd).parent.name / "empty-0001.jsonl"
    empty.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": "u1",
                "sessionId": "empty-0001",
                "cwd": cwd.as_posix(),
                "message": {"role": "user", "content": REMINDER},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    touch(empty, 900)

    found = latest_session({"claude": roots["claude"]}, cwd)

    assert found is not None and found.record.session_id == CLAUDE_ID


@needs_zstd
def test_codex_finder_follows_the_thread_table_to_a_compressed_rollout(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    root = all_roots(tmp_path)["codex"]
    rollout = codex_session(root, cwd)
    codex_state(root, cwd, rollout, name="alpha 增量", title=GOAL)
    subprocess.run(["zstd", "-q", "--rm", "-f", str(rollout)], check=True)  # native compression

    found = recent_sessions("codex", root, cwd)

    assert [f.record.source_path.name for f in found] == [rollout.name + ".zst"]
    assert GOAL in build_packet(found[0].record, native_summary(found[0].record))


def test_openclaw_native_is_found_only_when_asked_for(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    agents = tmp_path / "agents"
    database = openclaw_write(agents, TURNS)
    connection = sqlite3.connect(database)
    session_event = json.dumps({"type": "session", "id": "ev-session", "cwd": cwd.as_posix()})
    connection.execute("UPDATE transcript_events SET event_json=? WHERE seq=0", (session_event,))
    connection.commit()
    connection.close()

    found = recent_sessions("openclaw-native", agents, cwd)

    assert [f.record.session_id for f in found] == [f"kurisu/{OPENCLAW_NATIVE_ID}"]
    assert latest_session(all_roots(tmp_path), cwd) is None  # not part of the default search
    assert "目標: " + GOAL in build_packet(found[0].record)


# ---- CLI -------------------------------------------------------------------------------


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def cli_roots(tmp_path: Path) -> list[str]:
    arguments: list[str] = []
    for tool, root in all_roots(tmp_path).items():
        arguments += ["--source-root", f"{tool}={root.as_posix()}"]
    return arguments


def test_cli_handoff_prints_a_packet_and_show_resolves_its_references(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    roots = all_roots(tmp_path)
    touch(codex_session(roots["codex"], cwd), 100)
    touch(grok_session(roots["grok"], cwd), 500)
    before = snapshot(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["handoff", "--workspace", cwd.as_posix(), *cli_roots(tmp_path)])

    assert result.exit_code == 0, result.output
    assert f"from: grok session {GROK_ID}" in result.output
    assert "標題: alpha 匯出增量化" in result.output and "上一輪摘要: " in result.output
    reply = next(line for line in result.output.splitlines() if line.startswith("最後的回覆: "))
    shown = runner.invoke(
        app,
        ["handoff-show", reply[reply.rindex("[") :], "--workspace", cwd.as_posix(),
         *cli_roots(tmp_path)],
    )
    assert shown.exit_code == 0, shown.output
    assert shown.output.strip() == LAST_REPLY
    assert snapshot(tmp_path) == before, "handoff must not write to any source"


def test_cli_handoff_from_tool_cap_and_out_file(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    roots = all_roots(tmp_path)
    touch(codex_session(roots["codex"], cwd), 100)
    touch(grok_session(roots["grok"], cwd), 500)
    out = tmp_path / "packets" / "handoff.md"
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["handoff", "--workspace", cwd.as_posix(), "--from-tool", "codex", "--max-chars", "700",
         "--out", out.as_posix(), *cli_roots(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    packet = out.read_text(encoding="utf-8")
    assert f"from: codex session {CODEX_ID}" in packet
    assert len(packet.rstrip("\n")) <= 700


def test_cli_handoff_fails_clearly_when_nothing_matches(tmp_path: Path) -> None:
    runner = CliRunner()
    cwd = workspace_dir(tmp_path)

    missing = runner.invoke(app, ["handoff", "--workspace", cwd.as_posix(), *cli_roots(tmp_path)])
    too_small = runner.invoke(app, ["handoff", "--workspace", cwd.as_posix(), "--max-chars", "10"])
    bad_tool = runner.invoke(app, ["handoff", "--workspace", cwd.as_posix(), "--from-tool", "qwen"])
    bad_ref = runner.invoke(app, ["handoff-show", "not-a-reference", "--workspace", cwd.as_posix()])
    gone = runner.invoke(
        app,
        ["handoff-show", f"codex:{CODEX_ID}#ord:2", "--workspace", cwd.as_posix(),
         *cli_roots(tmp_path)],
    )

    assert missing.exit_code == 1 and "no session with conversation found" in missing.output
    assert too_small.exit_code != 0 and bad_tool.exit_code != 0 and bad_ref.exit_code != 0
    assert gone.exit_code == 1 and "session not found" in gone.output


# ---- --session -------------------------------------------------------------------------

OLDER_CLAUDE_ID = "c1a0de00-2222-4bbb-8bbb-0000000000b2"
OLDER_GOAL = "替 alpha-etl 的匯出加上 schema 漂移偵測"


def older_claude_session(root: Path, cwd: Path) -> Path:
    """A second, older Claude session of the same workspace with its own goal."""
    newest = claude_session(root, cwd)
    path = newest.with_name(f"{OLDER_CLAUDE_ID}.jsonl")
    lines = []
    for raw in claude_lines([("user", OLDER_GOAL), ("assistant", "完成: 漂移偵測已上線")]):
        line = json.loads(raw)
        line["sessionId"] = OLDER_CLAUDE_ID
        if "cwd" in line:
            line["cwd"] = cwd.as_posix()
        lines.append(json.dumps(line, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    touch(path, 50)
    touch(newest, 400)
    return path


def test_select_session_by_id_prefix_or_tool_qualified_id(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    roots = all_roots(tmp_path)
    older = older_claude_session(roots["claude"], cwd)
    touch(codex_session(roots["codex"], cwd), 900)

    assert latest_session(roots, cwd).record.tool == "codex"  # type: ignore[union-attr]
    for spec in (OLDER_CLAUDE_ID, f"claude:{OLDER_CLAUDE_ID}", OLDER_CLAUDE_ID[:13]):
        assert select_session(roots, cwd, spec).record.source_path == older
    assert select_session(roots, cwd, f"codex:{CODEX_ID[:8]}").record.session_id == CODEX_ID


def test_select_session_reports_ambiguous_and_unknown_ids(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    roots = all_roots(tmp_path)
    older_claude_session(roots["claude"], cwd)

    with pytest.raises(SessionSelectionError, match="ambiguous") as ambiguous:
        select_session(roots, cwd, "c1a0de00")
    assert CLAUDE_ID in str(ambiguous.value) and OLDER_CLAUDE_ID in str(ambiguous.value)
    with pytest.raises(SessionSelectionError, match="no session 'nope'") as unknown:
        select_session(roots, cwd, "nope")
    assert f"claude:{CLAUDE_ID}" in str(unknown.value)  # tells which ids exist
    # A tool-qualified id is only looked up in that tool.
    with pytest.raises(SessionSelectionError, match="no session"):
        select_session(roots, cwd, f"codex:{OLDER_CLAUDE_ID}")
    # Another workspace never sees this one's sessions.
    with pytest.raises(SessionSelectionError, match=r"most recent sessions: \(none\)"):
        select_session(roots, tmp_path / "work" / "other", OLDER_CLAUDE_ID)


def test_cli_handoff_session_option_overrides_latest(tmp_path: Path) -> None:
    cwd = workspace_dir(tmp_path)
    roots = all_roots(tmp_path)
    older_claude_session(roots["claude"], cwd)
    touch(codex_session(roots["codex"], cwd), 900)
    runner = CliRunner()
    base = ["handoff", "--workspace", cwd.as_posix(), *cli_roots(tmp_path)]

    latest = runner.invoke(app, base)
    chosen = runner.invoke(app, [*base, "--session", OLDER_CLAUDE_ID[:13]])
    qualified = runner.invoke(app, [*base, "--session", f"claude:{CLAUDE_ID}"])
    wrong_tool = runner.invoke(
        app, [*base, "--from-tool", "codex", "--session", OLDER_CLAUDE_ID]
    )
    ambiguous = runner.invoke(app, [*base, "--session", "c1a0de00"])

    assert f"from: codex session {CODEX_ID}" in latest.output
    assert chosen.exit_code == 0, chosen.output
    assert f"from: claude session {OLDER_CLAUDE_ID}" in chosen.output
    assert f"目標: {OLDER_GOAL}" in chosen.output
    assert f"from: claude session {CLAUDE_ID}" in qualified.output
    assert wrong_tool.exit_code == 1 and "no session" in wrong_tool.output
    assert ambiguous.exit_code == 1 and "ambiguous" in ambiguous.output

