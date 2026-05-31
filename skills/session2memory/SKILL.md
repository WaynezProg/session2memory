---
name: session2memory
description: Use when importing local AI coding sessions into session2memory, reviewing generated candidates, or ingesting session memory into HKS for agent recall.
---

# session2memory

Use this skill to compile local coding-agent sessions into evidence-backed,
HKS-ingestable memory without importing raw transcripts.

## Scope

- Supported session stores: Codex, Claude Code, Qwen Code, OpenCode, Cursor GUI,
  Cursor CLI, OpenClaw logs, Hermes logs.
- Optional agentic-os evidence index (`--agentic-os-root`, `--no-agentic-os`).
- Output is a generated source folder, not HKS itself.
- Do not ingest raw session stores into HKS.
- Keep HKS session memory in a separate `KS_ROOT` unless the user explicitly wants it mixed with another knowledge base.

## Import

Run from the `session2memory` repo.

For one day:

```bash
date=2026-05-22
uv run session2memory import --date "$date" --output "./out/session-memory/$date"
```

For several known dates:

```bash
for date in 2026-05-20 2026-05-21 2026-05-22; do
  uv run session2memory import --date "$date" --output "./out/session-memory/$date"
done
```

Use `--tool codex`, `--tool claude`, `--tool qwen`, `--tool opencode`,
`--tool cursor`, `--tool cursor-cli`, `--tool openclaw`, or `--tool hermes`
to limit the source. Use `--workspace /absolute/path` to limit one project.

Check local supported stores:

```bash
uv run session2memory discover
```

## Review

Review is per date-scoped output folder:

```bash
date=2026-05-22
output="./out/session-memory/$date"

uv run session2memory review list --date "$date" --output "$output"
uv run session2memory review inspect r000001 --date "$date" --output "$output"
uv run session2memory review approve r000001 --date "$date" --output "$output" --note "keep"
uv run session2memory review promote --date "$date" --output "$output"
uv run session2memory review approve-bulk --date "$date" --output "$output"
uv run session2memory review conflicts --date "$date" --output "$output"
```

Use `review reject` / `review reject-bulk` for local, noisy, or misleading candidates.
Use `review promote --resolve keep-new` when `review conflicts` reports duplicates. Use
`review approve --durable` only when a daily-only candidate should become
durable memory.

## HKS Ingest

Run from the HKS repo and isolate this knowledge base:

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/hks
export KS_ROOT="$PWD/.hks-runs/session-memory/ks"

uv run ks ingest /Users/waynetu/claw_prog/projects/04-kurisu-github/seesion2memory/out/session-memory
uv run ks update /Users/waynetu/claw_prog/projects/04-kurisu-github/seesion2memory/out/session-memory
uv run ks query "今天做了哪些驗證？"
```

`ks ingest` creates HKS documents for Markdown daily logs. `ks update`
synchronizes later changes against the same source root. `ks query` reads from
the selected `KS_ROOT`.

## Expected Noise

- `evidence/index.jsonl`, `review/*.jsonl`, and `manifest.json` may show as
  `unsupported` in HKS. That is expected; HKS ingests the Markdown daily logs.
- `missing` in `ks update` means old manifest entries are absent from the
  current source root. Do not add `--prune` unless the user explicitly wants
  removal.
- If `mise` refuses the HKS config, run:

```bash
mise trust /Users/waynetu/claw_prog/projects/04-kurisu-github/hks/.mise.toml
```

## Sanity Checks

```bash
find ./out/session-memory -maxdepth 3 -type f | sort
jq '.counts, .source_roots' ./out/session-memory/2026-05-22/manifest.json
git -C /Users/waynetu/claw_prog/projects/04-kurisu-github/hks status --short
```

Do not commit generated `out/` data.
