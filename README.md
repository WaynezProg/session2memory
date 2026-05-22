# session2memory

`session2memory` turns messy local AI coding sessions into clean, evidence-backed, HKS-ingestable project memory without importing raw transcripts.

It is not a complete shared memory system. It is a local memory compiler: it reads coding-agent session stores, extracts conservative project memory, and writes a refined source folder for HKS.

Raw transcripts do not enter HKS. Raw session stores remain local evidence sources; generated Markdown contains only daily logs, durable memories, and evidence ids that point back to the original session.

## Supported P0 Sources

- Claude Code JSONL under `~/.claude/projects`
- Codex JSONL under `~/.codex/sessions`
- Qwen Code JSONL under `~/.qwen/projects`
- OpenCode SQLite under `~/.local/share/opencode/opencode.db`

## Generate Memory Docs

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
```

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

## HKS Ingest

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/hks
export KS_ROOT=/path/to/ks
export HKS_EMBEDDING_MODEL=simple
uv run ks ingest /path/to/out/session-memory
uv run ks update /path/to/out/session-memory
```

The HKS source root is the generated `session-memory` folder. Do not ingest `~/.codex/sessions`, `~/.claude/projects`, `~/.qwen/projects`, or OpenCode SQLite directly.

## Current Extraction Model

P0 extraction is marker-based and conservative. It recognizes explicit session lines such as `Decision:`, `Done:`, `Pitfall:`, `Constraint:`, and `Verification:`. This avoids invented memory, but it can miss important context that was never marked.

Durable memories are kept separate from daily logs. `Decision`, `Pitfall`, and `Constraint` entries can enter `memories/`; `Done` and `Verification` entries stay in `daily/` plus `evidence/`.

## Roadmap

The next important workflow is review and promote: daily memory should land as a temporary review queue first, and durable memory should enter `memories/` only after human or agent review.

## Verification

```bash
uv run pytest tests/integration/test_hks_compatibility.py -q
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```
