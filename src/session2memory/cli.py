import json
import shutil
from collections.abc import Callable
from datetime import date as Date
from pathlib import Path
from typing import Annotated

import typer

from session2memory.adapters import (
    ClaudeAdapter,
    ClaudeDesktopAdapter,
    CodexAdapter,
    CursorAdapter,
    CursorCliAdapter,
    HermesAdapter,
    OpenClawAdapter,
    OpenCodeAdapter,
    QwenAdapter,
)
from session2memory.adapters.registry import load_plugin_adapters
from session2memory.agentic_os_index import AgenticOsIndex
from session2memory.distill import DistillError, distill_reviews
from session2memory.llm_extract import LlmInputMode, SubprocessLlmExtractBackend
from session2memory.llm_extract_mock import MockLlmExtractBackend
from session2memory.memory_lifecycle import (
    MemoryLifecycleError,
    revoke_memory,
    supersede_memory,
)
from session2memory.pipeline import PipelineAdapter, run_pipeline
from session2memory.review import (
    ReviewNotFoundError,
    ReviewStatus,
    approve_review,
    inspect_review,
    list_review_conflicts,
    list_reviews,
    promote_reviews,
    reject_review,
)
from session2memory.review_bulk import BulkFilter, bulk_update_reviews
from session2memory.review_conflicts import ConflictResolve
from session2memory.solidify import SolidifyError, solidify_distill
from session2memory.state.store import StateStore
from session2memory.sync_back import SyncError, sync_workspace_memory
from session2memory.validate import ValidateError, validate_distill

app = typer.Typer(no_args_is_help=True)
review_app = typer.Typer(no_args_is_help=True)
memory_app = typer.Typer(no_args_is_help=True)

AdapterFactory = Callable[[Path], PipelineAdapter]
P0_TOOLS = (
    "codex",
    "claude",
    "claude-desktop",
    "qwen",
    "opencode",
    "cursor",
    "cursor-cli",
    "openclaw",
    "hermes",
)
DEFAULT_SOURCE_ROOTS = {
    "codex": Path("~/.codex/sessions"),
    "claude": Path("~/.claude/projects"),
    "claude-desktop": Path("~/Library/Application Support/Claude"),
    "qwen": Path("~/.qwen"),
    "opencode": Path("~/.local/share/opencode/opencode.db"),
    "cursor": Path("~/.cursor/chats"),
    "cursor-cli": Path("~/.cursor/projects"),
    "openclaw": Path("~/.openclaw/logs"),
    "hermes": Path("~/.hermes/logs"),
}
ADAPTERS: dict[str, AdapterFactory] = {
    "codex": CodexAdapter,
    "claude": ClaudeAdapter,
    "claude-desktop": ClaudeDesktopAdapter,
    "qwen": QwenAdapter,
    "opencode": OpenCodeAdapter,
    "cursor": CursorAdapter,
    "cursor-cli": CursorCliAdapter,
    "openclaw": OpenClawAdapter,
    "hermes": HermesAdapter,
}
TOOL_EXECUTABLES = {
    "codex": ("codex",),
    "claude": ("claude",),
    "claude-desktop": (),
    "qwen": ("qwen",),
    "opencode": ("opencode",),
    "cursor": ("cursor",),
    "cursor-cli": ("cursor-agent", "cursor-cli"),
    "openclaw": ("openclaw",),
    "hermes": ("hermes",),
}
DEFAULT_AGENTIC_OS_ROOT = Path("~/.agentic-os")


@app.callback()
def main() -> None:
    pass


app.add_typer(review_app, name="review")
app.add_typer(memory_app, name="memory")


def _parse_source_root(raw: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for item in raw:
        if "=" not in item:
            raise typer.BadParameter("--source-root must use tool=path")
        tool, path = item.split("=", 1)
        tool_name = tool.strip()
        if tool_name not in P0_TOOLS:
            raise typer.BadParameter(f"Unsupported tool: {tool_name}")
        roots[tool_name] = Path(path).expanduser()
    return roots


def _selected_tools(requested_tools: list[str] | None, roots: dict[str, Path]) -> list[str]:
    selected: list[str] = []
    for tool in requested_tools or sorted(roots) or list(P0_TOOLS):
        if tool not in P0_TOOLS:
            raise typer.BadParameter(f"Unsupported tool: {tool}")
        if tool not in selected:
            selected.append(tool)
    return selected


def _build_llm_backend(
    name: str,
    *,
    command: str | None,
    timeout_seconds: int,
    input_mode: LlmInputMode,
    strict: bool,
) -> SubprocessLlmExtractBackend | MockLlmExtractBackend:
    if name == "mock":
        return MockLlmExtractBackend(items_payload=[])
    if name == "subprocess":
        return SubprocessLlmExtractBackend(
            command=command,
            timeout_seconds=timeout_seconds,
            input_mode=input_mode,
            strict=strict,
        )
    raise typer.BadParameter("Unsupported --llm-backend (use subprocess or mock)")


def _parse_date(value: str) -> str:
    try:
        return Date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise typer.BadParameter("--date must use YYYY-MM-DD") from exc


def _default_source_roots() -> dict[str, Path]:
    return {tool: path.expanduser() for tool, path in DEFAULT_SOURCE_ROOTS.items()}


def _agentic_os_db_path(root: Path) -> Path:
    return root.expanduser() / "agentic-os.db"


def _open_agentic_os_index(
    *,
    root: Path,
    enabled: bool,
) -> tuple[AgenticOsIndex | None, str | None]:
    if not enabled:
        return None, None
    db_path = _agentic_os_db_path(root)
    if not db_path.exists():
        return None, f"agentic-os evidence=missing db={db_path.as_posix()}"
    try:
        return AgenticOsIndex.open(db_path), f"agentic-os evidence=found db={db_path.as_posix()}"
    except OSError as exc:
        return None, f"agentic-os evidence=unreadable db={db_path.as_posix()} error={exc}"


def _build_adapters(tools: list[str], source_roots: dict[str, Path]) -> dict[str, PipelineAdapter]:
    factories = dict(ADAPTERS)
    factories.update(load_plugin_adapters())
    return {tool: factories[tool](source_roots[tool]) for tool in tools}


@app.command("discover")
def discover_sources(
    tool: Annotated[
        list[str] | None, typer.Option("--tool", help="Limit discovery to one or more tools.")
    ] = None,
    source_root: Annotated[
        list[str] | None, typer.Option("--source-root", help="Override source root as tool=path.")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print machine-readable discovery rows.")
    ] = False,
    agentic_os_root: Annotated[
        Path, typer.Option("--agentic-os-root", help="agentic-os state directory.")
    ] = DEFAULT_AGENTIC_OS_ROOT,
    no_agentic_os: Annotated[
        bool, typer.Option("--no-agentic-os", help="Skip agentic-os evidence discovery.")
    ] = False,
) -> None:
    overrides = _parse_source_root(source_root or [])
    source_roots = _default_source_roots()
    source_roots.update(overrides)
    selected_tools = _selected_tools(tool, source_roots)
    _, agentic_os_status = _open_agentic_os_index(root=agentic_os_root, enabled=not no_agentic_os)
    rows: list[dict[str, str | bool | list[str]]] = []
    for tool_name in selected_tools:
        root = source_roots[tool_name]
        executables = TOOL_EXECUTABLES.get(tool_name, ())
        found_executables = [name for name in executables if shutil.which(name)]
        rows.append(
            {
                "tool": tool_name,
                "source_found": root.exists(),
                "source_root": root.as_posix(),
                "executables": found_executables,
                "supported": tool_name in ADAPTERS,
            }
        )
    if json_output:
        payload: dict[str, object] = {"tools": rows}
        if agentic_os_status:
            payload["agentic_os"] = agentic_os_status
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    if agentic_os_status:
        typer.echo(agentic_os_status)
    for row in rows:
        source_status = "found" if row["source_found"] else "missing"
        row_executables = row["executables"] if isinstance(row["executables"], list) else []
        executable_status = ",".join(row_executables) or "missing"
        typer.echo(
            f"{row['tool']} source={source_status} root={row['source_root']} "
            f"executable={executable_status} supported={str(row['supported']).lower()}"
        )


@app.command("import")
def import_sessions(
    date: Annotated[str, typer.Option("--date", help="Date to import in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated HKS-ingestable folder.")],
    tool: Annotated[
        list[str] | None, typer.Option("--tool", help="Limit import to one or more tools.")
    ] = None,
    workspace: Annotated[
        Path | None, typer.Option("--workspace", help="Limit to one workspace path.")
    ] = None,
    source_root: Annotated[
        list[str] | None, typer.Option("--source-root", help="Override source root as tool=path.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Scan and report without writing output.")
    ] = False,
    agentic_os_root: Annotated[
        Path, typer.Option("--agentic-os-root", help="agentic-os state directory.")
    ] = DEFAULT_AGENTIC_OS_ROOT,
    no_agentic_os: Annotated[
        bool, typer.Option("--no-agentic-os", help="Skip agentic-os evidence enrichment.")
    ] = False,
    agentic_os_sessions_only: Annotated[
        bool,
        typer.Option(
            "--agentic-os-sessions-only",
            help="Import only harness logs registered in agentic-os for this date.",
        ),
    ] = False,
    llm_extract: Annotated[
        bool,
        typer.Option("--llm-extract", help="Add optional LLM-extracted review candidates."),
    ] = False,
    llm_backend: Annotated[
        str,
        typer.Option(
            "--llm-backend",
            help="LLM extractor backend (subprocess or mock).",
        ),
    ] = "subprocess",
    llm_cmd: Annotated[
        str | None,
        typer.Option(
            "--llm-cmd",
            help=(
                "Subprocess command for --llm-backend=subprocess. "
                "Falls back to SESSION2MEMORY_LLM_CMD."
            ),
        ),
    ] = None,
    llm_timeout: Annotated[
        int,
        typer.Option("--llm-timeout", help="LLM subprocess timeout in seconds."),
    ] = 120,
    llm_input: Annotated[
        LlmInputMode,
        typer.Option("--llm-input", help="How to pass the prompt to the subprocess."),
    ] = "argument",
    strict_llm: Annotated[
        bool,
        typer.Option("--strict-llm", help="Fail import when LLM extraction fails."),
    ] = False,
    use_state: Annotated[
        bool,
        typer.Option(
            "--use-state/--no-state",
            help="Persist import state in session2memory.db (default: on).",
        ),
    ] = True,
    state_db: Annotated[
        Path | None,
        typer.Option("--state-db", help="Override state database path."),
    ] = None,
) -> None:
    parsed_date = _parse_date(date)
    overrides = _parse_source_root(source_root or [])
    selected_tools = _selected_tools(tool, overrides)
    source_roots = _default_source_roots()
    source_roots.update(overrides)
    selected_source_roots = {tool_name: source_roots[tool_name] for tool_name in selected_tools}
    agentic_os_index, agentic_os_status = _open_agentic_os_index(
        root=agentic_os_root,
        enabled=not no_agentic_os,
    )
    llm_backend_instance = (
        _build_llm_backend(
            llm_backend,
            command=llm_cmd,
            timeout_seconds=llm_timeout,
            input_mode=llm_input,
            strict=strict_llm,
        )
        if llm_extract
        else None
    )
    state_store: StateStore | None = None
    if use_state and not dry_run:
        db_path = (state_db or (output / "session2memory.db")).expanduser()
        state_store = StateStore.open(db_path, output_dir=output)
    try:
        session_count, candidate_count = run_pipeline(
            adapters=_build_adapters(selected_tools, source_roots),
            output_dir=output,
            date=parsed_date,
            source_roots=selected_source_roots,
            dry_run=dry_run,
            workspace=workspace,
            agentic_os_index=agentic_os_index,
            agentic_os_sessions_only=agentic_os_sessions_only,
            llm_backend=llm_backend_instance,
            state_store=state_store,
        )
    finally:
        if state_store is not None:
            state_store.close()
    typer.echo(
        f"date={parsed_date} tools={len(selected_tools)} sessions={session_count} "
        f"written={0 if dry_run else 1} candidates={candidate_count}"
    )


@app.command("sync", help="Write promoted memories back into harness context files.")
def sync_back(
    workspace: Annotated[
        Path,
        typer.Option("--workspace", help="Repo root to receive harness context updates."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="Generated session-memory folder with memories/."),
    ],
    target: Annotated[
        list[str] | None,
        typer.Option(
            "--target",
            help=(
                "Harness target to update (repeatable). "
                "Defaults: agents, claude, cursor. "
                "Also: codex, openclaw, hermes."
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report planned writes without changing files.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Write even when sync body hash is unchanged.")
    ] = False,
    since_last_sync: Annotated[
        bool,
        typer.Option(
            "--since-last-sync",
            help="Skip targets whose rendered sync body hash is unchanged.",
        ),
    ] = False,
) -> None:
    try:
        result = sync_workspace_memory(
            output_dir=output,
            workspace=workspace,
            targets=target,
            dry_run=dry_run,
            force=force,
            since_last_sync=since_last_sync,
        )
    except SyncError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for write in result.writes:
        action = "would_write" if dry_run else "wrote"
        status = "created" if write.created else ("updated" if write.changed else "unchanged")
        typer.echo(
            f"workspace_id={result.workspace_id} target={write.target} "
            f"path={write.path.as_posix()} entries={write.entry_count} "
            f"{action}={status}"
        )


@app.command("promote", help="Legacy alias for `review promote`.")
def promote(
    date: Annotated[str, typer.Option("--date", help="Date to promote in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
) -> None:
    parsed_date = _parse_date(date)
    result = promote_reviews(output_dir=output, date=parsed_date, resolve=None)
    if result.blocked:
        raise typer.Exit(code=2)
    typer.echo(
        f"date={parsed_date} reviewed={result.reviewed} promoted={result.promoted} "
        f"skipped_duplicate={result.skipped_duplicate} "
        f"skipped_conflict={result.skipped_conflict}"
    )


@app.command("distill")
def distill(
    date: Annotated[str, typer.Option("--date", help="Date to distill in YYYY-MM-DD format.")],
    output: Annotated[
        Path,
        typer.Option("--output", help="Generated session-memory folder."),
    ] = Path("./out/session-memory"),
) -> None:
    parsed_date = _parse_date(date)
    try:
        result = distill_reviews(output_dir=output, date=parsed_date)
    except DistillError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"date={parsed_date} review_rows={result.review_rows} "
        f"approved_reviews={result.approved_reviews} candidates={result.candidates} "
        f"distill={result.distill_dir.as_posix()}"
    )


@app.command("validate")
def validate_command(
    distill: Annotated[Path, typer.Option("--distill", help="Distill output directory.")],
) -> None:
    try:
        result = validate_distill(distill)
    except ValidateError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"distill={result.distill_dir.as_posix()} validated={result.validated} "
        f"pass={result.passed} needs_review={result.needs_review} blocked={result.blocked}"
    )


@app.command("solidify")
def solidify(
    distill: Annotated[Path, typer.Option("--distill", help="Distill output directory.")],
) -> None:
    try:
        result = solidify_distill(distill)
    except SolidifyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"distill={result.distill_dir.as_posix()} solidified={result.solidified} "
        f"ready_for_review={result.ready_for_review} "
        f"needs_human_review={result.needs_human_review} blocked={result.blocked}"
    )


@review_app.command("list")
def review_list(
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
    status: Annotated[
        ReviewStatus | None,
        typer.Option("--status", help="Limit rows to one review status."),
    ] = None,
) -> None:
    parsed_date = _parse_date(date)
    for row in list_reviews(output_dir=output, date=parsed_date, status=status):
        durable = "durable" if row.get("durable_suggestion") is True else "daily"
        source = _review_source_label(row)
        extraction = row.get("extraction", "marker")
        confidence = row.get("confidence")
        conf = f" confidence={confidence}" if confidence is not None else ""
        typer.echo(
            f"{row.get('id', '')} {row.get('status', '')} {durable} "
            f"extraction={extraction}{conf} "
            f"{row.get('kind', '')} {row.get('workspace_id', '')} "
            f"{row.get('evidence_id', '')} {source}{row.get('text', '')}"
        )


@review_app.command("ui")
def review_ui(
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
) -> None:
    parsed_date = _parse_date(date)
    try:
        from session2memory.review_ui import ReviewAppConfig, run_review_ui
    except ImportError as exc:
        raise typer.BadParameter("Install UI dependencies: uv sync --group ui") from exc
    run_review_ui(ReviewAppConfig(output_dir=output, date=parsed_date))


@review_app.command("web")
def review_web(
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
    host: Annotated[str, typer.Option("--host", help="HTTP bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="HTTP bind port.")] = 8765,
) -> None:
    parsed_date = _parse_date(date)
    from session2memory.review_web import ReviewWebConfig, run_review_web

    run_review_web(ReviewWebConfig(output_dir=output, date=parsed_date), host=host, port=port)


@review_app.command("inspect")
def review_inspect(
    review_id: Annotated[str, typer.Argument(help="Review row id.")],
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
) -> None:
    parsed_date = _parse_date(date)
    try:
        inspection = inspect_review(output_dir=output, date=parsed_date, review_id=review_id)
    except ReviewNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc

    row = inspection.row
    durable = "durable" if row.get("durable_suggestion") is True else "daily"
    typer.echo(
        f"id={row.get('id', '')} status={row.get('status', '')} durable={durable} "
        f"kind={row.get('kind', '')} workspace={row.get('workspace_id', '')} "
        f"evidence={row.get('evidence_id', '')}"
    )
    typer.echo("candidate:")
    typer.echo(str(row.get("text", "")))
    typer.echo("evidence:")
    evidence = inspection.evidence
    if evidence is None:
        typer.echo("missing")
    else:
        typer.echo(
            f"tool={evidence.get('tool', '')} session={evidence.get('session_id', '')} "
            f"source={evidence.get('source_path', '')} "
            f"lines={evidence.get('message_start', '')}-{evidence.get('message_end', '')} "
            f"digest={evidence.get('digest', '')}"
        )
    typer.echo("preview:")
    typer.echo(inspection.preview)


@review_app.command("approve")
def review_approve(
    review_id: Annotated[str, typer.Argument(help="Review row id.")],
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
    note: Annotated[str | None, typer.Option("--note", help="Reviewer note.")] = None,
    durable: Annotated[
        bool,
        typer.Option("--durable", help="Mark this row as durable memory eligible."),
    ] = False,
) -> None:
    parsed_date = _parse_date(date)
    try:
        result = approve_review(
            output_dir=output,
            date=parsed_date,
            review_id=review_id,
            note=note,
            durable=durable,
        )
    except ReviewNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"date={parsed_date} id={result.review_id} status={result.status}")


@review_app.command("reject")
def review_reject(
    review_id: Annotated[str, typer.Argument(help="Review row id.")],
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
    note: Annotated[str | None, typer.Option("--note", help="Reviewer note.")] = None,
) -> None:
    parsed_date = _parse_date(date)
    try:
        result = reject_review(
            output_dir=output,
            date=parsed_date,
            review_id=review_id,
            note=note,
        )
    except ReviewNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"date={parsed_date} id={result.review_id} status={result.status}")


@review_app.command("approve-bulk")
def review_approve_bulk(
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
    status: Annotated[
        ReviewStatus, typer.Option("--status", help="Only update rows in this status.")
    ] = "pending",
    kind: Annotated[list[str] | None, typer.Option("--kind", help="Filter by memory kind.")] = None,
    workspace_id: Annotated[
        str | None, typer.Option("--workspace-id", help="Filter by workspace id.")
    ] = None,
    tool: Annotated[str | None, typer.Option("--tool", help="Filter by source tool.")] = None,
    review_id: Annotated[
        list[str] | None, typer.Option("--id", help="Limit to explicit review ids.")
    ] = None,
    durable: Annotated[
        bool,
        typer.Option("--durable", help="Mark matched rows as durable memory eligible."),
    ] = False,
    note: Annotated[str | None, typer.Option("--note", help="Reviewer note.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report without writing.")] = False,
) -> None:
    parsed_date = _parse_date(date)
    result = bulk_update_reviews(
        output_dir=output,
        date=parsed_date,
        target_status="approved",
        filters=BulkFilter(
            status=status,
            kinds=frozenset(kind) if kind else None,
            workspace_id=workspace_id,
            tool=tool,
            review_ids=frozenset(review_id) if review_id else None,
        ),
        durable=True if durable else None,
        note=note,
        dry_run=dry_run,
    )
    typer.echo(
        f"date={parsed_date} matched={result.matched} updated={result.updated} "
        f"skipped={result.skipped}"
    )


@review_app.command("reject-bulk")
def review_reject_bulk(
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
    status: Annotated[
        ReviewStatus, typer.Option("--status", help="Only update rows in this status.")
    ] = "pending",
    kind: Annotated[list[str] | None, typer.Option("--kind", help="Filter by memory kind.")] = None,
    workspace_id: Annotated[
        str | None, typer.Option("--workspace-id", help="Filter by workspace id.")
    ] = None,
    tool: Annotated[str | None, typer.Option("--tool", help="Filter by source tool.")] = None,
    review_id: Annotated[
        list[str] | None, typer.Option("--id", help="Limit to explicit review ids.")
    ] = None,
    note: Annotated[str | None, typer.Option("--note", help="Reviewer note.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report without writing.")] = False,
) -> None:
    parsed_date = _parse_date(date)
    result = bulk_update_reviews(
        output_dir=output,
        date=parsed_date,
        target_status="rejected",
        filters=BulkFilter(
            status=status,
            kinds=frozenset(kind) if kind else None,
            workspace_id=workspace_id,
            tool=tool,
            review_ids=frozenset(review_id) if review_id else None,
        ),
        durable=None,
        note=note,
        dry_run=dry_run,
    )
    typer.echo(
        f"date={parsed_date} matched={result.matched} updated={result.updated} "
        f"skipped={result.skipped}"
    )


@review_app.command("conflicts")
def review_conflicts(
    date: Annotated[str, typer.Option("--date", help="Date to review in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    parsed_date = _parse_date(date)
    groups = list_review_conflicts(output_dir=output, date=parsed_date)
    if json_output:
        typer.echo(json.dumps(groups, ensure_ascii=False))
        return
    if not groups:
        typer.echo(f"date={parsed_date} conflicts=0")
        return
    typer.echo(f"date={parsed_date} conflicts={len(groups)}")
    for group in groups:
        typer.echo(
            f"{group['conflict_id']} workspace={group['workspace_id']} kind={group['kind']} "
            f"reviews={','.join(group['review_ids'])}"
        )


@review_app.command("promote")
def review_promote(
    date: Annotated[str, typer.Option("--date", help="Date to promote in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
    resolve: Annotated[
        ConflictResolve | None,
        typer.Option("--resolve", help="How to resolve promote conflicts."),
    ] = None,
) -> None:
    parsed_date = _parse_date(date)
    result = promote_reviews(output_dir=output, date=parsed_date, resolve=resolve)
    if result.blocked:
        typer.echo(
            f"date={parsed_date} reviewed={result.reviewed} promoted=0 "
            f"conflicts={result.conflicts}",
            err=True,
        )
        raise typer.Exit(code=2)
    typer.echo(
        f"date={parsed_date} reviewed={result.reviewed} promoted={result.promoted} "
        f"skipped_duplicate={result.skipped_duplicate} "
        f"skipped_conflict={result.skipped_conflict}"
    )


@memory_app.command("revoke")
def memory_revoke(
    memory_entry_id: Annotated[str, typer.Argument(help="Memory entry id to revoke.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
) -> None:
    try:
        result = revoke_memory(output_dir=output, memory_entry_id=memory_entry_id)
    except MemoryLifecycleError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"id={result.memory_entry_id} status={result.status} "
        f"exported_workspaces={len(result.exported_workspaces)} "
        f"synced_targets={result.synced_targets}"
    )


@memory_app.command("supersede")
def memory_supersede(
    old_id: Annotated[str, typer.Option("--old", help="Superseded memory entry id.")],
    new_id: Annotated[str, typer.Option("--new", help="Replacement memory entry id.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
) -> None:
    try:
        result = supersede_memory(output_dir=output, old_id=old_id, new_id=new_id)
    except MemoryLifecycleError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"id={result.memory_entry_id} status={result.status} "
        f"exported_workspaces={len(result.exported_workspaces)} "
        f"synced_targets={result.synced_targets}"
    )


def _review_source_label(row: dict[str, object]) -> str:
    source = row.get("source")
    if not isinstance(source, dict):
        return ""
    tool = source.get("tool")
    session_id = source.get("session_id")
    message_start = source.get("message_start")
    message_end = source.get("message_end")
    if not tool or not session_id or not message_start or not message_end:
        return ""
    return f"source={tool} session={session_id} lines={message_start}-{message_end} "
