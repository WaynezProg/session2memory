# Distill / Validate / Solidify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `distill`, `validate`, and `solidify` CLI pipeline from
`docs/specs/distill-solidify.md` without changing existing review/promote behavior.

**Architecture:** Add three focused modules: `distill.py` reads approved review rows and
materializes candidates/evidence under `distill/YYYY-MM-DD`; `validate.py` performs
deterministic gates, scoring, dedupe, and conflict output; `solidify.py` renders
reviewable JSONL/Markdown artifacts. `cli.py` only wires Typer commands to those modules.

**Tech Stack:** Python 3.12, Typer, stdlib JSON/Path/dataclasses, existing pytest/Ruff/mypy
tooling.

---

## File Structure

- Create `src/session2memory/distill.py`: strict JSONL reads, evidence projection,
  deterministic distill candidate generation, atomic writes.
- Create `src/session2memory/validate.py`: schema checks, validation gates, scoring,
  duplicate merge, validation reports.
- Create `src/session2memory/solidify.py`: final reviewable JSONL and Markdown rendering.
- Modify `src/session2memory/cli.py`: add `distill`, `validate`, `solidify` commands with
  existing `_parse_date` style and default `--output ./out/session-memory`.
- Create `tests/test_distill.py`: module and CLI behavior for approved-only distill.
- Create `tests/test_validate.py`: hard gates, scoring, dedupe, correction precedence.
- Create `tests/test_solidify.py`: reviewable artifact rendering and safety boundaries.
- Create `tests/test_cli_distill_solidify.py`: command contract and promote regression.

## Task 1: Distill Approved Review Rows

**Files:**
- Create: `tests/test_distill.py`
- Create: `src/session2memory/distill.py`
- Modify: `src/session2memory/cli.py`

- [ ] **Step 1: Write failing distill tests**

```python
def test_distill_reads_only_approved_review_rows(tmp_path: Path) -> None:
    result = distill_reviews(output_dir=output, date="2026-05-22")
    assert result.approved_reviews == 2
    assert [row["source_review_ids"] for row in candidates] == [["r-approved-a"], ["r-approved-b"]]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_distill.py -q`
Expected: FAIL because `session2memory.distill` does not exist.

- [ ] **Step 3: Implement minimal distill module and CLI**

Implement `distill_reviews(output_dir: Path, date: str) -> DistillResult`, writing:
`evidence_index.jsonl`, `candidates.jsonl`, and `manifest.json`.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_distill.py -q`
Expected: PASS.

## Task 2: Validate Gates, Scoring, And Dedupe

**Files:**
- Create: `tests/test_validate.py`
- Create: `src/session2memory/validate.py`
- Modify: `src/session2memory/cli.py`

- [ ] **Step 1: Write failing validate tests**

```python
def test_validate_blocks_real_completion_supported_only_by_mock_or_dry_run(tmp_path: Path) -> None:
    result = validate_distill(distill_dir)
    assert validation[0]["validation_outcome"] == "blocked"
    assert "mock_or_dry_run_real_completion" in validation[0]["hard_gate_failures"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_validate.py -q`
Expected: FAIL because `session2memory.validate` does not exist.

- [ ] **Step 3: Implement deterministic validation**

Implement hard gates from the spec, score clamping, duplicate merge by type/scope/workspace
and normalized claim, and write `validation.jsonl`, `validation_report.json`,
`merged_candidates.jsonl`.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_validate.py -q`
Expected: PASS.

## Task 3: Solidify Reviewable Artifacts

**Files:**
- Create: `tests/test_solidify.py`
- Create: `src/session2memory/solidify.py`
- Modify: `src/session2memory/cli.py`

- [ ] **Step 1: Write failing solidify tests**

```python
def test_solidify_never_writes_review_or_memories(tmp_path: Path) -> None:
    result = solidify_distill(distill_dir)
    assert not (output / "review").exists()
    assert not (output / "memories").exists()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_solidify.py -q`
Expected: FAIL because `session2memory.solidify` does not exist.

- [ ] **Step 3: Implement solidify renderer**

Render `solidified/solidified.jsonl`, candidate-type Markdown files, and
`solidified/manifest.json` without writing outside the distill directory.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_solidify.py -q`
Expected: PASS.

## Task 4: CLI Contract And Regression Gates

**Files:**
- Create: `tests/test_cli_distill_solidify.py`
- Modify: `src/session2memory/cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
def test_distill_validate_solidify_cli_pipeline(tmp_path: Path) -> None:
    assert runner.invoke(app, ["distill", "--date", "2026-05-22", "--output", str(output)]).exit_code == 0
    assert runner.invoke(app, ["validate", "--distill", str(output / "distill" / "2026-05-22")]).exit_code == 0
    assert runner.invoke(app, ["solidify", "--distill", str(output / "distill" / "2026-05-22")]).exit_code == 0
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_cli_distill_solidify.py -q`
Expected: FAIL until CLI commands are wired.

- [ ] **Step 3: Wire CLI summaries and error handling**

Commands should print concise summaries and raise Typer bad-parameter errors for invalid
input. Existing `review promote` and legacy `promote` remain untouched.

- [ ] **Step 4: Verify all gates**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
git diff --check
```

Expected: all commands exit `0`.
