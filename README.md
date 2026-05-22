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
- Codex JSONL under `~/.codex/sessions`
- Qwen Code JSONL under `~/.qwen/projects`
- OpenCode SQLite under `~/.local/share/opencode/opencode.db`

## Generate Memory Docs

```bash
uv run session2memory import --date 2026-05-22 --output ./out/session-memory
```

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

## HKS Ingest

```bash
cd /Users/waynetu/claw_prog/projects/04-kurisu-github/hks
export KS_ROOT=/path/to/ks
export HKS_EMBEDDING_MODEL=simple
uv run ks ingest /path/to/out/session-memory
uv run ks update /path/to/out/session-memory
```

The HKS source root is the generated `session-memory` folder. Do not ingest
`~/.codex/sessions`, `~/.claude/projects`, `~/.qwen/projects`, or OpenCode
SQLite directly.

## Review And Promote

Durable project memory enters `memories/` only after review.

List candidates:

```bash
uv run session2memory review list \
  --date 2026-05-22 \
  --output ./out/session-memory
```

Inspect one candidate with its evidence preview:

```bash
uv run session2memory review inspect r000001 \
  --date 2026-05-22 \
  --output ./out/session-memory
```

Approve one candidate:

```bash
uv run session2memory review approve r000001 \
  --date 2026-05-22 \
  --output ./out/session-memory \
  --note "keep this"
```

Reject one candidate:

```bash
uv run session2memory review reject r000001 \
  --date 2026-05-22 \
  --output ./out/session-memory \
  --note "too local"
```

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

The next important review workflow is bulk approval/rejection and conflict
handling for repeated promotions.

## Verification

```bash
uv run pytest tests/integration/test_hks_compatibility.py -q
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```
