# session2memory

`session2memory` turns messy local AI coding sessions into clean,
evidence-backed, HKS-ingestable project memory without importing raw
transcripts.

It is not a complete shared memory system. It is a local memory compiler:
it reads coding-agent session stores, extracts conservative project memory,
and writes a refined source folder for HKS.

Raw transcripts do not enter HKS. Raw session stores remain local evidence
sources; generated Markdown contains only daily logs, durable memories, and
evidence ids that point back to the original session.

Architecture, pipeline, session-start and data-model diagrams are in
[docs/architecture.md](docs/architecture.md).

A native macOS console for browsing memory, sessions and runs, and for testing HKS
queries, is in [apps/mac](apps/mac/README.md).

## Supported P0 Sources

- Claude Code JSONL under `~/.claude/projects`
- Claude Desktop local-agent JSONL and session metadata under
  `~/Library/Application Support/Claude`
- Codex JSONL under `~/.codex/sessions`
- Qwen Code JSONL under `~/.qwen`
- OpenCode SQLite under `~/.local/share/opencode/opencode.db`
- Cursor GUI SQLite stores under `~/.cursor/chats`
- Cursor CLI JSONL transcripts under `~/.cursor/projects`
- OpenClaw text logs under `~/.openclaw/logs`
- Hermes text logs under `~/.hermes/logs`

Optional **agentic-os evidence** enriches `evidence/index.jsonl` from
`~/.agentic-os/agentic-os.db` (session + audit pointers). Harness logs remain
the primary transcript source.

File-based sources use their normal session paths plus any JSONL files modified
on the import date, so reopened older sessions are still scanned.
Claude Desktop support is scoped to local-agent JSONL transcripts and
`claude-code-sessions` metadata. It does not parse the GUI chat cache stored in
Chromium IndexedDB/Local Storage, and it does not ingest local audit logs as
transcripts.

Check which supported local stores are present before importing:

```bash
uv run session2memory discover
```

Discovery reports supported source roots and known local executables. It does
not claim arbitrary AI coding tools are ingestible until an adapter exists for
their session format.

## Generate Memory Docs

Dry-run first if you only want to see what would be scanned:

```bash
uv run session2memory import --date 2026-05-22 --workspace "$PWD" --output ./out/session-memory --dry-run
```

- `uv run session2memory import`: run the local CLI without installing it globally.
- `--date 2026-05-22`: scan sessions created or updated on this date. For file
  sources, this also includes older session files whose mtime falls on this date.
- `--workspace "$PWD"`: keep only sessions opened from the current repo.
- `--output ./out/session-memory`: choose where generated docs would be written.
- `--dry-run`: report counts only; do not write or replace files.

Generate the HKS-ingestable output after the dry-run looks right:

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
```

The command above scans all supported tools for that date. Add filters when you
want a narrower run:

```bash
uv run session2memory import \
  --date 2026-05-22 \
  --workspace "$PWD" \
  --tool codex \
  --output ./out/session-memory
```

- `uv run session2memory import \`: start an import command.
- `--date 2026-05-22 \`: choose the import date; reopened older sessions updated
  on this date are included.
- `--workspace "$PWD" \`: limit results to the current repo.
- `--tool codex \`: scan only Codex sessions. Repeat `--tool` for more tools.
- `--output ./out/session-memory`: write generated docs under this folder.

Re-running import with the same `--output` replaces the managed generated files:
`daily/`, `evidence/`, `review/`, and `manifest.json`. It does not delete local
raw session stores, `worklogs/`, or HKS data under `KS_ROOT`.

Import writes three layers:

- `daily/YYYY-MM-DD.md`: HKS-ingestable daily log.
- `evidence/index.jsonl`: provenance pointers back to original local sessions.
- `review/YYYY-MM-DD.jsonl`: pending memory candidates for review.

It does not write new entries into `memories/` directly.
Daily and review rows include compact provenance such as
`source: codex, session: <id>, lines: 2-2`. Full raw `source_path` values stay
in `evidence/index.jsonl`.

Limit to one tool:

```bash
uv run session2memory import \
  --date 2026-05-22 \
  --tool codex \
  --output ./out/session-memory
```

Override a source root:

```bash
uv run session2memory import \
  --date 2026-05-22 \
  --source-root codex=/Users/waynetu/.codex/sessions \
  --output ./out/session-memory
```

## Worklog Aggregation

Weekly or monthly rollups must read `session2memory.db`, not `daily/*.md`, because
re-import replaces managed generated files and would drop older daily markdown from the
export tree. `worklog` aggregates `candidates` by `import_date` range and writes
HKS-ingestable markdown under `worklogs/` with date-based filenames.

```bash
uv run session2memory worklog yesterday --output ./out/session-memory
uv run session2memory worklog last-week --output ./out/session-memory
uv run session2memory worklog last-month --output ./out/session-memory
uv run session2memory worklog --from 2026-06-01 --to 2026-06-07 --output ./out/session-memory
```

- Period aliases (`yesterday`, `last-week`, `last-month`) only affect the resolved date
  range; output files are always named by ISO dates, for example `worklogs/2026-06-08.md`
  or `worklogs/2026-06-01_2026-06-07.md`.
- Requires `session2memory.db` under `--output` (or `--state-db`). Run `import` first.
- Front matter uses `hks_type: session_worklog`. Body sections are Shipped, Verified,
  Decisions, Constraints, Pitfalls, and Notes. Every line keeps `evidence_id`, `tool`,
  `session_id`, and `lines` metadata.
- v1 is deterministic aggregation only; no LLM synthesis or external sources.

## HKS Ingest (agents)

Agents should use the HKS MCP **agent profile** (`hks-mcp --profile agent`), not
direct `ks ingest` on raw session stores.

1. Import into the export tree:

```bash
export HKS_SESSION2MEMORY_EXPORT_ROOT="${HKS_SESSION2MEMORY_EXPORT_ROOT:-$HOME/session2memory/export}"
workspace_id=repo-123   # session2memory id from manifest, or git basename if you named the folder that way

uv run session2memory import \
  --date 2026-05-22 \
  --workspace "$PWD" \
  --output "$HKS_SESSION2MEMORY_EXPORT_ROOT/$workspace_id"
```

2. Review and promote (see below).

3. Ingest via MCP:

```text
hks_workspace_ingest_session_memory(
  workspace_id="<workspace_id>",
  path="daily/2026-05-22.md",
)
hks_workspace_query(workspace_id="<workspace_id>", query="...", writeback=no)
```

Configure `HKS_KS_ROOT_BASE`, `HKS_WORKSPACE_REGISTRY`, and `HKS_EMBEDDING_MODEL`
per the HKS repo docs. The ingest `path` is relative to
`$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/`.

Do **not** ingest `~/.codex/sessions`, `~/.claude/projects`,
`~/Library/Application Support/Claude`, `~/.qwen`, OpenCode SQLite,
`~/.cursor/chats`, `~/.cursor/projects`, or raw logs. HKS reads only generated
Markdown from the export tree.

## Operator / scheduled ingest

Batch jobs on this machine may use `scripts/daily-session-memory-to-hks.sh`,
which runs `ks ingest` / `ks update` against a combined local `./out/session-memory`
tree and a dedicated `KS_ROOT`. That path is for operators and launchd, not for
autonomous agents following the skill contract.

## Review And Promote

Durable project memory enters `memories/` only after review.

After promote, push promoted memories back into harness context files:

```bash
uv run session2memory sync \
  --workspace "$PWD" \
  --output ./out/session-memory
```

By default this updates `AGENTS.md`, `CLAUDE.md`, and
`.cursor/rules/session2memory-memory.mdc` using an idempotent managed block.
Repeat `--target` for `codex` (also `AGENTS.md`), `openclaw`, or `hermes`
(global memory under `~/.openclaw/memory` / `~/.hermes/memory`).

Optional LLM extraction adds review candidates without auto-writing durable memory:

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory --llm-extract
```

Set `SESSION2MEMORY_LLM_CMD` for the subprocess backend, or pass `--llm-cmd`.
By default the prompt is appended as the last argv; use `--llm-input stdin` for
commands that read prompts from standard input.

```bash
uv run session2memory import \
  --date 2026-05-22 \
  --output ./out/session-memory \
  --llm-extract \
  --llm-cmd "acpx codex exec" \
  --llm-timeout 180 \
  --strict-llm
```

Use `--llm-backend mock` only for tests.

Interactive review TUI (requires `uv sync --group ui`):

```bash
uv run session2memory review ui --date 2026-05-22 --output ./out/session-memory
```

Local web review UI (stdlib HTTP server):

```bash
uv run session2memory review web \
  --date 2026-05-22 \
  --output ./out/session-memory \
  --host 127.0.0.1 \
  --port 8765
```

Phase 2 state (default on import) keeps stable ids in `session2memory.db`, supports
incremental re-import, redacted exports, memory revoke/supersede, and sync hash skip.
`memory revoke` and `memory supersede` re-export active `memories/*.md` entries and
resync any targets already recorded in `sync_targets`:

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
uv run session2memory memory revoke m_0123456789ab --output ./out/session-memory
uv run session2memory memory supersede --old m_old --new m_new --output ./out/session-memory
```

Use `session2memory sync --since-last-sync` to skip manually requested sync writes
whose rendered body hash is unchanged.

Use `--no-state` for legacy file-only imports without the database.

List candidates:

```bash
uv run session2memory review list \
  --date 2026-05-22 \
  --output ./out/session-memory
```

- `review list`: show pending, approved, rejected, or promoted candidates.
- `--date 2026-05-22`: read the review queue for that date.
- `--output ./out/session-memory`: read the generated output folder.

Inspect one candidate with its evidence preview:

```bash
uv run session2memory review inspect r000001 \
  --date 2026-05-22 \
  --output ./out/session-memory
```

- `review inspect r000001`: show one candidate, its evidence pointer, and a preview.

Approve one candidate:

```bash
uv run session2memory review approve r000001 \
  --date 2026-05-22 \
  --output ./out/session-memory \
  --note "keep this"
```

- `review approve r000001`: mark the candidate as accepted.
- `--note "keep this"`: save the reviewer note in the review row.

Reject one candidate:

```bash
uv run session2memory review reject r000001 \
  --date 2026-05-22 \
  --output ./out/session-memory \
  --note "too local"
```

- `review reject r000001`: keep the candidate out of durable memory.

If a reviewer wants a daily-only candidate to become durable memory, approve it
with `--durable`:

```bash
uv run session2memory review approve r000001 \
  --date 2026-05-22 \
  --output ./out/session-memory \
  --durable
```

Then run:

```bash
uv run session2memory review promote \
  --date 2026-05-22 \
  --output ./out/session-memory
```

- `review promote`: append approved durable candidates into `memories/<workspace-id>.md`.

Approved entries are appended to `memories/<workspace-id>.md`, and their review
status changes to `promoted`. Pending entries stay out of durable memory.
Entries with `durable_suggestion: false` also stay out unless a reviewer
explicitly marks them with `review approve --durable`.

## Current Extraction Model

P0 extraction is marker-based and conservative. It recognizes explicit session
lines such as `Decision:`, `Done:`, `Pitfall:`, `Constraint:`, and
`Verification:`. This avoids invented memory, but it can miss important context
that was never marked.

Durable memories are kept separate from daily logs. `Decision`, `Pitfall`,
and `Constraint` entries can enter `memories/`; `Done` and `Verification`
entries stay in `daily/` plus `evidence/`.

## Roadmap

Bulk review and conflict-aware promote:

```bash
uv run session2memory review approve-bulk --date 2026-05-22 --output ./out/session-memory
uv run session2memory review conflicts --date 2026-05-22 --output ./out/session-memory
uv run session2memory review promote --date 2026-05-22 --output ./out/session-memory \
  --resolve keep-new
```

## Verification

```bash
uv run pytest tests/integration/test_hks_compatibility.py -q
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```

## Source registry (stable session identity)

With the state DB enabled, adapters that report a content digest (currently Codex) are
tracked in a source registry inside `session2memory.db`:

- A session is `tool:native_session_id`; a message is addressed by a native locator
  (Codex rollout `ordinal`), not by file path or line number.
- Every on-disk copy is a *location* of that source. When Codex natively compresses
  `rollout-….jsonl` to `rollout-….jsonl.zst`, or the same thread also exists under another
  home, it is registered as another location and is not imported again.
- `StateStore.sources.resolve_candidate(...)` finds the exact source message in any location
  that is still present and verifies its digest; it reports a failure when none is left.

Reading `.jsonl.zst` needs the `zstd` executable on `PATH`; files are streamed, never
unpacked to disk.

By default only the main Codex home (`--source-root codex=…`) is scanned. Pass
`--include-openclaw-codex-homes` to `import` or `discover` to also scan every
`$OPENCLAW_STATE_DIR/agents/<agent>/agent/codex-home/sessions`. It is opt-in because those
sessions are agent-private and would otherwise flow into the shared output.

### Registry-tracked adapters

| Tool | Source | Session id | Message locator |
| --- | --- | --- | --- |
| `codex` | `rollout-*.jsonl` / `.jsonl.zst` | thread id | `ord:<ordinal>` |
| `claude`, `claude-desktop` | `<project>/<session>.jsonl` | `sessionId` | `uuid:<uuid>` |
| `openclaw-native` | `<state>/agents/<agent>/agent/openclaw-agent.sqlite` (opened read-only) | `<agent>/<session id>` | `event:<event_id>` |
| `grok` | `<cwd>/<session>/chat_history.jsonl` | directory name | `msg:<position>` (Grok records have no id) |
| `cursor` | `<hash>/<session>/store.db` | `agentId` in `meta` | `blob:<blob id>` |
| `cursor-cli` | `agent-transcripts/<session>.jsonl` | file name | `msg:<position>` |
| `muse` | `YYYY/MM/DD/<session>/session.jsonl` | session stream id | `rec:<record id>` |

`openclaw-native`, `grok` and `muse` are opt-in: they are imported only when named with
`--tool`, never as part of the default tool set. `openclaw-native` reads agent-private
session stores; the conversation source of record for OpenClaw remains LCM.

The Claude adapter skips `api-request*` records: they are copies of what was sent to the
API (system prompt, replayed turns), not turns of the conversation.

## Handoff packet (manual, read-only)

Run these from this repository (or add `--project <path to this repo>` to `uv run` when
calling it from another directory, such as the workspace being handed off):

```bash
uv run session2memory handoff --workspace /path/to/workspace
uv run session2memory handoff --workspace /path/to/workspace --from-tool codex --max-chars 2000
```

Without `--from-tool` the latest session across codex, claude, grok, muse, antigravity,
cursor and cursor-cli is used. To
hand off a specific session instead, add `--session <id>`: the full session id, a unique
prefix of it, or `<tool>:<id>`. When the id is unknown or ambiguous the error lists the
most recent session ids of the workspace. Each
quoted line of the packet ends with a reference in square brackets. Copy one exactly as
printed to read the full message:

```bash
uv run session2memory handoff-show '<reference copied from the packet>' --workspace /path/to/workspace
```

`handoff` prints a short packet for the most recent session of a workspace so another
agent can continue it. It reads the agent's own store directly (no import, no state DB,
no hook, no LLM) and never writes to it. The packet holds, in order of what is kept when
space is tight: the first goal, the latest request and the last assistant reply (shortened
to fit, never dropped), then the title, the agent's own summary where one exists, earlier
requests and marker lines. Each quoted item ends with an evidence reference that
`handoff-show` resolves to the full message. `--max-chars` (default 4000, minimum 600) is a
hard upper bound.

What the agents record natively is mostly a title, not a summary: Grok's `session_summary`
repeats its title, Codex's thread `title` is the first user message (the short `name` is
used instead), Claude Code has a real summary only after a compaction, Muse has a session
name. Harness-injected user messages (`<system-reminder>`, `<environment_context>`, …) are
skipped, whether they come before or after the user's words; `<user_query>` and
`<pasted_content>` are unwrapped, and Codex's `## My request:` heading marks where the request
starts. The summary a compacted session restarts with is shown as a summary, not a request.
Bare acknowledgements ("Commit", "修正", "ok") are left out of the goal and the recent
requests unless the session has nothing else.

`openclaw-native` is searched only with `--from-tool openclaw-native`.

## SessionStart hook (passive)

```bash
uv run session2memory hook-install            # show what would change
uv run session2memory hook-install --apply    # add the hook to ~/.claude/settings.json
uv run session2memory hook-install --remove --apply
uv run session2memory hook-install --agent codex --apply   # also: muse, grok, antigravity, cursor
```

| Agent | Where the hook goes | When it injects |
| --- | --- | --- |
| Claude Code | `~/.claude/settings.json`, `SessionStart` | session start and `/clear` |
| Codex | `~/.codex/hooks.json`, `SessionStart` | session start. Codex runs a new hook only after you approve it once (`/hooks` in the TUI) |
| Muse | `~/.config/muse/settings.json`, `SessionStart` | session start |
| Grok | `~/.grok/hooks/session2memory.json`, `PostToolUse` (+ a `SessionStart` watcher) | after the first tool call of a new session |
| Antigravity CLI | `~/.gemini/config/hooks.json`, a named `session2memory` hook on `SessionStart` | once per new conversation (as an inserted system message) |
| Cursor | `~/.cursor/hooks.json`, `sessionStart` | when a new conversation is created (as `additional_context`) |

Grok shows a `SessionStart` hook's output to the person only and never to the model, so
its hook sits on `PostToolUse` and adds the context once, after the first tool call that
actually ran (context given before a call is dropped when the call is refused). A shell
test in front of the command answers every later call without starting Python, and a
second entry on `SessionStart` only notes resumed sessions so that they stay silent. A
Grok session that never runs a tool gets nothing.

`hook-install` adds one `SessionStart` entry to Claude Code's settings, leaves every other
hook as it is, and writes a timestamped backup (`settings.json.bak-session2memory-…`)
before changing the file. The entry runs `session2memory session-start-hook`, which adds
two things to a new session of a workspace:

- **Memory index, every time** (`startup` and `clear`): titles and file paths of the memory
  *other* agents keep for this workspace: Codex's own rollout summaries and memory
  overview for a Claude session, Claude Code's `memory/MEMORY.md` entries for a Codex
  session. Only titles and paths are injected; the agent reads a file when it needs it.
- **Handoff, once** (`startup` only): the previous session of the workspace across codex,
  claude and grok, delivered once per state of that session and only if it was active in
  the last 72 hours. When Codex wrote its own summary for that session and it is not older
  than the session, the summary is used; otherwise the rule-based packet is.

It stays silent for headless sessions (entrypoint `sdk-*`, which is how orchestrators and
`claude -p` start Claude), temporary directories, the home directory and resumed sessions.
A session larger than 40 MB is not parsed inside the hook; a pointer to `handoff --session`
is given instead. The text is capped (`--max-chars`, default 3000) and is labelled as
reference material, not instructions. Nothing is written to any agent's store; the only
state is the delivery ledger at `~/.local/share/session2memory/handoff-ledger.json`. Any
error results in no injection, never in a failed session.

No embeddings or reranking are involved here: at session start there is no query yet, so
the index is selected by workspace and recency. Search by meaning is `auto search`, below.

## Unattended pipeline (`auto`)

```bash
uv run session2memory auto run --dry-run     # what would happen, no file changes, no LLM
uv run session2memory auto run               # one full run
uv run session2memory auto status            # counts and the last run's report
uv run session2memory auto search "paperclip 暫停"
uv run session2memory auto memory --workspace /path/to/workspace
```

`auto run` keeps one persistent store (`~/.local/share/session2memory/store`) and does, in
order:

| Step | What it does |
| --- | --- |
| `scan` | Registers every session changed since the last run (Codex incl. per-agent homes, Claude Code, Grok, Muse, Antigravity CLI, Cursor chat stores and agent transcripts, OpenClaw native) in the source registry and stores a compact digest: the user's requests and the reply each ended with. First run looks back `--since-days` (30); `--rescan` does that again once, for a store that was added later. |
| `compress` | Cold `rollout-*.jsonl` files in Codex homes that Codex does not maintain itself are compressed in place to `.jsonl.zst` (Codex reads and re-inflates these on resume). The original is moved to `holding/` and removed only after the holding period, once the compressed file has been verified again. |
| `archive` | Cold Claude Code transcripts are copied into `archive/claude/` as `.zst`. The originals are not touched; Claude Code deletes them itself after `cleanupPeriodDays`. |
| `curate` | One small LLM call per project batch summarises the new sessions and maintains project memory: add, confirm, supersede (conflicting or updated), retire (no longer true). Memory not confirmed for 120 days becomes `stale`. |
| `reconcile` | Looks over a project's whole active memory for entries that contradict each other (the older one is retired, naming the newer one that replaced it) or repeat each other (merged into one). One LLM call per project, and only when that project's set of active memories changed since it was last checked; `--max-reconcile-calls` (default 5) bounds a run and `0` turns the step off. A call can never retire more than half of what it was shown, and a newer memory is never retired in favour of an older one. Nothing is deleted. |
| `embed` | Gives every active memory and session summary a vector for `auto search` (local model; skipped unless the `semantic` extra is installed). Only rows whose text changed are embedded again. |
| `export` | Renders memory and session summaries as Markdown under `memory/` (rewritten only when changed). |
| `index` | Optional `--index-cmd`, e.g. HKS `ks update {memory_dir}`. |

Every compressed or archived file is decompressed and compared by SHA-256 before the
original is moved or left. Memory is never deleted: entries change status and each change
is logged with its reason and evidence, so an automated decision can be undone
(`auto memory --all`, `auto memory --retire <id>`).

**Checking reconcile.** `uv run python evals/reconcile_cases.py` runs the real step against
seven hand-written projects with a known answer (an older decision replaced by a newer one,
the same thing said twice, entries that only share a topic, an entry that reads like an
instruction to the model) and reports what was found and what was changed wrongly. It makes
one model call per project and touches only a temporary store. One run with the default
command (`claude -p --model haiku`, 2026-11-14, 7 calls, $0.005): 4 of 4 contradictions
found with the right winner, 2 of 2 repeats merged, 27 of 29 other entries left alone. The
two that changed were a general rule and the later entry that adds its details, merged into
one. This is a single run; it was not repeated to measure variance. It was scored by the
status of each entry only; the script has since been made stricter (a retirement must name
the entry that replaced it, a merge must join exactly the expected entries and keep their
key words) and the run has not been repeated under the stricter scoring.

**Token use.** The model never sees transcripts or tool output, only digests (at most
3,000 characters per session). Sessions of one project share a call. The default command
runs `claude -p` with no tools, no MCP servers, no settings and a one-line system prompt,
which removes the fixed overhead of a normal headless run (measured: 630 instead of about
34,000 input tokens per call). `--max-llm-calls` bounds a run; the rest waits for the next
one. Scratch, benchmark and deleted directories are skipped, as are one-line exchanges.
Use `--llm-cmd` or `SESSION2MEMORY_AUTO_LLM_CMD` for another command; the prompt arrives
on stdin and the reply must contain one JSON object.

**Search.** `auto search` takes keywords or a whole question. With the optional extra
(`uv sync --extra semantic`) and after one `embed` step it ranks by meaning as well:
local embeddings pick the candidates (keyword hits are added so an exact name or id is
never missed), then the best 20 are re-read by a local cross-encoder (`--no-rerank` skips that, `--mode keyword` skips both). Models run on
this machine through `fastembed` (ONNX), are downloaded once into
`~/.cache/session2memory/models`, and can be changed with `SESSION2MEMORY_EMBED_MODEL` /
`SESSION2MEMORY_RERANK_MODEL`. No text leaves the machine and a query costs no tokens.
Without the extra, or before the first `embed`, search is by keywords. The reader and
workspace filters are applied before anything is compared, as in keyword search.

**Isolation.** Each session and memory has a scope: `openclaw` when it comes only from
OpenClaw agents (their own session stores, their Codex homes, or work inside an OpenClaw
workspace), `coding` otherwise. A `coding` reader sees everything. An `openclaw` reader
sees only `openclaw` rows, and a process is forced to that reader when it runs inside an
OpenClaw workspace, with an OpenClaw-owned `CODEX_HOME`, or with
`SESSION2MEMORY_READER=openclaw`, whatever `--reader` says. The SessionStart hook injects
the distilled memory of the workspace (`這個專案的記憶`) and stays silent for headless
sessions, which is how OpenClaw starts its agents.

