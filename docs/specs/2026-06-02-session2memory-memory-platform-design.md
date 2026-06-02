# session2memory Memory Platform Design

**Date:** 2026-06-02  
**Status:** Draft (awaiting review)  
**Scope:** Complete the transition from local memory compiler to multi-harness memory sync system.

## Problem

`session2memory` today compiles local coding-agent sessions into HKS-ingestable output
(`daily/`, `evidence/`, `review/`, optional `memories/` after promote). It is explicitly
**not** a complete shared memory system (see README).

Gaps:

1. **Sync-back** — adapters read harness stores; promoted memory must flow back into harness
   context (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, Codex/OpenClaw/Hermes memory).
2. **Incremental state** — date-based import rebuilds managed output; `evidence_id` drifts
   (`e000001…` from sort order).
3. **Extraction** — marker-only (`Decision:`, `Done:`, …) misses vibe-coding sessions.
4. **Review UX** — CLI-only review at scale is slow.
5. **Safety & lifecycle** — no systematic redaction, supersede, or revoke.
6. **Extensibility** — no documented adapter plugin surface.

## Goals

| Goal | Success criteria |
|------|------------------|
| Harness continuity | After promote + sync, next session in Codex/Cursor/Claude picks up promoted memory without manual copy-paste |
| Stable provenance | Re-import does not change `evidence_id` / `candidate_id` for unchanged source rows |
| Conservative memory | LLM candidates enter review only; nothing auto-writes `memories/` |
| Safe exports | Secrets and home paths redacted in all generated/synced text |
| Lifecycle | Revoked memories disappear from sync; supersede shows current truth |

## Non-goals

- Cloud/shared memory service or multi-machine sync
- Unattended auto-promote
- Web review UI in Phase 1 (TUI first)
- Ingesting raw session stores into HKS (unchanged policy)

## Source file preservation (sync-back)

Three layers — **do not conflate**:

| Layer | Examples | sync-back behavior |
|-------|----------|-------------------|
| Raw harness stores | `~/.codex/sessions`, `~/.cursor/chats` | **Never read or written** by sync (import reads only) |
| Generated compiler output | `out/session-memory/{daily,evidence,review,memories}` | sync **reads** `memories/<workspace_id>.md` only; import may replace managed dirs |
| Harness context files | `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc` | **Merge**: replace only `<!-- session2memory:sync-start -->` … `<!-- session2memory:sync-end -->`; preserve all other content |

`import` re-run with the same `--output` still replaces managed generated files (`daily/`,
`evidence/`, `review/`, `manifest.json`). That is independent of `sync`.

**Current implementation (v0.1.1):** `session2memory sync` implements marker merge for default
targets (`agents`, `claude`, `cursor`) plus optional `codex`, `openclaw`, `hermes`.
Phase 2 adds DB-backed sync deduplication.

## Implementation order (approved)

User preference: **B → A**

### Phase 1 (B) — Review experience

1. Optional **LLM extractor**
2. **Review TUI** (Textual)

### Phase 2 (A) — Platform foundation

1. **`session2memory.db`** (stable IDs, incremental import)
2. **Redaction** pipeline
3. **Supersede / revoke**
4. **Sync-back hardening** (content-hash skip, DB `sync_targets`)
5. **Plugin adapter SDK**

## Architecture overview

```text
  [Harness session stores]
           │ import (adapters)
           ▼
  ┌─────────────────────┐     Phase 2: canonical
  │  session2memory.db  │◄────────────────────┐
  └─────────┬───────────┘                     │
            │ export/render                   │ state updates
            ▼                                 │
  out/session-memory/                         │
    daily/ evidence/ review/ memories/        │
            │                                 │
            │ promote (review → memories)     │
            ▼                                 │
  session2memory sync ────────────────────────┘
            │
            ▼
  [Harness context files]
```

Phase 1 operates on file output (today’s model). Phase 2 makes the DB canonical; files
become render targets.

## Phase 1 design

### 1.1 LLM extractor

**CLI:** `session2memory import --llm-extract [--llm-backend <name>]`

**Input:** Non-noise `SessionMessage` rows after existing filtering (same pipeline as
marker extraction).

**Output:** Additional `MemoryCandidate` rows tagged `extraction=llm` with:

- `text` — proposed memory statement
- `evidence_quote` — short verbatim excerpt (redacted in Phase 2; plain in Phase 1)
- `confidence` — float 0–1 from backend JSON
- `durable_suggestion` — bool (hint only; reviewer decides)

**Flow:** Candidates merge into the same `review/YYYY-MM-DD.jsonl` queue as marker
candidates. **Never** append to `memories/` without approve + promote.

**Deduplication:** After marker extraction, drop LLM candidates when
`normalize_review_text(text)` matches an existing candidate or when line range overlaps
same session + kind.

**Backend interface:**

```python
class LlmExtractBackend(Protocol):
    def extract(self, *, messages: Sequence[SessionMessage], workspace_id: str) -> list[LlmCandidate]: ...
```

**Default backend:** subprocess via local agent runner (e.g. `acpx`), driven by a
versioned prompt template in `src/session2memory/prompts/llm_extract.txt`. No vendor API
required in core package.

**Failure modes:** Backend timeout or invalid JSON → log warning, continue import with
marker-only candidates (import exit 0 unless `--strict-llm`).

### 1.2 Review TUI

**CLI:** `session2memory review ui --date YYYY-MM-DD --output <dir>`

**Stack:** [Textual](https://textual.textualize.io/) (add as optional dependency group
`dev` / `ui` or required dependency — prefer `dependency-group ui` for `uv sync --group ui`).

**Screens:**

- **List** — id, kind, status, tool, confidence (if llm), first line of text
- **Detail** — full text, evidence preview (reuse `inspect_review` logic), approve/reject
- **Promote** — invoke existing `promote_reviews` with conflict summary modal

**Constraints:** All mutations call existing `review.py` functions (no duplicate state
logic). CLI subcommands remain for scripting.

### 1.3 Sync-back (existing + Phase 1 freeze)

No Phase 1 code changes required beyond documentation. Behavior frozen as:

- Default targets: `agents`, `claude`, `cursor`
- Marker block idempotent merge
- `codex` → same file as `agents`; `openclaw` / `hermes` → `~/.openclaw/memory`,
  `~/.hermes/memory`

## Phase 2 design

### 2.1 `session2memory.db`

**Location:** `<output>/session2memory.db` unless `--state-db <path>`.

**Schema (initial):**

| Table | Key columns |
|-------|-------------|
| `source_files` | `id`, `tool`, `path`, `digest`, `mtime_ns`, `last_imported_at` |
| `messages` | `id`, `source_file_id`, `line_start`, `line_end`, `message_hash`, `role` |
| `candidates` | `id`, `workspace_id`, `kind`, `text_normalized`, `extraction`, `message_id` |
| `evidence` | `id` (stable hash), `candidate_id`, `tool`, `session_id`, pointers |
| `review_state` | `candidate_id`, `status`, `note`, `confidence`, `durable_flag` |
| `memory_entries` | `id`, `workspace_id`, `kind`, `text`, `evidence_id`, `review_ref`, `status` |
| `sync_targets` | `workspace_id`, `target`, `dest_path`, `content_hash`, `last_synced_at` |

**Stable IDs:**

- `evidence_id = "e_" + sha256(tool|session|lines|digest)[:12]`
- `candidate_id = "c_" + sha256(workspace|kind|normalized_text|message_hash)[:12]`

**Import:** Upsert by digest; skip unchanged `source_files`. Re-export JSONL/MD from DB.

**Migration:** On first run against legacy output without DB:

1. Create DB schema
2. Backfill from `evidence/index.jsonl` and `review/*.jsonl`
3. Map old `e00000n` → new stable ids where possible; emit `migration_report.json` for
   unresolved rows

### 2.2 Redaction

**Module:** `session2memory/redaction.py`

**Rules (ordered):**

1. User home prefix → `[REDACTED:home]`
2. Absolute paths `/…` and `C:\…` → `[REDACTED:path]`
3. Token patterns (`sk-…`, `ghp_…`, `AKIA…`, etc.)
4. `.env` assignment lines in excerpts

**Apply at:** writer (daily/review/evidence excerpts), LLM extractor output, sync-back
body, TUI previews.

**Raw stores:** never modified.

### 2.3 Supersede / revoke

**CLI:**

- `session2memory memory revoke --id <memory_entry_id>`
- `session2memory memory supersede --old <id> --new <id>`

**Semantics:**

- `revoked` — excluded from `memories/` export and sync-back render
- `superseded` — old entry hidden; new entry carries `supersedes=<old_id>` metadata

Promote path assigns `memory_entry_id` at promotion time (DB row + optional frontmatter in
`memories/*.md`).

### 2.4 Sync-back hardening

- Before write: compare SHA-256 of rendered marker body to `sync_targets.content_hash`
- Flags: `--force` (ignore hash), `--since-last-sync` (skip unchanged targets)
- Record successful writes in `sync_targets`

### 2.5 Plugin adapter SDK

**API:**

- `PipelineAdapter` protocol (existing)
- `register_adapter(tool: str, factory: Callable[[Path], PipelineAdapter])`
- Entry point group: `session2memory.adapters`

**Docs:** `docs/adapters.md` — session discovery contract, `iter_sessions`, test fixture
pattern, registration example.

**Policy:** P0 adapters stay in-tree; experimental formats use plugins.

## Testing strategy

| Phase | Tests |
|-------|-------|
| 1 | Mock LLM backend returns fixed JSON; dedup with marker candidates; TUI keybindings smoke (Textual pilot); sync golden unchanged |
| 2 | DB re-import stable ids; migration fixture from legacy `e000001`; redaction snapshots; revoke → sync excludes entry; entry point adapter registration |

Run before merge: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy src/session2memory`.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Phase 1 file IDs diverge from Phase 2 DB IDs | Migration script + `migration_report.json`; document one-time re-promote if needed |
| LLM hallucinated memory | Review-only; confidence displayed; default `durable_suggestion=false` |
| Sync overwrites hand-edited marker block | Markers documented; human edits outside markers preserved |
| Textual not installed | Optional `ui` dependency group with clear error message |

## Open questions (deferred)

- Exact `acpx`/backend default for LLM extract on Wayne’s machine (config file in Phase 1.1)
- Whether HKS ingest should read DB export or continue file-only (file-only for Phase 2)

## References

- README — local memory compiler scope
- `src/session2memory/sync_back.py` — current sync implementation
- `docs/specs/2026-05-31-session2memory-skill-hks-alignment-design.md` — HKS skill alignment (docs only)
