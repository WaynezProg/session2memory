"""The memory hook in OpenCode: a plugin file that runs ``session-start-hook``."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from session2memory.cli import app
from session2memory.hook_install import hook_command, update_settings
from session2memory.opencode_plugin import embedded_command, render
from session2memory.session_start import hook_output

EXE = Path("/opt/s2m/bin/session2memory")


def test_install_replace_reinstall_and_remove(tmp_path: Path) -> None:
    plugin = tmp_path / "plugins" / "session2memory.js"

    preview = update_settings(plugin, agent="opencode", executable=EXE)
    assert preview.action == "add" and not plugin.exists()
    assert preview.command == hook_command(EXE, "opencode")
    assert "--agent opencode --event SessionStart --once" in preview.command

    assert update_settings(plugin, agent="opencode", executable=EXE, apply=True).action == "add"
    source = plugin.read_text(encoding="utf-8")
    assert embedded_command(source) == preview.command
    assert plugin.stat().st_mode & 0o777 == 0o644
    assert update_settings(plugin, agent="opencode", executable=EXE, apply=True).action == "none"

    moved = Path("/elsewhere/session2memory")
    plan = update_settings(plugin, agent="opencode", executable=moved, apply=True)
    assert plan.action == "replace" and plan.backup_path is None
    assert embedded_command(plugin.read_text(encoding="utf-8")) == hook_command(moved, "opencode")
    assert list(plugin.parent.iterdir()) == [plugin], "no backup is left among the plugins"

    plan = update_settings(plugin, agent="opencode", executable=EXE, remove=True, apply=True)
    assert plan.action == "remove" and not plugin.exists()
    assert update_settings(plugin, agent="opencode", executable=EXE, remove=True).action == "none"


@pytest.mark.parametrize(
    "foreign",
    [
        "export default {}\n",
        render("echo hello", 5),  # our marker, not our command
    ],
)
def test_a_plugin_this_tool_did_not_write_is_left_alone(tmp_path: Path, foreign: str) -> None:
    plugin = tmp_path / "session2memory.js"
    plugin.write_text(foreign, encoding="utf-8")
    for remove in (False, True):
        with pytest.raises(ValueError, match="not written by this tool"):
            update_settings(plugin, agent="opencode", executable=EXE, remove=remove, apply=True)
    assert plugin.read_text(encoding="utf-8") == foreign


def test_cli_installs_the_plugin(tmp_path: Path) -> None:
    plugin = tmp_path / "session2memory.js"
    result = CliRunner().invoke(
        app, ["hook-install", "--agent", "opencode", "--settings", str(plugin), "--apply"]
    )
    assert result.exit_code == 0, result.output
    assert "action: add" in result.output and plugin.exists()


def test_output_shape_the_plugin_reads() -> None:
    assert json.loads(hook_output("memo", style="opencode")) == {"additionalContext": "memo"}
    assert hook_output(None, style="opencode") == "{}"


DRIVER = """
const [plugin, mode] = process.argv.slice(2)
const mod = (await import(plugin)).default
const pushed = []
if (mode === "v2") {
  let handler
  const ctx = {
    location: { directory: "/fallback" },
    session: {
      hook: async (name, fn) => { if (name === "context") handler = fn },
      get: async ({ sessionID }) => sessionID === "child"
        ? { parentID: "ses_1" } : { location: { directory: "/work/space" } },
    },
  }
  await mod.setup(ctx)
  for (const id of ["ses_1", "ses_1", "child"]) {
    const event = { sessionID: id, system: [] }
    await handler(event)
    pushed.push({ id, system: event.system })
  }
} else {
  const hooks = await mod.server({ directory: "/v1/dir" })
  for (const id of ["ses_9", "ses_9"]) {
    const output = { system: [] }
    await hooks["experimental.chat.system.transform"]({ sessionID: id }, output)
    pushed.push({ id, system: output.system })
  }
}
console.log(JSON.stringify(pushed))
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("mode", ["v2", "v1"])
def test_the_plugin_asks_once_per_session_and_adds_the_answer(tmp_path: Path, mode: str) -> None:
    calls = tmp_path / "calls.jsonl"
    stub = tmp_path / "stub.sh"
    stub.write_text(
        "#!/bin/sh\n"
        f"cat >> '{calls}'; echo >> '{calls}'\n"
        'echo \'{"additionalContext": "MEMORY"}\'\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    # The stub stands where the executable goes; its arguments are ignored.
    plugin = tmp_path / "session2memory.mjs"
    plugin.write_text(render(f"'{stub.as_posix()}' session-start-hook", 5), encoding="utf-8")
    driver = tmp_path / "driver.mjs"
    driver.write_text(DRIVER, encoding="utf-8")

    done = subprocess.run(
        ["node", str(driver), str(plugin), mode],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    pushed = json.loads(done.stdout)
    payloads = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]

    if mode == "v2":
        text = [{"type": "text", "text": "MEMORY"}]
        assert pushed == [
            {"id": "ses_1", "system": text},
            {"id": "ses_1", "system": text},
            {"id": "child", "system": []},
        ]
        assert payloads == [
            {"session_id": "ses_1", "cwd": "/work/space", "source": "startup",
             "hook_event_name": "SessionStart"},
        ]  # fmt: skip
    else:
        assert pushed == [{"id": "ses_9", "system": ["MEMORY"]}] * 2
        assert [p["cwd"] for p in payloads] == ["/v1/dir"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_failing_or_silent_command_adds_nothing(tmp_path: Path) -> None:
    plugin = tmp_path / "session2memory.mjs"
    plugin.write_text(render("exit 3", 5), encoding="utf-8")
    driver = tmp_path / "driver.mjs"
    driver.write_text(DRIVER, encoding="utf-8")
    done = subprocess.run(
        ["node", str(driver), str(plugin), "v2"],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    assert all(entry["system"] == [] for entry in json.loads(done.stdout))
