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

`worklog` always reads state from `session2memory.db`. By default it looks for
`<output>/session2memory.db`. If your import tree stores the database in a date
subfolder (for example `./out/session-memory/2026-06-08/session2memory.db`), pass
`--state-db` explicitly. Do not move or copy the database just to satisfy the default
lookup path.

```bash
uv run session2memory worklog yesterday --output ./out/session-memory
uv run session2memory worklog last-week --output ./out/session-memory
uv run session2memory worklog last-month --output ./out/session-memory
uv run session2memory worklog --from 2026-06-01 --to 2026-06-07 --output ./out/session-memory
```

When the database is not at the `--output` root:

```bash
uv run session2memory worklog yesterday \
  --output ./out/session-memory \
  --state-db ./out/session-memory/2026-06-08/session2memory.db
```

- Period aliases (`yesterday`, `last-week`, `last-month`) only affect the resolved date
  range; output files are always named by ISO dates, for example `worklogs/2026-06-08.md`
  or `worklogs/2026-06-01_2026-06-07.md`.
- Run `import` first so `session2memory.db` exists. If lookup fails, the CLI prints the
  path it checked and a suggested `--state-db` command when nearby databases are found.
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
