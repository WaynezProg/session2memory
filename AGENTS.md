# Repository Guidelines

## Project Structure & Module Organization

`session2memory` is a Python package that compiles local coding-agent sessions into HKS-ingestable memory output. Source code lives under `src/session2memory/`, with the Typer CLI entrypoint in `cli.py`. Adapter-specific parsing belongs in `src/session2memory/adapters/`; shared data contracts live in `models.py`, `pipeline.py`, `writer.py`, and related modules.

Tests live in `tests/`. Fixtures are under `tests/fixtures/`, golden output under `tests/golden/`, and HKS compatibility coverage under `tests/integration/`. The packaged OpenClaw skill is in `skills/session2memory/`. Generated local output belongs in `out/` and should not be treated as source.

## Build, Test, and Development Commands

- `uv run session2memory import --date 2026-05-22 --workspace "$PWD" --output ./out/session-memory --dry-run`: inspect what would be scanned without writing output.
- `uv run session2memory import --date 2026-05-22 --output ./out/session-memory`: generate daily logs, evidence, and review queues.
- `uv run pytest -q`: run the full test suite.
- `uv run pytest tests/integration/test_hks_compatibility.py -q`: verify HKS-facing output contracts.
- `uv run ruff check .`: run lint and import-order checks.
- `uv run mypy src/session2memory`: run strict type checks.

## Coding Style & Naming Conventions

Target Python 3.12. Keep code typed and compatible with strict mypy. Ruff is configured for `B`, `E`, `F`, `I`, and `UP` rules with a 100-character line limit.

Use snake_case for modules, functions, variables, and test names. Use PascalCase for classes and dataclasses. Keep adapter code source-specific; do not add cross-adapter conditionals unless the behavior is truly shared.

## Testing Guidelines

Add focused unit tests beside the behavior being changed, and add fixture or golden updates only when the serialized output contract intentionally changes. Test files should follow `tests/test_*.py`; integration tests belong in `tests/integration/`.

Run `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src/session2memory` before handing off changes.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional Commit-style subjects such as `feat: add review CLI workflow`, `docs: clarify session2memory command workflow`, and `chore: bump version to 0.1.1`. Keep the subject specific and imperative.

Pull requests should describe the user-facing behavior change, list verification commands run, and call out any fixture, golden, or generated-output contract changes. Link the related issue or task when available.

## Agent-Specific Instructions

Do not ingest raw session stores directly into HKS. Generate `out/session-memory` first, then ingest that generated source from the HKS repo. Preserve provenance: raw paths belong in `evidence/index.jsonl`, while daily and review output should expose compact evidence identifiers only.
