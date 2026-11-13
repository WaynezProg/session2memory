---
name: session2memory
description: Use when importing local AI coding sessions into session2memory, reviewing generated candidates, or ingesting session memory into HKS for agent recall.
---

# session2memory

Use this skill to compile local coding-agent sessions into evidence-backed,
HKS-ingestable memory without importing raw transcripts.

## Scope

- Supported session stores: Codex, Claude Code, Claude Desktop local-agent
  transcripts and metadata, Qwen Code, OpenCode, Cursor GUI, Cursor CLI,
  OpenClaw logs, Hermes logs.
- Claude Desktop support does not parse the GUI chat cache in Chromium
  IndexedDB/Local Storage, and does not ingest local audit logs as transcripts.
- Optional agentic-os evidence index (`--agentic-os-root`, `--no-agentic-os`).
- Output is a generated export tree under `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/`, not HKS itself.
- Human review is required before treating candidates as durable memory.
- Agents use HKS MCP agent profile tools; operators may use batch `ks ingest` scripts.

## Import

Run from the `session2memory` repo. Set the export root once per machine:

```bash
export HKS_SESSION2MEMORY_EXPORT_ROOT="${HKS_SESSION2MEMORY_EXPORT_ROOT:-$HOME/session2memory/export}"
mkdir -p "$HKS_SESSION2MEMORY_EXPORT_ROOT"
```

Resolve `workspace_id` before import:

- **Default:** session2memory-generated id (for example `repo-123`) from
  `manifest.json` or daily `###` headings after a dry-run or prior import.
- **Alternate:** git repository directory basename only when the export directory
  is intentionally named with that basename and registered the same way in HKS.

For one project and one day (recommended for agent ingest):

```bash
date=2026-05-22
workspace_id=repo-123   # from manifest or prior import
project=/absolute/path/to/repo

uv run session2memory import \
  --date "$date" \
  --workspace "$project" \
  --output "$HKS_SESSION2MEMORY_EXPORT_ROOT/$workspace_id"
```

For several dates into the same workspace export tree, re-run import per date
(same `--output`; managed files for that date are replaced):

```bash
workspace_id=repo-123
project=/absolute/path/to/repo
for date in 2026-05-20 2026-05-21 2026-05-22; do
  uv run session2memory import \
    --date "$date" \
    --workspace "$project" \
    --output "$HKS_SESSION2MEMORY_EXPORT_ROOT/$workspace_id"
done
```

Use `--tool codex`, `--tool claude`, `--tool claude-desktop`, `--tool qwen`,
`--tool opencode`, `--tool cursor`, `--tool cursor-cli`, `--tool openclaw`,
or `--tool hermes` to limit the source.

Local dev may use `./out/session-memory` as a scratch export root, but agent
docs and HKS ingest assume `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/`.

Check local supported stores:

```bash
uv run session2memory discover
```

## Review

Review uses the workspace export folder:

```bash
date=2026-05-22
output="$HKS_SESSION2MEMORY_EXPORT_ROOT/$workspace_id"

uv run session2memory review list --date "$date" --output "$output"
uv run session2memory review inspect r000001 --date "$date" --output "$output"
uv run session2memory review approve r000001 --date "$date" --output "$output" --note "keep"
uv run session2memory review promote --date "$date" --output "$output"
uv run session2memory review approve-bulk --date "$date" --output "$output"
uv run session2memory review conflicts --date "$date" --output "$output"
uv run session2memory review web --date "$date" --output "$output" --host 127.0.0.1 --port 8765
```

Use `review reject` / `review reject-bulk` for local, noisy, or misleading candidates.
Use `review promote --resolve keep-new` when `review conflicts` reports duplicates. Use
`review approve --durable` only when a daily-only candidate should become
durable memory.

Optional LLM extraction stays review-only. Configure it with `SESSION2MEMORY_LLM_CMD`
or pass `--llm-cmd`; use `--llm-input stdin` for commands that read prompts from
stdin and `--strict-llm` when extraction failure should fail the import.

Lifecycle commands re-export active `memories/*.md` rows and resync previously
recorded sync targets from `sync_targets`:

```bash
uv run session2memory memory revoke m_0123456789ab --output "$output"
uv run session2memory memory supersede --old m_old --new m_new --output "$output"
```

## Security

Agents must **not**:

- Run `ks ingest`, `ks update`, `hks_ingest`, or `hks_workspace_ingest_session_memory`
  on raw session stores: `~/.codex`, `~/.claude`,
  `~/Library/Application Support/Claude`, `~/.cursor`, `~/.qwen`, OpenCode
  SQLite, OpenClaw/Hermes raw logs, or unreviewed transcripts.
- Treat the session2memory repository `./out/` as an ingest target unless that
  path is the registered `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/` tree.

Agents **must**:

- Import with `session2memory` into `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/`.
- Complete review (and promote when needed) before durable memory is trusted.
- Ingest only generated Markdown via `hks_workspace_ingest_session_memory`.

## HKS Ingest (agents)

Use HKS MCP with the agent profile (`hks-mcp --profile agent`). Configure export
and workspace runtime per HKS docs (`HKS_KS_ROOT_BASE`, `HKS_WORKSPACE_REGISTRY`,
`HKS_EMBEDDING_MODEL`).

```bash
export HKS_SESSION2MEMORY_EXPORT_ROOT="${HKS_SESSION2MEMORY_EXPORT_ROOT:-$HOME/session2memory/export}"
# Plus HKS_KS_ROOT_BASE, HKS_WORKSPACE_REGISTRY, HKS_EMBEDDING_MODEL per HKS setup
```

After import and review, ingest the daily log for that workspace:

```text
hks_workspace_ingest_session_memory(
  workspace_id="<workspace_id>",
  path="daily/YYYY-MM-DD.md",
)
```

Query with writeback disabled unless the user explicitly wants wiki updates:

```text
hks_workspace_query(
  workspace_id="<workspace_id>",
  question="今天做了哪些驗證？",
  writeback=no,
)
```

`path` is relative to `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/`.
Do not use direct `ks ingest` on session store paths as the agent default.

## Operator batch ingest

Scheduled or human batch jobs may use `scripts/daily-session-memory-to-hks.sh`,
which runs `ks ingest` / `ks update` against a combined local output tree. That
path is for operators, not autonomous agents. See the script and README for
`KS_ROOT` and embedding settings.

## Expected Noise

- `evidence/index.jsonl`, `review/*.jsonl`, and `manifest.json` may show as
  `unsupported` in HKS. That is expected; agent ingest targets Markdown daily logs.
- Operator `ks update` may report `missing` when old manifest entries are absent.
  Do not add `--prune` unless the user explicitly wants removal.

## Sanity Checks

```bash
export_root="${HKS_SESSION2MEMORY_EXPORT_ROOT:-$HOME/session2memory/export}"
find "$export_root" -maxdepth 3 -type f | sort
jq '.counts, .source_roots' "$export_root/$workspace_id/manifest.json"
test -f "$export_root/$workspace_id/daily/2026-05-22.md"
```

Do not commit generated export data.

## Unattended maintenance (`auto`)

The nightly job runs this; run it by hand only to check or catch up:

```bash
uv run session2memory auto status                 # counts and the last run's report
uv run session2memory auto run --dry-run          # what a run would do; changes nothing
uv run session2memory auto run                    # scan, compress/archive, curate, export
```

- It keeps one persistent store in `~/.local/share/session2memory/store`; it does not
  use the per-date `./out/session-memory/<date>` trees of `import`, and both can coexist.
- Session compression, archival and memory curation are automatic. Do not compress,
  move or delete session files by hand.
- Curated memory is never deleted, only re-statused. To undo a wrong entry:
  `uv run session2memory auto memory --workspace <path> --all`, then
  `--retire <memory id>`.

Reading memory:

```bash
uv run session2memory auto search "<keywords>" [--workspace <path>]
uv run session2memory auto memory --workspace <path>
```

**Isolation.** An OpenClaw agent only reads memory and summaries that came from OpenClaw
agents' own sessions. This is enforced by where the command runs (an OpenClaw workspace,
an OpenClaw-owned `CODEX_HOME`, or `SESSION2MEMORY_READER=openclaw`); `--reader coding`
does not widen it. Coding agents outside OpenClaw read everything. Running the pipeline
(`auto run`, `import`) is not restricted, only reading its results is.

