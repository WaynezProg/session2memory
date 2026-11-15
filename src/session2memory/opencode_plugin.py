"""The OpenCode plugin that runs the memory hook.

OpenCode has no command hooks: it loads JavaScript plugins from
``~/.config/opencode/plugins/``. The plugin written here calls the same
``session-start-hook`` command the other agents run, once per session, and adds what it
answers to the system prompt of every request in that session (OpenCode rebuilds the
system prompt per request, so the text is kept in memory and pushed each time).

OpenCode 2.x calls ``setup(ctx)`` and offers the session ``context`` hook; 1.x calls
``server()`` and offers ``experimental.chat.system.transform``. The one file serves both.
"""

from __future__ import annotations

import json
import re

PLUGIN_MARKER = "// session2memory-managed:"
_COMMAND_LINE = re.compile(r"^const COMMAND = (\".*\")$", re.MULTILINE)

_TEMPLATE = """\
{marker} written by `session2memory hook-install --agent opencode`; reinstalling replaces it.
// Adds other agents' memory index and a one-time handoff to a new OpenCode session.
// Auto-loaded from ~/.config/opencode/plugins/; do not list it in opencode.json.
import {{ spawn }} from "node:child_process"

const COMMAND = {command}
const TIMEOUT_MS = {timeout_ms}
const answers = new Map()

// The hook's answer for one session, or null. It never throws.
function ask(sessionID, directory) {{
  return new Promise((resolve) => {{
    let out = ""
    let done = false
    let child
    const finish = (value) => {{
      if (done) return
      done = true
      clearTimeout(timer)
      resolve(value)
    }}
    const timer = setTimeout(() => {{
      try {{ child?.kill() }} catch {{}}
      finish(null)
    }}, TIMEOUT_MS)
    try {{
      child = spawn("/bin/sh", ["-c", COMMAND], {{ stdio: ["pipe", "pipe", "ignore"] }})
    }} catch {{
      return finish(null)
    }}
    child.on("error", () => finish(null))
    child.stdout.on("data", (chunk) => {{ out += chunk }})
    child.on("close", () => {{
      try {{
        const answer = JSON.parse(out || "{{}}")
        const text = answer?.additionalContext
        finish(typeof text === "string" && text ? text : null)
      }} catch {{
        finish(null)
      }}
    }})
    child.stdin.on("error", () => {{}})
    child.stdin.end(JSON.stringify({{
      session_id: sessionID,
      cwd: directory,
      source: "startup",
      hook_event_name: "SessionStart",
    }}))
  }})
}}

// Asked once per session in this process; every later request reuses the answer.
function memoryFor(sessionID, lookup) {{
  let answer = answers.get(sessionID)
  if (!answer) {{
    answer = lookup().then((found) => (found ? ask(sessionID, found) : null)).catch(() => null)
    answers.set(sessionID, answer)
    if (answers.size > 512) answers.delete(answers.keys().next().value)
  }}
  return answer
}}

const Session2MemoryPlugin = {{
  id: "session2memory",

  async setup(ctx) {{
    if (typeof ctx?.session?.hook !== "function") return
    await ctx.session.hook("context", async (event) => {{
      const id = event?.sessionID
      if (typeof id !== "string" || !Array.isArray(event?.system)) return
      const text = await memoryFor(id, async () => {{
        let info
        try {{ info = await ctx.session.get({{ sessionID: id }}) }} catch {{}}
        if (info?.parentID) return null // a subagent's session: its parent has the memory
        return info?.location?.directory ?? ctx.location?.directory ?? process.cwd()
      }})
      if (text) event.system.push({{ type: "text", text }})
    }})
  }},

  async server(input) {{
    const directory = input?.directory ?? process.cwd()
    return {{
      "experimental.chat.system.transform": async (hook, output) => {{
        const id = hook?.sessionID
        if (typeof id !== "string" || !Array.isArray(output?.system)) return
        const text = await memoryFor(id, async () => directory)
        if (text) output.system.push(text)
      }},
    }}
  }},
}}

export default Session2MemoryPlugin
export {{ Session2MemoryPlugin }}
"""


def render(command: str, timeout_seconds: int) -> str:
    """The plugin source that runs ``command`` (a shell command line)."""
    return _TEMPLATE.format(
        marker=PLUGIN_MARKER,
        command=json.dumps(command, ensure_ascii=False),
        timeout_ms=timeout_seconds * 1000,
    )


def embedded_command(source: str) -> str | None:
    """The command a plugin file written by :func:`render` runs; None for any other file."""
    if not source.startswith(PLUGIN_MARKER):
        return None
    match = _COMMAND_LINE.search(source)
    if match is None:
        return None
    try:
        value = json.loads(match.group(1))
    except ValueError:
        return None
    return value if isinstance(value, str) else None
