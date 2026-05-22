# session2memory

`session2memory` converts local coding-agent sessions into refined HKS-ingestable memory documents.

It does not put raw transcripts into HKS. Raw session stores remain local evidence sources; generated Markdown contains only conservative summaries and evidence ids.

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
export HKS_EMBEDDING_MODEL=simple
uv run ks ingest /path/to/out/session-memory
uv run ks update /path/to/out/session-memory
```

The HKS source root is the generated `session-memory` folder. Do not ingest `~/.codex/sessions`, `~/.claude/projects`, `~/.qwen/projects`, or OpenCode SQLite directly.

## Verification

```bash
uv run pytest tests/integration/test_hks_compatibility.py -q
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```
