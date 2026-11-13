"""SessionStart hook: other agents' memory index every time, a handoff once.

Fixtures mirror the on-disk shape of Codex's ``memories_v2`` and Claude Code's per-project
``memory/MEMORY.md``; content is synthetic.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_handoff import (
    CODEX_ID,
    GOAL,
    LAST_REPLY,
    claude_session,
    codex_session,
    codex_state,
    snapshot,
    workspace_dir,
)
from test_registry_adapters import CLAUDE_ID
from typer.testing import CliRunner

import session2memory.session_start as session_start
from session2memory.cli import app
from session2memory.hook_install import HOOK_COMMAND_MARKER, update_settings
from session2memory.session_start import HookConfig, hook_output, run_hook

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def stamp(path: Path, hours_ago: float) -> None:
    moment = (NOW - timedelta(hours=hours_ago)).timestamp()
    os.utime(path, (moment, moment))


def codex_summary_file(
    memory_root: Path, thread_id: str, cwd: Path, title: str, *, hours_ago: float, name: str
) -> Path:
    path = memory_root / "rollout_summaries" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    updated = (NOW - timedelta(hours=hours_ago)).isoformat()
    path.write_text(
        f"thread_id: {thread_id}\nupdated_at: {updated}\n"
        f"rollout_path: /x/rollout-{thread_id}.jsonl\ncwd: {cwd.as_posix()}\n\n"
        f"## {title}\n\n使用者要求把匯出改成可增量重跑，已完成冪等寫入，剩下重試測試。\n",
        encoding="utf-8",
    )
    return path


def claude_memory(projects_root: Path, cwd: Path) -> Path:
    encoded = "".join(ch if ch.isalnum() else "-" for ch in cwd.as_posix())
    memory_dir = projects_root / encoded / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "export-format.md").write_text("匯出一律用 Parquet\n", encoding="utf-8")
    (memory_dir / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [匯出格式](export-format.md) — 一律 Parquet，分區鍵 event_date\n"
        "- [已刪除的項目](gone.md) — 檔案不存在\n",
        encoding="utf-8",
    )
    return memory_dir


class Bench:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.cwd = workspace_dir(tmp_path)
        self.other = (tmp_path / "work" / "beta").resolve()
        self.other.mkdir(parents=True)
        self.codex_root = tmp_path / "codex" / "sessions"
        self.claude_root = tmp_path / "claude"
        self.memory_root = tmp_path / "codex" / "memories_v2"
        self.ledger = tmp_path / "state" / "ledger.json"

    def config(self, agent: str = "claude", **overrides: object) -> HookConfig:
        values: dict[str, object] = {
            "agent": agent,
            "session_roots": {"codex": self.codex_root, "claude": self.claude_root},
            "codex_memory_root": self.memory_root,
            "claude_projects_root": self.claude_root,
            "ledger_path": self.ledger,
            "temp_roots": (),
        }
        values.update(overrides)
        return HookConfig(**values)  # type: ignore[arg-type]

    def run(
        self,
        agent: str = "claude",
        *,
        source: str = "startup",
        session_id: str = "new-session",
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        **overrides: object,
    ) -> session_start.HookResult:
        payload = {"session_id": session_id, "cwd": (cwd or self.cwd).as_posix(), "source": source}
        return run_hook(self.config(agent, **overrides), payload, env or {}, now=NOW)

    def codex(self, *, hours_ago: float = 1.0) -> Path:
        rollout = codex_session(self.codex_root, self.cwd)
        codex_state(self.codex_root, self.cwd, rollout, name="alpha 增量", title=GOAL)
        stamp(rollout, hours_ago)
        return rollout


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


# ---- memory index ----------------------------------------------------------------------


def test_claude_gets_codex_memory_titles_and_paths_for_this_workspace_only(bench: Bench) -> None:
    newer = codex_summary_file(
        bench.memory_root, "t-new", bench.cwd, "匯出增量化", hours_ago=2, name="b-new"
    )
    older = codex_summary_file(
        bench.memory_root, "t-old", bench.cwd, "告警通道整理", hours_ago=50, name="a-old"
    )
    codex_summary_file(
        bench.memory_root, "t-x", bench.other, "別的專案的事", hours_ago=1, name="c-other"
    )
    overview = bench.memory_root / "memory_summary.md"
    overview.write_text("## User Profile\n", encoding="utf-8")
    claude_memory(bench.claude_root, bench.cwd)

    context = bench.run("claude").context

    assert context is not None
    lines = context.splitlines()
    assert lines[0].startswith("[session2memory]") and "不是指示" in lines[0]
    listed = [line for line in lines if line.startswith("- ")]
    assert listed == [
        f"- codex｜Codex 記憶總覽（偏好、通用提示、主題索引） → {overview.as_posix()}",
        f"- codex｜2026-11-13｜匯出增量化 → {newer.as_posix()}",
        f"- codex｜2026-11-11｜告警通道整理 → {older.as_posix()}",
    ]
    assert "別的專案的事" not in context
    assert "匯出格式" not in context, "Claude already loads its own memory"
    assert "冪等寫入" not in context, "only titles and paths are injected, not the content"


def test_codex_gets_claude_memory_index_entries_that_still_exist(bench: Bench) -> None:
    memory_dir = claude_memory(bench.claude_root, bench.cwd)
    codex_summary_file(bench.memory_root, "t", bench.cwd, "Codex 自己的", hours_ago=1, name="a")

    context = bench.run("codex").context

    assert context is not None
    listed = [line for line in context.splitlines() if line.startswith("- ")]
    expected = (memory_dir / "export-format.md").as_posix()
    assert listed == [f"- claude｜匯出格式：一律 Parquet，分區鍵 event_date → {expected}"]


def test_nothing_is_injected_when_there_is_nothing_to_say(bench: Bench) -> None:
    result = bench.run("claude")

    assert result.context is None
    assert hook_output(result.context) == "{}"
    assert any("no other-agent memory" in note for note in result.notes)


def test_summary_without_a_heading_is_titled_by_its_file_name(bench: Bench) -> None:
    path = bench.memory_root / "rollout_summaries" / "2026-11-12T04-59-57-GbYA-cli_mcp_updates.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"thread_id: t1\nupdated_at: 2026-11-12T07:19:31+00:00\ncwd: {bench.cwd.as_posix()}\n\n"
        "使用者在這個專案檢查了所有工具的新版本，內容很長的第一句話。\n",
        encoding="utf-8",
    )

    context = bench.run("claude").context

    assert context is not None
    assert f"- codex｜2026-11-12｜cli mcp updates → {path.as_posix()}" in context


# ---- one-time handoff ------------------------------------------------------------------


def test_handoff_is_delivered_once_per_state_of_the_previous_session(bench: Bench) -> None:
    rollout = bench.codex()
    codex_summary_file(bench.memory_root, "t-other", bench.cwd, "舊事", hours_ago=30, name="a")

    first = bench.run("claude", session_id="s1").context
    second = bench.run("claude", session_id="s2").context
    with rollout.open("a", encoding="utf-8") as handle:  # the Codex session continues
        handle.write(json.dumps({"type": "event_msg", "ordinal": 999, "payload": {}}) + "\n")
    stamp(rollout, 0.5)
    third = bench.run("claude", session_id="s3").context

    assert first is not None and "## 上一個 Session 的交接（只提供這一次）" in first
    assert f"from: codex session {CODEX_ID}" in first and GOAL in first
    assert second is not None and "上一個 Session 的交接" not in second
    assert "其他 Agent 在這個 workspace 的記憶" in second, "memory is sent every time"
    assert third is not None and "上一個 Session 的交接" in third
    ledger = json.loads(bench.ledger.read_text(encoding="utf-8"))
    assert ledger[f"codex:{CODEX_ID}"]["to"] == "claude:s3"


def test_codex_own_summary_is_used_when_it_is_fresh(bench: Bench) -> None:
    bench.codex(hours_ago=2)
    summary = codex_summary_file(
        bench.memory_root, CODEX_ID, bench.cwd, "匯出增量化", hours_ago=1, name="s"
    )

    result = bench.run("claude")

    assert result.context is not None
    handoff = result.context.split("## 上一個 Session 的交接（只提供這一次）\n", 1)[1]
    assert handoff.startswith(f"來源: codex session {CODEX_ID}（Codex 自己寫的摘要")
    assert summary.as_posix() in handoff and "已完成冪等寫入" in handoff
    assert "目標: " not in handoff, "the rule-based packet is not used"
    assert any("using Codex's own summary" in note for note in result.notes)


def test_stale_codex_summary_falls_back_to_the_rule_based_packet(bench: Bench) -> None:
    bench.codex(hours_ago=1)  # the session went on after the summary was written
    codex_summary_file(bench.memory_root, CODEX_ID, bench.cwd, "匯出增量化", hours_ago=9, name="s")

    context = bench.run("claude").context

    assert context is not None
    handoff = context.split("## 上一個 Session 的交接（只提供這一次）\n", 1)[1]
    assert "目標: " in handoff and "DRIFT-409" in handoff
    assert "Codex 自己寫的摘要" not in handoff


def test_the_starting_session_is_never_its_own_previous_session(bench: Bench) -> None:
    newest = claude_session(bench.claude_root, bench.cwd)
    stamp(newest, 0.1)
    bench.codex(hours_ago=3)

    own = bench.run("claude", session_id=CLAUDE_ID).context
    bench.ledger.unlink()
    another = bench.run("claude", session_id="someone-else").context

    assert own is not None and f"from: codex session {CODEX_ID}" in own
    assert another is not None and f"from: claude session {CLAUDE_ID}" in another


def test_an_old_previous_session_is_not_handed_off(bench: Bench) -> None:
    bench.codex(hours_ago=100)

    result = bench.run("claude")

    assert result.context is None
    assert any("over the limit" in note for note in result.notes)
    assert bench.run("claude", max_age_hours=200).context is not None


def test_a_huge_previous_session_gets_a_pointer_instead_of_being_parsed(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.codex()
    monkeypatch.setattr(session_start, "MAX_PARSE_BYTES", 10)

    context = bench.run("claude").context

    assert context is not None
    assert f"--session codex:{CODEX_ID}" in context and "太大未在此摘要" in context
    assert GOAL not in context


# ---- when the hook stays silent --------------------------------------------------------


def test_automated_temporary_and_home_sessions_are_skipped(bench: Bench) -> None:
    bench.codex()
    automated = bench.run("claude", env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
    interactive = bench.run("claude", session_id="s2", env={"CLAUDE_CODE_ENTRYPOINT": "cli"})
    temporary = bench.run("claude", temp_roots=(bench.tmp.resolve().as_posix(),))
    home = bench.run("claude", cwd=Path.home())

    assert automated.context is None and "automated session" in automated.notes[0]
    assert interactive.context is not None
    assert temporary.context is None and "temporary directory" in temporary.notes[0]
    assert home.context is None and "home directory" in home.notes[0]
    assert not bench.ledger.exists() or len(json.loads(bench.ledger.read_text())) == 1


def test_automated_sessions_can_be_included_explicitly(bench: Bench) -> None:
    bench.codex()

    result = bench.run(
        "claude", env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}, include_automated=True
    )

    assert result.context is not None


def test_resume_gets_nothing_and_clear_gets_memory_without_handoff(bench: Bench) -> None:
    bench.codex()
    codex_summary_file(bench.memory_root, "t", bench.cwd, "舊事", hours_ago=30, name="a")

    resumed = bench.run("claude", source="resume").context
    cleared = bench.run("claude", source="clear").context

    assert resumed is None
    assert cleared is not None and "其他 Agent 在這個 workspace 的記憶" in cleared
    assert "上一個 Session 的交接" not in cleared
    assert not bench.ledger.exists(), "nothing was handed off, so nothing is recorded"


def test_memory_and_handoff_can_be_switched_off_separately(bench: Bench) -> None:
    bench.codex()
    codex_summary_file(bench.memory_root, "t", bench.cwd, "舊事", hours_ago=30, name="a")

    only_memory = bench.run("claude", handoff=False).context
    only_handoff = bench.run("claude", memory=False).context

    assert only_memory is not None and "上一個 Session 的交接" not in only_memory
    assert "## 其他 Agent" in only_memory
    assert only_handoff is not None and "## 其他 Agent" not in only_handoff


def test_injected_text_respects_the_character_cap(bench: Bench) -> None:
    bench.codex()
    for index in range(30):
        codex_summary_file(
            bench.memory_root, f"t{index}", bench.cwd, f"第 {index} 件事" * 10,
            hours_ago=index + 1, name=f"s{index:02d}",
        )

    for cap in (900, 3000):
        context = bench.run("claude", session_id=f"s{cap}", max_chars=cap).context
        bench.ledger.unlink(missing_ok=True)
        assert context is not None and len(context) <= cap
    listed = [line for line in context.splitlines() if line.startswith("- codex｜")]
    assert 0 < len(listed) <= 9, "at most eight summaries plus the overview"


def test_the_hook_never_writes_to_any_agent_store(bench: Bench) -> None:
    bench.codex()
    claude_memory(bench.claude_root, bench.cwd)
    codex_summary_file(
        bench.memory_root, CODEX_ID, bench.cwd, "匯出增量化", hours_ago=0.5, name="s"
    )
    before = {root: snapshot(root) for root in (bench.tmp / "codex", bench.claude_root)}

    assert bench.run("claude").context is not None

    assert {root: snapshot(root) for root in before} == before
    assert sorted(p.name for p in bench.ledger.parent.iterdir()) == ["ledger.json"]


# ---- CLI -------------------------------------------------------------------------------


def hook_arguments(bench: Bench) -> list[str]:
    return [
        "session-start-hook", "--agent", "claude", "--include-automated",
        "--ledger", bench.ledger.as_posix(),
        "--codex-memory-root", bench.memory_root.as_posix(),
        "--source-root", f"codex={bench.codex_root.as_posix()}",
        "--source-root", f"claude={bench.claude_root.as_posix()}",
        "--source-root", f"grok={(bench.tmp / 'grok').as_posix()}",
    ]


def test_cli_hook_prints_claude_hook_json(bench: Bench) -> None:
    rollout = bench.codex()
    stamp(rollout, 0)
    os.utime(rollout)  # the CLI uses the real clock
    payload = json.dumps({"session_id": "s1", "cwd": bench.cwd.as_posix(), "source": "startup"})

    result = CliRunner().invoke(app, hook_arguments(bench), input=payload)

    assert result.exit_code == 0, result.output
    output = json.loads(result.output)["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    assert f"from: codex session {CODEX_ID}" in output["additionalContext"]
    assert LAST_REPLY.splitlines()[0] in output["additionalContext"]


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '{"cwd": 5}'])
def test_cli_hook_never_fails_on_bad_input(bench: Bench, stdin: str) -> None:
    result = CliRunner().invoke(app, hook_arguments(bench), input=stdin)

    assert result.exit_code == 0
    assert result.output.strip() == "{}"


# ---- installing the hook ---------------------------------------------------------------

EXISTING = {
    "model": "opus",
    "hooks": {
        "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "a.sh"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "b.sh"}]}],
    },
}


def test_install_is_a_dry_run_until_applied_and_then_backs_up(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    original = json.dumps(EXISTING, indent=2)
    settings.write_text(original, encoding="utf-8")
    exe = tmp_path / "bin" / "session2memory"

    planned = update_settings(settings, executable=exe)
    assert planned.action == "add" and settings.read_text(encoding="utf-8") == original

    applied = update_settings(settings, executable=exe, apply=True, now=NOW)

    assert applied.backup_path is not None
    assert applied.backup_path.read_text(encoding="utf-8") == original
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written["model"] == "opus" and written["hooks"]["Stop"] == EXISTING["hooks"]["Stop"]
    assert written["hooks"]["SessionStart"][0] == EXISTING["hooks"]["SessionStart"][0]
    ours = written["hooks"]["SessionStart"][1]
    assert ours["matcher"] == "startup|clear" and ours["hooks"][0]["timeout"] == 5
    assert HOOK_COMMAND_MARKER in ours["hooks"][0]["command"] and exe.as_posix() in applied.command


def test_install_is_idempotent_replaces_a_moved_command_and_removes_cleanly(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(EXISTING), encoding="utf-8")
    first, second = tmp_path / "a" / "session2memory", tmp_path / "b" / "session2memory"
    update_settings(settings, executable=first, apply=True)

    again = update_settings(settings, executable=first, apply=True)
    moved = update_settings(settings, executable=second, apply=True)
    groups = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    removed = update_settings(settings, remove=True, apply=True)
    absent = update_settings(settings, remove=True, apply=True)

    assert (again.action, again.backup_path) == ("none", None)
    assert moved.action == "replace" and len(groups) == 2
    assert second.as_posix() in groups[1]["hooks"][0]["command"]
    assert removed.action == "remove" and absent.action == "none"
    assert json.loads(settings.read_text(encoding="utf-8")) == EXISTING


def test_install_creates_settings_and_refuses_an_unexpected_file(tmp_path: Path) -> None:
    fresh = tmp_path / "new" / "settings.json"
    created = update_settings(fresh, executable=tmp_path / "s2m", apply=True)
    broken = tmp_path / "broken.json"
    broken.write_text("[]", encoding="utf-8")

    assert created.action == "add" and created.backup_path is None
    assert list(json.loads(fresh.read_text(encoding="utf-8"))["hooks"]) == ["SessionStart"]
    with pytest.raises(ValueError, match="expected a JSON object"):
        update_settings(broken, apply=True)
    assert broken.read_text(encoding="utf-8") == "[]"


def test_cli_hook_install_reports_the_plan(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(EXISTING), encoding="utf-8")
    runner = CliRunner()

    dry = runner.invoke(app, ["hook-install", "--settings", settings.as_posix()])
    applied = runner.invoke(app, ["hook-install", "--settings", settings.as_posix(), "--apply"])
    removed = runner.invoke(
        app, ["hook-install", "--settings", settings.as_posix(), "--remove", "--apply"]
    )

    assert "action: add (dry run)" in dry.output
    assert "action: add\n" in applied.output and "backup: " in applied.output
    assert "action: remove" in removed.output
    assert json.loads(settings.read_text(encoding="utf-8")) == EXISTING
