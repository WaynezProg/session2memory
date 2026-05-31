# session2memory Skill ↔ HKS Agent Profile Alignment

**Date:** 2026-05-31  
**Status:** Approved (brainstorming)  
**Note:** `docs/superpowers/` is gitignored in this repo; this spec is tracked under `docs/specs/`.  
**Scope:** Documentation and agent-facing skill contract only — no changes to `src/session2memory/` or scheduled ingest scripts.

## Problem

The packaged skill (`skills/session2memory/SKILL.md`) still documents:

- Date-scoped local output (`./out/session-memory/$date`) as the primary contract.
- Direct `uv run ks ingest` against a hard-coded path containing the typo `seesion2memory`.
- Operator-style `KS_ROOT` + `ks query` flows as the default agent path.

HKS agent profile ingest expects:

- Export layout: `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/` with `daily/YYYY-MM-DD.md` inside.
- MCP tool `hks_workspace_ingest_session_memory(workspace_id, path="daily/YYYY-MM-DD.md")`.
- No ingestion of raw session stores (`~/.codex`, `~/.claude`, `~/.cursor`, transcripts).

Agents following the current skill can bypass review boundaries and misconfigure HKS.

## Goals

1. Align skill, `skill.json`, `agents/openai.yaml`, and README with the HKS agent profile contract.
2. Fix the `seesion2memory` typo and remove hard-coded user paths from agent examples.
3. Document `workspace_id` resolution (session2memory id default; git basename when export folder uses basename).
4. Keep operator batch ingest (`scripts/daily-session-memory-to-hks.sh`, launchd) on `ks ingest` — documented as a separate path.

## Non-Goals

- Changing session2memory CLI behavior or output writer layout.
- Migrating launchd / shell scripts to MCP ingest.
- Updating `tests/integration/test_hks_compatibility.py` README golden strings (unless explicitly needed later).

## Design

### Document structure (SKILL.md)

Reorganize into clear sections:

1. **Scope** — supported tools, review required, no raw ingest.
2. **Import** — `--output "$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>"`; optional local `./out/session-memory` for dev only.
3. **Review** — unchanged commands; output paths relative to workspace export root.
4. **HKS ingest (agents)** — env vars + `hks_workspace_ingest_session_memory` + `hks_workspace_query` (`writeback=no`).
5. **Security** — explicit deny list for ingest sources.
6. **Operator batch ingest** — pointer to `scripts/daily-session-memory-to-hks.sh` and `ks ingest` for scheduled runs.
7. **Expected noise / sanity checks** — retain where still accurate; remove `seesion2memory` paths.

### Output contract

| Item | Value |
|------|--------|
| Export root env | `HKS_SESSION2MEMORY_EXPORT_ROOT` |
| Per-workspace tree | `{export_root}/{workspace_id}/daily/`, `evidence/`, `review/`, `manifest.json` |
| Import flag | `--output "$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>"` |

**workspace_id (dual convention):**

- **Default:** session2memory-generated id (e.g. `repo-123`) from `manifest.json` or daily `###` headings.
- **Alternate:** git repository directory basename only when the export directory is intentionally named with that basename and matches HKS workspace registration.

**Multi-workspace same day:** Document using `--workspace /absolute/path` so a single import run targets one project and one `workspace_id` folder. Do not document dumping all tools into one ambiguous export folder for agent ingest.

### HKS ingest (agent profile)

Agents with HKS MCP (`hks-mcp --profile agent`) should:

```text
export HKS_SESSION2MEMORY_EXPORT_ROOT="<user-chosen-export-root>"
export HKS_KS_ROOT_BASE="<per HKS docs>"
export HKS_WORKSPACE_REGISTRY="<per HKS docs>"   # if required by setup
export HKS_EMBEDDING_MODEL=simple                # or configured model

hks_workspace_ingest_session_memory(
  workspace_id="<workspace_id>",
  path="daily/YYYY-MM-DD.md",
)

hks_workspace_query(
  workspace_id="<workspace_id>",
  query="...",
  writeback=no,
)
```

Do not document `uv run ks ingest` on session store paths as the agent default.

### Security rules (mandatory section)

Agents must **not**:

- Run `ks ingest`, `ks update`, `hks_ingest`, or equivalent on `~/.codex`, `~/.claude`, `~/.cursor`, OpenCode SQLite, OpenClaw/Hermes raw logs, or unreviewed transcripts.
- Point HKS at `session2memory` repo `./out/` unless that tree is the registered export root for a specific `workspace_id`.

Agents **must**:

- Import via `session2memory` CLI into `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>/`.
- Complete review/promote workflow before treating content as durable memory.
- Ingest only generated Markdown under the workspace export tree via `hks_workspace_ingest_session_memory`.

### skill.json

- Update `commands.hks_ingest` / `hks_update` to describe MCP workspace tools (not `uv run ks ingest <repo>/out/...`).
- Update `outputs.generated_source` to `$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>`.
- Keep `hks_policy.do_not_ingest_raw_session_stores: true`.

### agents/openai.yaml

- Revise `default_prompt` to mention export root + agent profile ingest, not isolated `KS_ROOT` + `ks ingest`.

### README.md

- Replace HKS section to match SKILL agent contract.
- Add short **Operator / scheduled ingest** subsection referencing `scripts/daily-session-memory-to-hks.sh` and whole-tree `ks ingest` for batch jobs.

### Tests

`tests/test_skill.py`:

- Assert `HKS_SESSION2MEMORY_EXPORT_ROOT` and `hks_workspace_ingest_session_memory` in SKILL text.
- Assert security deny language (raw session stores).
- Remove requirement for `uv run ks ingest` / `uv run ks update` in SKILL.md.
- Optionally assert `Operator` or `scripts/daily-session-memory` if documented in SKILL.

## Files to change

| File | Action |
|------|--------|
| `skills/session2memory/SKILL.md` | Rewrite per sections above |
| `skills/session2memory/skill.json` | MCP-oriented commands + outputs |
| `skills/session2memory/agents/openai.yaml` | default_prompt |
| `README.md` | HKS + operator subsection |
| `tests/test_skill.py` | Assertions |
| `docs/superpowers/specs/2026-05-31-session2memory-skill-hks-alignment-design.md` | This spec |

## Verification

```bash
uv run pytest tests/test_skill.py -q
uv run ruff check tests/test_skill.py
```

Manual: read SKILL.md — no `seesion2memory`, no `ks ingest ~/.codex`, agent path uses export root + MCP tool names.

## References

- HKS: `skill/hks-knowledge-system/workflows/ingest-query.md` (agent profile section)
- HKS: `src/hks/adapters/core.py` — `hks_workspace_ingest_session_memory`
- HKS: `docs/configuration.md` — `HKS_SESSION2MEMORY_EXPORT_ROOT`
