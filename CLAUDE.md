# CLAUDE.md

Guidance for Claude Code when working in this repository. Shared contributor
rules also live in `AGENTS.md`; keep both in sync when changing workflow or
verification commands.

## What this repo is

`session2memory` compiles local coding-agent sessions into HKS-ingestable memory
output. It is a local memory compiler, not a shared memory service. Do not ingest
raw session stores into HKS; generate `out/session-memory` (or your chosen
`--output` tree) first, then ingest generated Markdown from the HKS repo.

## Project structure

Source lives under `src/session2memory/` (`cli.py` entrypoint, adapters in
`adapters/`, shared contracts in `models.py`, `pipeline.py`, `writer.py`).
Tests are in `tests/`; fixtures in `tests/fixtures/`; golden output in
`tests/golden/`; HKS contracts in `tests/integration/`. Packaged skill:
`skills/session2memory/`. Generated local output belongs in `out/` — not source.

## Common commands

```bash
uv run session2memory discover
uv run session2memory import --date 2026-05-22 --workspace "$PWD" --output ./out/session-memory --dry-run
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
uv run session2memory worklog last-week --output ./out/session-memory
uv run session2memory sync --workspace "$PWD" --output ./out/session-memory
uv run pytest -q
uv run pytest tests/integration/test_hks_compatibility.py -q
uv run ruff check .
uv run mypy src/session2memory
```

Before handoff: `uv run pytest -q`, `uv run ruff check .`, and
`uv run mypy src/session2memory`.

## CodeGraph

This repo maintains a CodeGraph index (`.codegraph/`, gitignored). When the
`codegraph_*` MCP tools are available, prefer them for structural questions
(definitions, callers, impact, traces). Cursor loads the full guide from
`.cursor/rules/codegraph.mdc`. Use grep only for literal strings, logs, and
config values.

## Coding style

Python 3.12, strict mypy, Ruff (`B`, `E`, `F`, `I`, `UP`), 100-character lines.
snake_case for modules/functions; PascalCase for classes. Keep adapter code
source-specific; avoid cross-adapter conditionals unless behavior is truly shared.

## Session memory sync

`session2memory sync` merges promoted memory into harness context files using
`<!-- session2memory:sync-start -->` … `<!-- session2memory:sync-end -->` blocks
only. Default targets: `AGENTS.md`, `CLAUDE.md`, and
`.cursor/rules/session2memory-memory.mdc`. Preserve all content outside those
markers.

## Provenance and safety

Raw paths belong in `evidence/index.jsonl`. Daily and review output expose
compact evidence identifiers only. Claude Code provider metadata may be recorded
under `~/.local/share/session2memory/`; import reads it for Claude evidence rows
but does not infer provider/model from transcript text.
