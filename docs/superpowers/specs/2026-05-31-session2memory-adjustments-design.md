# session2memory Adjustments Design

**Date:** 2026-05-31  
**Status:** Approved for implementation planning (pending user spec review)

## Goals

Extend `session2memory` so it remains the **sole compiler gate** for HKS-ingestable project durable memory while:

1. Expanding **P0 harness importers** (docs alignment + `openclaw` / `hermes` adapters).
2. Adding a **read-only agentic-os evidence index** (sessions + audit pointers; not a substitute for harness logs).
3. Strengthening **review / promote** with bulk operations, conflict resolution, and stronger duplicate prevention.

Raw transcripts and agentic-os operational memory must not bypass this pipeline into HKS.

## Constraints

| Rule | Detail |
|------|--------|
| **HKS / project durable** | Only `session2memory review promote` writes `out/.../memories/*.md`, then `ks ingest` reads that folder. |
| **No raw → HKS** | Harness stores and agentic-os DB are evidence sources only. |
| **Dual gate (operational)** | **agentic-os** may approve/search memory in its own `MemoryStore` for runtime/fleet use. That does **not** imply HKS durable. |
| **No dual-write** | agentic-os approve must not write `memories/` or trigger HKS ingest. |
| **Optional seed** | Future: agentic-os-approved items may seed `review/` rows; still require session2memory approve + promote for HKS. Not in initial importer PR scope. |
| **Conservative extraction** | Marker-based extraction unchanged; no invented memory from unstructured logs. |

## Architecture Overview

```
Harness logs (P0 adapters) ──► SessionRecord ──► extract ──► review (pending)
        ▲                              │
        │                              ▼
AgenticOsIndex (read-only DB) ── enrich evidence/index.jsonl

review approve | approve-bulk | reject-bulk
review conflicts (inspect)
review promote [--resolve] ──► memories/*.md ──► HKS ks ingest

agentic-os memory approve ──► MemoryStore only (parallel, not synced to memories/)
```

---

## §1 Gate & Data Flow

### Responsibility split

| Layer | Owns | Consumers |
|-------|------|-----------|
| **agentic-os** | `~/.agentic-os/agentic-os.db` sessions, audit, `MemoryStore`, `memory review/approve` API | agentic-os CLI/API, attach, search |
| **session2memory** | `daily/`, `evidence/`, `review/`, `memories/`, `manifest.json` | Human review, `ks ingest` |

### Prohibited paths

- Raw session stores → HKS.
- agentic-os `memory approve` → direct `ks ingest` or write to `out/session-memory/memories/`.

### Sync rules

- agentic-os approve status is **not** a promote prerequisite.
- HKS path always: import → review → promote.
- On conflict between stores for HKS content, **session2memory `memories/` + promotion_key** wins.

---

## §2 Importer

### P0 harness tools

**After change**, `P0_TOOLS` and docs/skill list:

| Tool | Default `source_root` | Notes |
|------|----------------------|--------|
| `codex` | `~/.codex/sessions` | existing |
| `claude` | `~/.claude/projects` | existing |
| `qwen` | `~/.qwen` | existing |
| `opencode` | `~/.local/share/opencode/opencode.db` | existing |
| `cursor` | `~/.cursor/chats` | in code today; README/SKILL must catch up |
| `cursor-cli` | `~/.cursor/projects` | in code today; README/SKILL must catch up |
| `openclaw` | `~/.openclaw/logs` | per agentic-os adapter contract v2 |
| `hermes` | `~/.hermes/logs` | per agentic-os adapter contract v2 |

`agentic-os` is **not** a `--tool` harness name.

### CLI

Existing:

- `--tool`, `--source-root tool=path`, `--date`, `--workspace`, `--dry-run`, `discover`

New:

| Flag | Default | Purpose |
|------|---------|---------|
| `--agentic-os-root` | `~/.agentic-os` | Locate `agentic-os.db` for evidence index |
| `--no-agentic-os` | off | Disable evidence enrichment (harness-only import) |
| `--agentic-os-sessions-only` | off | Import only sessions registered in agentic-os for the date that resolve to a harness log |

`discover` adds: `agentic-os evidence=found|missing db=<path>`.

### openclaw / hermes adapters (v1)

1. Scan `source_root` recursively; include files whose mtime falls on `--date`.
2. **Session identity v1:** one log file = one session unless a documented on-disk convention exists (see Open Questions).
3. Map lines to `SessionMessage` + `EvidencePointer` (`tool=openclaw|hermes`, line ranges).
4. Reuse marker extraction (`Decision:`, `Pitfall:`, etc.).
5. Populate `adapter.skipped` for unreadable files; do not abort whole import.

Primary evidence text always comes from harness log files, not agentic-os DB blobs.

### AgenticOsIndex (read-only)

- SQLite: sessions + `audit_events` filtered by import date.
- Lookup: link `(harness_id, upstream_session_id?)` → `agentic_os_session_id`, audit id list.
- **Enrich** `evidence/index.jsonl` optional fields: `agentic_os_session_id`, `agentic_os_audit_ids[]`; `source_path` still points at harness log.
- Do **not** read agentic-os `MemoryStore` text into candidates.
- Missing or corrupt DB: warn and continue harness-only import.

### Pipeline composition

```
Harness adapters → SessionRecord stream
AgenticOsIndex   → enrich at evidence write (and optional session filter)
                 → extract_candidates → writer (daily, evidence, review)
```

---

## §3 Promote / Review

### State machine

`pending → approved | rejected → promoted`

Only rows with `status=approved` and `durable_suggestion=true` are promote-eligible.

### Bulk commands

```
session2memory review approve-bulk --date D --output O [filters]
session2memory review reject-bulk  --date D --output O [filters]
```

| Filter / flag | Behavior |
|---------------|----------|
| `--status` | Default `pending` for bulk |
| `--kind`, `--workspace-id`, `--tool` | AND filters |
| `--id` (repeatable) | Explicit review ids (AND with filters) |
| `--all-pending` | Explicit “all pending” semantics |
| `--durable` | approve-bulk sets `durable_suggestion=true` |
| `--note` | Stored on `review_note` |
| `--dry-run` | Print matched ids/counts; no write |

Output: `matched=N updated=M skipped=K` (optional `--json`).

**Idempotency:** Re-approving already-approved rows counts as `skipped`, exit 0.

**v1 rule:** Do not bulk-change rows already `promoted` (avoid `memories/` drift).

### Conflicts

**Definition:** same `workspace_id` + `kind` + `normalize(text)` (collapse whitespace, trim; case-sensitive v1) + **different** `evidence_id`.

Commands:

```
session2memory review conflicts --date D --output O
session2memory review promote --date D --output O [--resolve keep-new|keep-old|skip]
```

| `--resolve` | Behavior |
|-------------|----------|
| (omitted) | If unresolved conflicts exist → exit code **2**, print groups, no promote |
| `keep-new` | Per group, promote newest row (later date, then higher review id) |
| `keep-old` | Prefer row already reflected in `memories/`; else older id |
| `skip` | Skip entire conflict group; promote non-conflicting approved rows |

Bulk approve only changes review JSONL; conflicts are resolved at **promote** time.

### Duplicate promote prevention

1. **Exact (existing):** `_promotion_key = hash(workspace_id, kind, text, evidence_id)` embedded as `review=<date>/<key>` in `memories/`; repromote → `created=false`, `promoted=0`.
2. **Semantic (new):** `hash(workspace_id, kind, normalize(text))` without evidence_id; if `memories/<workspace>.md` already contains equivalent body → mark `promoted`, no append, no count bump.
3. **Cross-date:** Semantic scan entire `memories/` under output root, not only current `review/<date>.jsonl`.
4. **Re-import:** Review id reorder OK if promotion_key unchanged.

**Promote atomicity:** Compute full plan, then write `memories/`, `review/`, `manifest.json` via temp + rename.

### Manifest counts

- `durable_memories`: increment only for newly appended lines (`created=true`).
- Optional: `promote_skipped_duplicate`, `promote_conflicts_skipped` for automation.

### Dual-gate interaction

| Action | session2memory | agentic-os |
|--------|----------------|------------|
| approve-bulk | Updates `review/*.jsonl` only | No API call |
| promote | Writes `memories/` only | No API call |
| Trace field (optional) | `agentic_os_item_id` on review row | Informational |

---

## §4 Rollout & Integration

**Order:** (1) Docs/skill/P0 list alignment + `AgenticOsIndex` stub/enrich fields; (2) `openclaw`/`hermes` adapters with fixtures; (3) bulk + conflicts + semantic dedup + atomic promote; (4) README roadmap closure; (5) HKS integration test pass if evidence schema adds optional keys.

**Docs/skill:** `README.md`, `skills/session2memory/SKILL.md`, `skill.json` `supported_tools` must match `P0_TOOLS`.

**HKS contract:** Optional evidence fields must remain backward-compatible for `ks ingest`; daily/review rows keep compact evidence ids; full paths stay in `evidence/index.jsonl` only.

---

## Out of Scope

- agentic-os → session2memory review seed automation (hook only in evidence).
- Bidirectional sync between MemoryStore and `memories/`.
- NLP / LLM extraction beyond markers.
- Changing HKS `KS_ROOT` layout or ingest semantics.
- Bulk un-promote or editing `promoted` rows back to `pending` in v1.

---

## Testing

| Area | Tests |
|------|--------|
| openclaw / hermes | `tests/fixtures/openclaw/`, `hermes/`; `test_adapter_openclaw.py`, `test_adapter_hermes.py` |
| AgenticOsIndex | `test_agentic_os_index.py` with tmp sqlite |
| Bulk | `test_review_bulk.py` filters, dry-run, idempotency |
| Conflicts | `test_review_conflicts.py` all `--resolve` modes, exit code 2 |
| Promote | Extend `test_promote.py`: semantic dedup, cross-date, atomic write, manifest counts |
| CLI | `CliRunner` approve-bulk → conflicts → promote |
| HKS | Run `tests/integration/test_hks_compatibility.py` if evidence optional fields change |

---

## Open Questions

1. **openclaw / hermes log format:** Need 1–2 real samples from `~/.openclaw/logs` and `~/.hermes/logs` to finalize session boundaries and line→role mapping before adapter implementation.
2. **agentic-os session → harness mapping:** Confirm stable columns in `agentic-os.db` for `harness_id` and upstream session id (read schema from live DB during implementation).
3. **`--agentic-os-sessions-only` default:** Spec keeps default **off**; revisit if imports are too noisy.

---

## Spec Self-Review (2026-05-31)

- [x] No TBD sections; open items listed explicitly under Open Questions.
- [x] §1 dual gate consistent with §3 (HKS only via promote; agentic-os parallel).
- [x] agentic-os not listed as P0 `--tool`; evidence index only.
- [x] Importer does not read MemoryStore; promote does not call agentic-os API.
- [x] Scope bounded; rollout order in §4.
