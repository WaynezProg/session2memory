# session_worklog Design

**Date:** 2026-06-09  
**Status:** Frozen (v1)  
**Scope:** Deterministic worklog aggregation from `session2memory.db` for HKS ingest.

## Problem

`import` writes managed generated files (`daily/`, `evidence/`, `review/`, `manifest.json`)
that are **replaced** on re-import. Weekly or monthly rollups that read `daily/*.md` lose
history whenever a day is re-imported. README already documents this replacement behavior.

Worklog aggregation belongs in `session2memory`; HKS only ingests the result.

## Goals

| Goal | Success criteria |
|------|------------------|
| Durable source | `worklog` reads `candidates` from `session2memory.db` by `import_date` range |
| HKS ingest | Output under `worklogs/` with `hks_type: session_worklog` |
| Evidence-backed lines | Every entry line includes `evidence_id`, `tool`, `session_id`, `lines` |
| Deterministic v1 | No LLM synthesis, Email/Calendar/Jira, or HKS-generated worklogs |
| Date-named files | Output filenames use ISO dates, not period aliases |

## Non-goals (v1)

- LLM synthesis or narrative summarization
- Email, Calendar, Jira, or other external sources
- HKS generating worklogs (one-way: s2m → HKS)
- Replacing `daily/YYYY-MM-DD.md` as the primary per-day ingest target
- Updating `manifest.json` on worklog generation (`worklogs/` is outside managed import output)

## CLI contract

```bash
uv run session2memory worklog yesterday --output ./out/session-memory
uv run session2memory worklog last-week --output ./out/session-memory
uv run session2memory worklog last-month --output ./out/session-memory
uv run session2memory worklog --from 2026-06-01 --to 2026-06-07 --output ./out/session-memory
```

- Period aliases (`yesterday`, `last-week`, `last-month`) resolve to concrete date ranges.
- `--from` / `--to` are inclusive `YYYY-MM-DD`; mutually exclusive with a period argument.
- `--state-db` overrides default `<output>/session2memory.db`.
- Fails if the state database does not exist.

### Period semantics

| Period | Range |
|--------|-------|
| `yesterday` | calendar day before today |
| `last-week` | previous Monday–Sunday |
| `last-month` | previous calendar month (1st through last day) |

## Output layout

```text
out/session-memory/
  worklogs/
    2026-06-08.md                      # yesterday (single day)
    2026-06-01_2026-06-07.md           # last-week or custom range
    2026-05-01_2026-05-31.md           # last-month
```

Single-day ranges use `YYYY-MM-DD.md`. Multi-day ranges use `YYYY-MM-DD_YYYY-MM-DD.md`.

`worklogs/` is **not** cleared by import's managed-output replacement.

## Markdown format

Front matter:

```yaml
---
hks_type: session_worklog
period: yesterday          # or last-week, last-month, 2026-06-01..2026-06-07
date_from: 2026-06-08
date_to: 2026-06-08
generator: session2memory
source_domain: coding_session
schema_version: 1
---
```

Body sections (fixed order):

1. **Summary** — entry count, workspace count, date range
2. **Shipped** — `kind=completed`
3. **Verified** — `kind=verification`
4. **Decisions** — `kind=decision`
5. **Constraints** — `kind=constraint`
6. **Pitfalls** — `kind=pitfall`
7. **Notes** — `kind=daily`

Empty sections render `_No entries._`.

Entry line template:

```markdown
- [decision] Use SQLite for state. {evidence_id=e_abc tool=codex session_id=s1 lines=2-2 workspace_id=repo-123 import_date=2026-06-02}
```

## Data source

Query `candidates` where `import_date >= date_from AND import_date <= date_to`, ordered by
`import_date`, `evidence_id`, `candidate_id`. All review statuses are included; worklog is a
factual rollup of compiled session memory, not promoted durable memory.

## HKS ingest

Operators or agents ingest `worklogs/<date>.md` the same way as `daily/`. Raw transcripts
and `evidence/index.jsonl` `source_path` values must not appear in worklog body text.

## Phase 2 (future)

- Optional LLM synthesis layer with evidence citations only
- `manifest.json` hint listing latest worklog files
- Filter by `review_status` or `durable` flag
