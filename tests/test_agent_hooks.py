"""The memory hook in Codex, Muse and Grok: where it is installed and what it answers.

The payload shapes below are the ones each agent was observed to send (2026-11-13).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_session_start import NOW, Bench, claude_memory, codex_summary_file, hook_arguments
from test_session_start import bench as bench  # noqa: F401 - pytest fixture
from typer.testing import CliRunner

from session2memory.cli import app
from session2memory.hook_install import AGENT_HOOKS, hook_command, update_settings
from session2memory.session_start import first_time, hook_output, run_hook, seen_marker

EXE = Path("/opt/s2m/bin/session2memory")


def ours(settings: Path, event: str) -> list[dict[str, object]]:
    groups = json.loads(settings.read_text(encoding="utf-8"))["hooks"][event]
    return [g for g in groups if "session-start-hook" in g["hooks"][0]["command"]]


@pytest.mark.parametrize("agent", ["codex", "muse"])
def test_codex_and_muse_take_a_session_start_hook_beside_existing_ones(
    tmp_path: Path, agent: str
) -> None:
    settings = tmp_path / "hooks.json"
    theirs = {"hooks": [{"type": "command", "command": "other-tool session", "timeout": 10}]}
    settings.write_text(
        json.dumps({"model": "x", "hooks": {"SessionStart": [theirs], "Stop": [theirs]}}),
        encoding="utf-8",
    )

    plan = update_settings(settings, agent=agent, executable=EXE, apply=True, now=NOW)

    written = json.loads(settings.read_text(encoding="utf-8"))
    assert plan.action == "add" and plan.backup_path is not None
    assert written["model"] == "x" and written["hooks"]["Stop"] == [theirs]
    assert written["hooks"]["SessionStart"][0] == theirs
    (group,) = ours(settings, "SessionStart")
    assert "matcher" not in group, "these agents filter by the payload's source, not a matcher"
    assert group["hooks"] == [
        {
            "type": "command",
            "command": f"'{EXE.as_posix()}' session-start-hook --agent {agent}",
            "timeout": 5,
        }
    ]
    assert update_settings(settings, agent=agent, executable=EXE, apply=True).action == "none"
    removed = update_settings(settings, agent=agent, executable=EXE, remove=True, apply=True)
    assert removed.action == "remove"
    assert json.loads(settings.read_text(encoding="utf-8"))["hooks"]["SessionStart"] == [theirs]


def test_grok_hook_lives_in_its_own_file_on_the_first_tool_call(tmp_path: Path) -> None:
    settings = tmp_path / "hooks" / "session2memory.json"

    plan = update_settings(settings, agent="grok", executable=EXE, apply=True, now=NOW)

    assert plan.action == "add" and plan.backup_path is None
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert list(written["hooks"]) == ["PreToolUse"], "Grok's SessionStart cannot reach the model"
    command = written["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == (
        '[ -e "$HOME/.local/share/session2memory/hook-seen/grok-${GROK_SESSION_ID:-}" ] '
        f"&& exit 0; '{EXE.as_posix()}' session-start-hook --agent grok "
        "--event PreToolUse --once"
    )
    update_settings(settings, agent="grok", executable=EXE, remove=True, apply=True)
    assert json.loads(settings.read_text(encoding="utf-8")) == {}


def test_unknown_agent_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown agent"):
        update_settings(tmp_path / "x.json", agent="cursor", executable=EXE)
    result = CliRunner().invoke(app, ["hook-install", "--agent", "cursor"])
    assert result.exit_code == 1 and "unknown agent" in result.output


def test_default_settings_file_of_each_agent() -> None:
    assert {name: spec.settings for name, spec in AGENT_HOOKS.items()} == {
        "claude": "~/.claude/settings.json",
        "codex": "~/.codex/hooks.json",
        "muse": "~/.config/muse/settings.json",
        "grok": "~/.grok/hooks/session2memory.json",
    }


def test_the_shell_guard_skips_python_once_the_session_was_served(tmp_path: Path) -> None:
    """The guard is what runs on every Grok tool call: it must agree with the marker."""
    seen = tmp_path / "seen dir"
    stub = tmp_path / "stub.sh"
    stub.write_text("#!/bin/sh\necho ran\n", encoding="utf-8")
    stub.chmod(0o755)
    command = hook_command(stub, "grok", seen.as_posix()).replace(
        "session-start-hook", "# session-start-hook"
    )
    env = {**os.environ, "GROK_SESSION_ID": "01a1-abc"}

    def fire() -> str:
        return subprocess.run(
            ["sh", "-c", command], env=env, capture_output=True, text=True, check=False
        ).stdout.strip()

    assert fire() == "ran"
    assert first_time(seen, "grok", "01a1-abc", NOW) is True
    assert seen_marker(seen, "grok", "01a1-abc").exists()
    assert fire() == "", "served sessions are answered by the shell alone"
    assert first_time(seen, "grok", "01a1-abc", NOW) is False
    assert first_time(seen, "grok", "", NOW) is False, "no session id: never inject"


def test_grok_payload_is_understood(bench: Bench) -> None:
    claude_memory(bench.claude_root, bench.cwd)
    payload = {
        "hookEventName": "pre_tool_use",
        "sessionId": "01a1d2e3-b3c4",
        "workspaceRoot": bench.cwd.as_posix(),
        "toolName": "run_terminal_command",
    }

    result = run_hook(bench.config("grok"), payload, {}, now=NOW)

    assert result.context is not None and "匯出格式" in result.context
    printed = json.loads(hook_output(result.context, "PreToolUse"))["hookSpecificOutput"]
    assert printed["hookEventName"] == "PreToolUse"
    assert printed["additionalContext"] == result.context


def test_grok_resumed_session_source_is_treated_as_a_resume(bench: Bench) -> None:
    claude_memory(bench.claude_root, bench.cwd)
    payload = {"sessionId": "s", "cwd": bench.cwd.as_posix(), "source": "load"}
    assert run_hook(bench.config("grok"), payload, {}, now=NOW).context is None
    payload["source"] = "new"
    assert run_hook(bench.config("grok"), payload, {}, now=NOW).context is not None


def test_muse_gets_both_other_agents_memory(bench: Bench) -> None:
    codex_summary_file(bench.memory_root, "t1", bench.cwd, "匯出增量化", hours_ago=2, name="a")
    claude_memory(bench.claude_root, bench.cwd)
    payload = {
        "hook_event_name": "SessionStart",
        "source": "startup",
        "session_id": "01a1d2e4",
        "cwd": bench.cwd.as_posix(),
        "model_provider": "meta",
    }

    context = run_hook(bench.config("muse"), payload, {}, now=NOW).context

    assert context is not None and "匯出增量化" in context and "匯出格式" in context


def test_cli_once_injects_on_the_first_firing_only(bench: Bench) -> None:
    claude_memory(bench.claude_root, bench.cwd)
    seen = bench.tmp / "seen"
    arguments = [
        *[("grok" if a == "claude" else a) for a in hook_arguments(bench)],
        "--event", "PreToolUse", "--once", "--seen-dir", seen.as_posix(),
    ]  # fmt: skip
    payload = json.dumps({"sessionId": "g1", "cwd": bench.cwd.as_posix()})

    first = CliRunner().invoke(app, arguments, input=payload)
    second = CliRunner().invoke(app, arguments, input=payload)

    output = json.loads(first.output)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse" and "匯出格式" in output["additionalContext"]
    assert json.loads(second.output) == {}
    other = CliRunner().invoke(
        app, arguments, input=json.dumps({"sessionId": "g2", "cwd": bench.cwd.as_posix()})
    )
    assert "hookSpecificOutput" in json.loads(other.output)
