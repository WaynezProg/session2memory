# session_worklog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add deterministic `session2memory worklog` that aggregates `candidates` from
`session2memory.db` into HKS-ingestable `worklogs/<date>.md` files.

**Architecture:** `worklog.py` owns date-range resolution and markdown rendering;
`StateStore.list_candidates_in_range` queries SQLite; `cli.py` wires the Typer command.
`worklogs/` stays outside import's managed-output replacement path.

**Tech Stack:** Python 3.12, Typer, sqlite3, pytest, Ruff, mypy.

**Spec:** `docs/specs/2026-06-09-session-worklog-design.md`

---

## File Structure

- Create `src/session2memory/worklog.py` — range resolution, markdown render, file write
- Modify `src/session2memory/state/store.py` — `list_candidates_in_range`
- Modify `src/session2memory/cli.py` — `worklog` command
- Create `tests/test_worklog.py` — unit + CLI tests
- Create `tests/golden/worklog_2026-06-01_2026-06-07.md` — golden markdown
- Modify `README.md` — operator docs

---

## Task 1: State Store Range Query

**Files:**
- Modify: `src/session2memory/state/store.py`
- Test: `tests/test_worklog.py`

- [x] **Step 1: Add `list_candidates_in_range`**

```python
def list_candidates_in_range(
    self, *, date_from: str, date_to: str
) -> list[tuple[str, StoredCandidate]]:
    rows = self._connection.execute(
        """
        SELECT * FROM candidates
        WHERE import_date >= ? AND import_date <= ?
        ORDER BY import_date, evidence_id, candidate_id
        """,
        (date_from, date_to),
    ).fetchall()
```

- [x] **Step 2: Verify with seeded store test**

Run: `uv run pytest tests/test_worklog.py::test_generate_worklog_reads_db_not_daily_markdown -q`

---

## Task 2: Worklog Module

**Files:**
- Create: `src/session2memory/worklog.py`
- Test: `tests/test_worklog.py`

- [x] **Step 1: Range resolution with date-based output names**

```python
def _output_name(date_from: date, date_to: date) -> str:
    if date_from == date_to:
        return f"{date_from.isoformat()}.md"
    return f"{date_from.isoformat()}_{date_to.isoformat()}.md"
```

- [x] **Step 2: Render `hks_type: session_worklog` with six sections**

- [x] **Step 3: Entry lines include evidence_id, tool, session_id, lines**

Run: `uv run pytest tests/test_worklog.py -q`

---

## Task 3: CLI Command

**Files:**
- Modify: `src/session2memory/cli.py`
- Test: `tests/test_worklog.py`

- [x] **Step 1: Wire `worklog` Typer command**

- [x] **Step 2: Fail when `session2memory.db` missing**

- [x] **Step 3: CLI integration tests**

Run: `uv run pytest tests/test_worklog.py::test_cli_worklog_yesterday -q`

---

## Task 4: Golden Output And README

**Files:**
- Create: `tests/golden/worklog_2026-06-01_2026-06-07.md`
- Modify: `tests/test_worklog.py`
- Modify: `README.md`

- [x] **Step 1: Add golden file test**

```python
def test_worklog_matches_golden(tmp_path: Path) -> None:
    ...
    assert markdown == read_golden("worklog_2026-06-01_2026-06-07.md")
```

- [x] **Step 2: Document worklog in README**

Add `## Worklog Aggregation` with CLI examples and `worklogs/` filename rules.

- [x] **Step 3: Verify**

Run:

```bash
uv run pytest tests/test_worklog.py -q
uv run ruff check src/session2memory/worklog.py
uv run mypy src/session2memory/worklog.py
```

---

## Task 5: Regression Gates

- [x] **Step 1: Full test suite**

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/session2memory
```

Expected: all exit `0`.
