# Distill / Validate / Solidify Pipeline

**Date:** 2026-06-05
**Status:** Draft
**Scope:** Add a spec-first, evidence-backed intermediate pipeline between approved review
rows and future human-reviewed durable memory work. This document is a design contract and
test plan only; it does not require CLI implementation in the first round.

## Problem Definition

`session2memory` already has a conservative compiler path:

```text
import -> review -> promote
```

`import` emits daily logs, evidence pointers, and review candidates. `promote` only writes
workspace memory from `review/YYYY-MM-DD.jsonl` rows where `status=approved` and
`durable_suggestion=true`.

That flow is safe for explicit memory candidates, but it does not provide a structured way
to extract reusable follow-on knowledge from approved sessions:

- reusable memory candidates that need more evidence before becoming durable memory
- repo or workflow rules that should be reviewed before becoming instructions
- validator candidates that describe future checks, gates, or regression tests

The new capability must keep the review-first contract. It may read approved review rows
and evidence, but it must not write durable memory, install skills, overwrite agent
instructions, or bypass existing promote logic.

## Non-goals

- Skill auto install or package installation
- Automatic `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, OpenClaw, or Hermes writes
- Direct writes to `memories/` or any official durable memory store
- External API validation or network-backed fact checking
- Destructive cleanup of existing `daily/`, `evidence/`, `review/`, `memories/`, or HKS data
- Changing existing `review approve`, `review reject`, `review promote`, or legacy `promote`
  behavior
- Supporting candidate types beyond `memory_candidate`, `rule_candidate`, and
  `validator_candidate`

## Target Flow

```text
import -> review -> evidence -> distill -> validate -> solidify -> promote
```

The final `promote` step remains the existing review/promote safety boundary. In v1,
`solidify` produces reviewable artifacts only. A future explicit human-review command may
copy or transform solidified output into `review/YYYY-MM-DD.jsonl`, but `solidify` itself
must not append to review rows or memory files.

## CLI Contract

Required v1 commands:

```bash
session2memory distill --date YYYY-MM-DD
session2memory validate --distill distill/YYYY-MM-DD
session2memory solidify --distill distill/YYYY-MM-DD
```

Implementation note for the next round: existing repo commands use `--output` to locate the
generated session-memory folder. The implementation should either:

- add optional `--output ./out/session-memory` to the three commands while keeping the
  required command shape above valid, or
- define `./out/session-memory` as the documented default output root.

### `distill --date YYYY-MM-DD`

Reads:

- `<output>/review/YYYY-MM-DD.jsonl`
- `<output>/evidence/index.jsonl`
- `<output>/manifest.json` when available

Filters:

- include only review rows with `status=approved`
- ignore rejected, pending, promoted, revoked, superseded, or malformed rows
- do not require `durable_suggestion=true`; distill is an analysis stage, not promotion

Writes:

- `<output>/distill/YYYY-MM-DD/evidence_index.jsonl`
- `<output>/distill/YYYY-MM-DD/candidates.jsonl`
- `<output>/distill/YYYY-MM-DD/manifest.json`

Exit behavior:

- exit `0` when no approved rows exist and write an empty manifest with counts
- exit non-zero for invalid JSONL, unsupported candidate type, or non-writable distill path

### `validate --distill distill/YYYY-MM-DD`

Reads:

- `evidence_index.jsonl`
- `candidates.jsonl`
- existing validation output only for deterministic re-run comparison

Writes:

- `validation.jsonl`
- `validation_report.json`
- `merged_candidates.jsonl`

Validation must not invent new claims. It only scores, deduplicates, checks conflicts, and
classifies `mock`, `dry_run`, and `real` evidence.

### `solidify --distill distill/YYYY-MM-DD`

Reads:

- `merged_candidates.jsonl` if present, otherwise `candidates.jsonl`
- `validation.jsonl`
- `validation_report.json`
- `evidence_index.jsonl`

Writes:

- `solidified/solidified.jsonl`
- `solidified/memory_candidates.md`
- `solidified/rule_candidates.md`
- `solidified/validator_candidates.md`
- `solidified/manifest.json`

`solidify` must not write to `review/`, `memories/`, HKS, agent instruction files, or skill
directories.

## Output Directory Layout

```text
<output>/
  daily/
    YYYY-MM-DD.md
  evidence/
    index.jsonl
  review/
    YYYY-MM-DD.jsonl
  distill/
    YYYY-MM-DD/
      evidence_index.jsonl
      candidates.jsonl
      manifest.json
      validation.jsonl
      validation_report.json
      merged_candidates.jsonl
      solidified/
        solidified.jsonl
        memory_candidates.md
        rule_candidates.md
        validator_candidates.md
        manifest.json
```

The `distill/YYYY-MM-DD/` directory is generated, reviewable, and disposable. Removing it
rolls back the new pipeline without changing imported evidence, review state, promoted
memory, or HKS output.

## `evidence_index.jsonl` Schema

Each line is a JSON object. Required fields:

```json
{
  "evidence_id": "e000001",
  "source_path": "/absolute/or/original/source/path.jsonl",
  "source_type": "session_message",
  "timestamp": "2026-06-05T09:30:00+08:00",
  "linked_session_id": "s1",
  "confidence": 0.86
}
```

Additional recommended fields:

```json
{
  "source_available": true,
  "source_unavailable_reason": null,
  "tool": "codex",
  "workspace_id": "repo-123",
  "review_ids": ["r000001"],
  "message_start": 2,
  "message_end": 4,
  "actor_roles": ["user", "assistant", "tool"],
  "evidence_mode": "real",
  "summary": "Reviewer-approved evidence span for a durable workflow decision.",
  "digest": "sha256:abc"
}
```

Field rules:

- `source_path` must be the original source path when known; if unavailable, keep the best
  known path and set `source_available=false` plus `source_unavailable_reason`.
- `source_type` is one of `session_message`, `review_row`, `tool_output`,
  `user_correction`, `assistant_summary`, `test_result`, or `file_snapshot`.
- `timestamp` must be ISO 8601 with timezone when available. If the source has no timestamp,
  use the importing evidence timestamp and record that choice in `summary`.
- `linked_session_id` is the local session id from the original evidence or review row.
- `confidence` is an evidence quality score from `0.0` to `1.0`, not a truth score.
- `evidence_mode` is one of `real`, `mock`, `dry_run`, or `unknown`.

## Candidate Schema

Each line in `candidates.jsonl` is a distill candidate, not an existing
`MemoryCandidate` model row. Required fields:

```json
{
  "candidate_id": "dc_8d3f5a1c9b21",
  "candidate_type": "memory_candidate",
  "claim": "Promoted memories must stay behind the approved durable review gate.",
  "evidence_ids": ["e000001"],
  "reuse_scope": "workspace",
  "risk_level": "medium",
  "status": "proposed"
}
```

Allowed `candidate_type` values:

- `memory_candidate`
- `rule_candidate`
- `validator_candidate`

Allowed `reuse_scope` values:

- `session`
- `workspace`
- `repo`
- `toolchain`
- `global`

Allowed `risk_level` values:

- `low`
- `medium`
- `high`
- `critical`

Allowed `status` values before validation:

- `proposed`
- `merged`
- `blocked`

Recommended fields:

```json
{
  "normalized_claim": "promoted memories must stay behind the approved durable review gate",
  "source_review_ids": ["r000001"],
  "workspace_id": "repo-123",
  "created_at": "2026-06-05T09:35:00+08:00",
  "distiller": "session2memory",
  "claim_mode": "real_completion",
  "merged_from": [],
  "blocked_by": [],
  "review_notes": []
}
```

Candidate rules:

- `evidence_ids` must contain at least one id.
- `claim` must be a reusable assertion, rule, or validator description; it must not be a
  transcript summary with no reuse scope.
- `validator_candidate` claims must describe a future check in executable terms, such as a
  CLI test, schema assertion, or file contract.
- `rule_candidate` claims must name the target rule surface, but v1 must not write to that
  surface.
- `claim_mode=real_completion` is allowed only when evidence supports a real completed
  action rather than a mock, dry-run, plan, or assistant summary.

## Validation Scoring Model

Validation is deterministic. It must not call external APIs, ask an LLM to invent new
facts, or rewrite claims into stronger statements.

### Hard gates

A candidate is `blocked` when any hard gate fails:

| Gate | Block condition |
|------|-----------------|
| Evidence present | `len(evidence_ids) < 1` |
| Source path | every evidence row is missing `source_path` without `source_available=false` |
| Source availability | `source_available=false` without `source_unavailable_reason` |
| Assistant-only | all supporting evidence is `assistant_summary` or actor role `assistant` |
| Mock/dry-run | `claim_mode=real_completion` but all supporting evidence is `mock` or `dry_run` |
| Duplicate | normalized duplicate exists and cannot be merged deterministically |
| Contradiction | newer contradictory evidence exists |
| User correction | a user correction contradicts the claim |
| Candidate type | `candidate_type` is not one of the three v1 types |

### Weights

Start each non-blocked candidate at `50`. Add or subtract:

| Signal | Score change |
|--------|--------------|
| User correction supporting claim | `+30` |
| User-authored decision or instruction | `+25` |
| Tool output, test result, or file snapshot supporting claim | `+20` |
| Approved review row linked to the claim | `+15` |
| Multiple independent evidence ids | `+10` |
| Assistant summary as supporting context | `+5` maximum, never sufficient alone |
| Source unavailable but explicitly marked | `-15` |
| Evidence mode unknown | `-10` |
| High risk | `-10` |
| Critical risk | `-25` |

Scores are clamped to `0..100`.

### Outcomes

| Outcome | Rule |
|---------|------|
| `pass` | no hard gate failure and score `>= 70` |
| `needs_review` | no hard gate failure and score `50..69` |
| `blocked` | any hard gate failure or score `< 50` |
| `merged` | duplicate was merged into a canonical candidate |

### Deduplication

Duplicate candidates are grouped by:

- `candidate_type`
- `reuse_scope`
- `workspace_id` when present
- normalized claim text

The canonical candidate keeps the earliest deterministic `candidate_id`, unions
`evidence_ids`, records `merged_from`, and receives the strongest non-contradictory
validation outcome. Duplicate candidates must not be emitted as separate solidified rows.

### Conflict and correction precedence

- Newer contradictory evidence blocks older claims.
- `user_correction` evidence outranks assistant summaries, review notes, and generated
  summaries.
- Tool outputs and test results outrank assistant summaries for completion claims.
- Mock and dry-run evidence may support a plan or validator candidate, but cannot support a
  `real_completion` claim.

## Solidified Output Schema

Each line in `solidified/solidified.jsonl` is reviewable output:

```json
{
  "solidified_id": "so_2b5e0a840f91",
  "candidate_id": "dc_8d3f5a1c9b21",
  "candidate_type": "memory_candidate",
  "claim": "Promoted memories must stay behind the approved durable review gate.",
  "reuse_scope": "workspace",
  "risk_level": "medium",
  "status": "ready_for_review",
  "validation_outcome": "pass",
  "validation_score": 90,
  "evidence_ids": ["e000001"],
  "merged_from": [],
  "blocked_by": [],
  "suggested_review_action": "create_pending_review_row"
}
```

Allowed `status` values:

- `ready_for_review`
- `needs_human_review`
- `blocked`

Markdown artifacts must group rows by candidate type and include:

- claim
- validation outcome and score
- evidence ids
- source availability summary
- risk level
- suggested human action

Markdown artifacts must not include raw transcript text beyond compact evidence summaries.
Full raw source paths remain in `evidence_index.jsonl`.

## Relationship To Promote

`promote` keeps its existing safety logic:

- it reads `review/YYYY-MM-DD.jsonl`
- it filters to `status=approved` and `durable_suggestion=true`
- it performs existing conflict and duplicate checks
- it writes `memories/<workspace_id>.md` only through the current promote path

The distill pipeline must not:

- mark review rows as approved
- set `durable_suggestion=true`
- change `status` in existing review rows
- append to `memories/`
- write HKS-ingestable durable memory directly

The logical `solidify -> promote` handoff means: a human or future explicit review-import
command may convert selected solidified output into normal review rows, after which existing
review and promote gates apply unchanged.

## Failure Cases

| Case | Expected behavior |
|------|-------------------|
| Missing `review/YYYY-MM-DD.jsonl` | `distill` writes empty manifest and exits `0` |
| No approved rows | `distill` writes empty candidate files and exits `0` |
| Invalid review JSONL | command exits non-zero and writes no partial output |
| Missing `evidence/index.jsonl` | candidates can be emitted only with unavailable evidence markers; validation blocks them |
| Review row references missing evidence id | candidate status becomes `blocked` during validation |
| Source path missing on disk | evidence row sets `source_available=false`; validation blocks only if reason is absent |
| Assistant-only claim | validation outcome `blocked` |
| Mock/dry-run marked as real completion | validation outcome `blocked` |
| Duplicate candidates | validation emits one canonical row with `merged_from` |
| Newer contradictory evidence | validation outcome `blocked` with `blocked_by` evidence id |
| User correction contradicts assistant summary | user correction wins; candidate blocked or downgraded |
| Existing distill directory contains unknown files | commands preserve unknown files |
| Non-writable distill directory | command exits non-zero before writing |

## Rollback And Safety Rules

- All writes are limited to `<output>/distill/YYYY-MM-DD/`.
- Use atomic writes for generated JSONL, JSON, and Markdown files.
- Do not delete unknown files in the distill directory.
- Do not modify `daily/`, `evidence/`, `review/`, `memories/`, HKS roots, skill folders, or
  agent instruction files.
- Re-running `distill`, `validate`, or `solidify` may replace only the files owned by that
  command inside the distill directory.
- `manifest.json` must list command name, input files, output files, counts, and schema
  version.
- Rollback is deleting `<output>/distill/YYYY-MM-DD/`; no source or durable state changes
  are required.
- Any future command that imports solidified output into review rows must create pending
  rows by default and must require explicit review before promotion.

## First Version Test Plan

Follow existing repo style: `pytest`, `tmp_path`, `CliRunner`, JSONL exact assertions, and
focused unit tests beside the behavior under test.

### Distill tests

- `test_distill_reads_only_approved_review_rows`: fixture with approved, pending, rejected,
  and promoted rows; only approved rows produce candidates.
- `test_distill_does_not_require_durable_suggestion_true`: approved daily-only row may
  produce a distill candidate but no durable output.
- `test_distill_writes_evidence_index_with_required_fields`: assert `source_path`,
  `source_type`, `timestamp`, `linked_session_id`, and `confidence`.
- `test_distill_missing_review_file_writes_empty_manifest`: no review file exits `0` and
  writes counts of zero.
- `test_distill_invalid_jsonl_exits_nonzero_without_partial_output`: malformed row produces
  no `candidates.jsonl`.

### Validate tests

- `test_validate_blocks_candidate_without_evidence_ids`.
- `test_validate_blocks_assistant_only_claim`.
- `test_validate_blocks_real_completion_supported_only_by_mock_or_dry_run`.
- `test_validate_merges_duplicate_candidates_into_one_canonical_row`.
- `test_validate_blocks_newer_contradictory_evidence`.
- `test_validate_user_correction_outweighs_assistant_summary`.
- `test_validate_scores_pass_needs_review_and_blocked_boundaries`.

### Solidify tests

- `test_solidify_emits_reviewable_jsonl_and_markdown_by_candidate_type`.
- `test_solidify_never_writes_review_or_memories`.
- `test_solidify_marks_low_score_rows_needs_human_review`.
- `test_solidify_preserves_blocked_rows_with_block_reasons`.

### CLI tests

- `test_distill_cli_date_contract`.
- `test_validate_cli_distill_path_contract`.
- `test_solidify_cli_distill_path_contract`.
- `test_distill_validate_solidify_default_output_root_or_output_option`.
- `test_commands_do_not_change_existing_promote_behavior`.

### Regression and integration tests

- Existing `tests/test_promote.py` cases must remain unchanged, especially approved
  non-durable rows staying out of `memories/`.
- Existing `tests/integration/test_hks_compatibility.py` must still pass because distill
  artifacts are not HKS primary documents.
- Add a golden fixture only if Markdown solidified artifacts need stable rendering.
- Run `uv run pytest -q`, `uv run ruff check .`, and
  `uv run mypy src/session2memory` before handoff.

## Implementation Notes For Next Round

Suggested files:

- `src/session2memory/distill.py`: read approved review rows and build distill evidence and
  candidates.
- `src/session2memory/validate.py`: deterministic gates, scoring, deduplication, and
  conflict output.
- `src/session2memory/solidify.py`: reviewable JSONL and Markdown rendering.
- `src/session2memory/cli.py`: add three commands without changing existing commands.
- `tests/test_distill.py`, `tests/test_validate.py`, `tests/test_solidify.py`,
  `tests/test_cli_distill_solidify.py`: focused tests.

Do not implement skill installation, instruction-file sync, HKS ingest, external API calls,
or direct durable memory writes in the first implementation round.
