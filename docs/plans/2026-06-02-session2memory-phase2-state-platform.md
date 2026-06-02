# Phase 2: State DB, Redaction, Lifecycle, Sync Hardening, Adapter SDK — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Complete **Phase 1 plan first**.

**Goal:** Make `session2memory.db` the canonical state (stable IDs, incremental import), harden safety/lifecycle, and expose adapter plugins.

**Architecture:** Introduce `StateStore` SQLite layer; refactor `run_pipeline`, `promote_reviews`, and `sync_workspace_memory` to read/write DB then export files. Redaction runs at export boundaries.

**Tech Stack:** Python 3.12, sqlite3 stdlib, Typer, pytest.

**Spec:** `docs/specs/2026-06-02-session2memory-memory-platform-design.md` (Phase 2 sections)

**Prerequisite:** `docs/plans/2026-06-02-session2memory-phase1-review-platform.md` merged.

---

## File map

| File | Responsibility |
|------|----------------|
| `src/session2memory/state/` | Package: schema, store, ids, migration |
| `src/session2memory/state/schema.sql` | DDL |
| `src/session2memory/state/store.py` | `StateStore` CRUD + transactions |
| `src/session2memory/state/ids.py` | Stable `evidence_id` / `candidate_id` helpers |
| `src/session2memory/state/migrate.py` | Legacy JSONL → DB backfill |
| `src/session2memory/redaction.py` | Redaction rules |
| `src/session2memory/memory_lifecycle.py` | revoke / supersede |
| `src/session2memory/pipeline.py` | Incremental import via store |
| `src/session2memory/writer.py` | Export from store |
| `src/session2memory/review.py` | Promote updates store |
| `src/session2memory/sync_back.py` | `sync_targets` hash skip |
| `src/session2memory/adapters/registry.py` | Plugin registration |
| `docs/adapters.md` | Adapter author guide |
| `tests/test_state_store.py` | DB + stable id tests |
| `tests/test_migration.py` | Legacy backfill |
| `tests/test_redaction.py` | Snapshot tests |
| `tests/test_memory_lifecycle.py` | revoke/supersede |
| `tests/test_adapter_registry.py` | entry point smoke |

---

### Task 1: Schema + StateStore skeleton

**Files:**
- Create: `src/session2memory/state/schema.sql`
- Create: `src/session2memory/state/store.py`
- Create: `src/session2memory/state/__init__.py`
- Test: `tests/test_state_store.py`

- [ ] **Step 1: Failing test — open DB, insert source_file, read back**

```python
def test_state_store_upsert_source_file(tmp_path: Path) -> None:
    from session2memory.state.store import StateStore

    store = StateStore.open(tmp_path / "session2memory.db")
    store.upsert_source_file(
        tool="codex",
        path="/tmp/s.jsonl",
        digest="sha256:abc",
        mtime_ns=1,
    )
    row = store.get_source_file(tool="codex", path="/tmp/s.jsonl")
    assert row is not None
    assert row["digest"] == "sha256:abc"
```

- [ ] **Step 2–4: Implement `StateStore.open`, `apply_schema`, `upsert_source_file`**

- [ ] **Step 5: Commit**

---

### Task 2: Stable ID helpers

**Files:**
- Create: `src/session2memory/state/ids.py`
- Test: `tests/test_state_store.py`

- [ ] **Step 1: Test stable evidence id**

```python
from session2memory.state.ids import evidence_id_for

def test_evidence_id_stable() -> None:
    a = evidence_id_for(tool="codex", session_id="s", start=1, end=2, digest="sha256:x")
    b = evidence_id_for(tool="codex", session_id="s", start=1, end=2, digest="sha256:x")
    assert a == b
    assert a.startswith("e_")
```

- [ ] **Step 2–4: Implement hash-based ids per spec**

- [ ] **Step 5: Commit**

---

### Task 3: Incremental import in pipeline

**Files:**
- Modify: `src/session2memory/pipeline.py`
- Modify: `src/session2memory/state/store.py`
- Test: `tests/test_state_store.py`

- [ ] **Step 1: Test — re-import unchanged file does not add duplicate candidate**

- [ ] **Step 2: `run_pipeline` accepts `state_store: StateStore | None`; when set:**

1. Upsert `source_files` by digest/mtime  
2. Skip sessions whose digest unchanged  
3. Upsert candidates/evidence with stable ids  

- [ ] **Step 3: CLI `import` adds `--state-db` defaulting to `<output>/session2memory.db`**

- [ ] **Step 4: Still call `write_output` for HKS-compatible export**

- [ ] **Step 5: Commit**

---

### Task 4: Legacy migration

**Files:**
- Create: `src/session2memory/state/migrate.py`
- Test: `tests/test_migration.py`

- [ ] **Step 1: Fixture with old `e000001` evidence + review row**

- [ ] **Step 2: `migrate_legacy_output(output_dir) -> MigrationReport`**

- [ ] **Step 3: Write `migration_report.json` listing unmapped ids**

- [ ] **Step 4: Auto-run on first `StateStore.open` if DB empty and `evidence/index.jsonl` exists**

- [ ] **Step 5: Commit**

---

### Task 5: Redaction module

**Files:**
- Create: `src/session2memory/redaction.py`
- Modify: `src/session2memory/writer.py`, `sync_back.py`, `review.py` (preview)
- Test: `tests/test_redaction.py`

- [ ] **Step 1: Snapshot tests**

```python
def test_redact_home_and_tokens() -> None:
    from session2memory.redaction import redact_text
    text = "key=sk-abc path=/Users/me/proj"
    out = redact_text(text, home=Path("/Users/me"))
    assert "sk-abc" not in out
    assert "/Users/me" not in out
```

- [ ] **Step 2–4: Implement ordered rules from spec**

- [ ] **Step 5: Apply in `_review_record`, daily lines, sync `render_harness_memory_body`**

- [ ] **Step 6: Commit**

---

### Task 6: Memory lifecycle (revoke / supersede)

**Files:**
- Create: `src/session2memory/memory_lifecycle.py`
- Modify: `src/session2memory/cli.py`, `state/store.py`, `writer.py`
- Test: `tests/test_memory_lifecycle.py`

- [ ] **Step 1: `memory_entry_id` assigned at promote time in store + memories md**

- [ ] **Step 2: CLI `session2memory memory revoke --id …`**

- [ ] **Step 3: CLI `session2memory memory supersede --old … --new …`**

- [ ] **Step 4: Export/sync excludes `status=revoked`; supersede shows only winner**

- [ ] **Step 5: Commit**

---

### Task 7: Sync-back hardening

**Files:**
- Modify: `src/session2memory/sync_back.py`, `state/store.py`
- Test: `tests/test_sync_back.py`

- [ ] **Step 1: Test — second sync with unchanged body → `changed=False`**

- [ ] **Step 2: Record `content_hash` in `sync_targets` table after write**

- [ ] **Step 3: Flags `--force`, `--since-last-sync`**

- [ ] **Step 4: Commit**

---

### Task 8: Adapter plugin SDK

**Files:**
- Create: `src/session2memory/adapters/registry.py`
- Modify: `src/session2memory/cli.py` (`_build_adapters` loads entry points)
- Create: `docs/adapters.md`
- Test: `tests/test_adapter_registry.py`

- [ ] **Step 1: `register_adapter(name, factory)` + `load_entry_point_adapters()`**

- [ ] **Step 2: pyproject.toml**

```toml
[project.entry-points."session2memory.adapters"]
# example: mytool = "my_pkg.adapter:MyAdapter"
```

- [ ] **Step 3: Test registers fake adapter via importlib**

- [ ] **Step 4: `docs/adapters.md` with minimal example**

- [ ] **Step 5: Commit**

---

### Task 9: Integration verification

- [ ] **Run:** `uv run pytest -q && uv run ruff check . && uv run mypy src/session2memory`

- [ ] **Manual:** import → review → promote → sync; re-import same date; assert evidence ids stable in DB

- [ ] **Commit:** `chore: verify phase 2 state platform`

---

## Spec coverage (Phase 2 self-review)

| Spec requirement | Task |
|------------------|------|
| session2memory.db | 1–4 |
| Stable evidence/candidate ids | 2–3 |
| Redaction | 5 |
| Supersede/revoke | 6 |
| Sync hash skip | 7 |
| Adapter SDK | 8 |

## Execution

Start only after Phase 1 plan complete. Same execution choice: subagent-driven vs inline.
