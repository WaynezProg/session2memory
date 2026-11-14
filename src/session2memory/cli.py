import json
import os
import re
import shlex
import shutil
import sys
from collections.abc import Callable
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from session2memory.adapters import (
    AntigravityAdapter,
    ClaudeAdapter,
    ClaudeDesktopAdapter,
    CodexAdapter,
    CursorAdapter,
    CursorCliAdapter,
    GrokAdapter,
    HermesAdapter,
    MuseAdapter,
    OpenClawAdapter,
    OpenClawNativeAdapter,
    OpenCodeAdapter,
    QwenAdapter,
)
from session2memory.adapters.codex import discover_codex_homes
from session2memory.adapters.registry import load_plugin_adapters
from session2memory.agentic_os_index import AgenticOsIndex
from session2memory.auto import console
from session2memory.auto.run import ALL_STEPS, AutoConfig, run_auto
from session2memory.auto.run import DB_NAME as AUTO_DB_NAME
from session2memory.auto.scan import ScanSources
from session2memory.auto.scope import Reader, forced_reader, openclaw_roots
from session2memory.auto.semantic import (
    embed_model_name,
    load_embedder,
    load_reranker,
    semantic_search,
)
from session2memory.auto.store import AutoStore
from session2memory.distill import DistillError, distill_reviews
from session2memory.handoff import (
    DEFAULT_MAX_CHARS,
    MIN_MAX_CHARS,
    build_packet,
    find_message,
    native_summary,
    parse_reference,
)
from session2memory.handoff_find import (
    DEFAULT_HANDOFF_TOOLS,
    FINDERS,
    FoundSession,
    SessionSelectionError,
    find_session,
    latest_session,
    select_session,
    split_session_spec,
)
from session2memory.hook_install import AGENT_HOOKS, DEFAULT_SEEN_DIR, update_settings
from session2memory.llm_extract import LlmInputMode, SubprocessLlmExtractBackend
from session2memory.llm_extract_mock import MockLlmExtractBackend
from session2memory.memory_lifecycle import (
    MemoryLifecycleError,
    revoke_memory,
    supersede_memory,
)
from session2memory.models import SourceHome
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
from session2memory.session_start import (
    DEFAULT_MAX_AGE_HOURS,
    HookConfig,
    first_time,
    hook_output,
    is_resume,
    payload_session_id,
    release,
    run_hook,
    served,
)
from session2memory.setup import render as render_setup
from session2memory.setup import run_setup
from session2memory.solidify import SolidifyError, solidify_distill
from session2memory.state.store import StateStore
from session2memory.sync_back import SyncError, sync_workspace_memory
from session2memory.validate import ValidateError, validate_distill
from session2memory.worklog import (
    format_missing_state_db_error,
    generate_worklog,
    resolve_state_db_path,
    resolve_worklog_range,
)

app = typer.Typer(no_args_is_help=True)
auto_app = typer.Typer(no_args_is_help=True, help="Unattended session maintenance.")
app.add_typer(auto_app, name="auto")
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
# Supported, but only imported when named with --tool (never part of the default set):
# openclaw-native reads agent-private session stores, the others are newer sources.
OPT_IN_TOOLS = ("openclaw-native", "grok", "muse", "antigravity")
SUPPORTED_TOOLS = P0_TOOLS + OPT_IN_TOOLS
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
    "openclaw-native": Path("~/.openclaw/agents"),
    "grok": Path("~/.grok/sessions"),
    "muse": Path("~/.local/share/muse/sessions"),
    "antigravity": Path("~/.gemini/antigravity-cli"),
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
    "openclaw-native": OpenClawNativeAdapter,
    "grok": GrokAdapter,
    "muse": MuseAdapter,
    "antigravity": AntigravityAdapter,
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
    "openclaw-native": ("openclaw",),
    "grok": ("grok",),
    "muse": ("muse",),
    "antigravity": ("agy",),
}
DEFAULT_AGENTIC_OS_ROOT = Path("~/.agentic-os")


@app.command("handoff", help="Print a handoff packet for the latest session of a workspace.")
def handoff_packet(
    workspace: Annotated[
        Path, typer.Option("--workspace", help="Workspace whose latest session is handed off.")
    ],
    from_tool: Annotated[
        list[str] | None,
        typer.Option(
            "--from-tool",
            help="Tool(s) to take the session from. Default: the latest across "
            + ", ".join(DEFAULT_HANDOFF_TOOLS)
            + ".",
        ),
    ] = None,
    max_chars: Annotated[
        int, typer.Option("--max-chars", help="Upper bound on the packet size, in characters.")
    ] = DEFAULT_MAX_CHARS,
    source_root: Annotated[
        list[str] | None, typer.Option("--source-root", help="Override source root as tool=path.")
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the packet to this file instead of stdout.")
    ] = None,
    session: Annotated[
        str | None,
        typer.Option(
            "--session",
            help="Hand off this session instead of the latest one: a session id, a unique "
            "prefix of it, or <tool>:<id>.",
        ),
    ] = None,
) -> None:
    """Read-only: nothing is imported, no state DB or hook is involved."""
    if max_chars < MIN_MAX_CHARS:
        raise typer.BadParameter(f"--max-chars must be at least {MIN_MAX_CHARS}")
    named_tool = split_session_spec(session)[0] if session else None
    roots = _handoff_roots(from_tool or ([named_tool] if named_tool else None), source_root)
    if session is not None:
        try:
            found: FoundSession | None = select_session(roots, workspace.expanduser(), session)
        except SessionSelectionError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    else:
        found = latest_session(roots, workspace.expanduser())
    if found is None:
        typer.echo(
            f"no session with conversation found for {workspace} in: {', '.join(roots)}", err=True
        )
        raise typer.Exit(code=1)
    packet = build_packet(found.record, native_summary(found.record), max_chars=max_chars)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(packet + "\n", encoding="utf-8")
        typer.echo(f"wrote {len(packet)} chars to {out.as_posix()}")
        return
    typer.echo(packet)


@app.command("handoff-show", help="Print the full source message behind a packet reference.")
def handoff_show(
    reference: Annotated[
        str, typer.Argument(help="Evidence reference: <tool>:<session id>#<locator>.")
    ],
    workspace: Annotated[
        Path, typer.Option("--workspace", help="Workspace the session belongs to.")
    ],
    source_root: Annotated[
        list[str] | None, typer.Option("--source-root", help="Override source root as tool=path.")
    ] = None,
) -> None:
    try:
        tool, session_id, locator = parse_reference(reference)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    roots = _handoff_roots([tool], source_root)
    record = find_session(tool, roots[tool], workspace.expanduser(), session_id)
    message = find_message(record, locator) if record is not None else None
    if message is None:
        missing = "session" if record is None else "message"
        typer.echo(f"{missing} not found for {reference}", err=True)
        raise typer.Exit(code=1)
    typer.echo(message.text)


def _handoff_roots(tools: list[str] | None, source_root: list[str] | None) -> dict[str, Path]:
    source_roots = _default_source_roots()
    source_roots.update(_parse_source_root(source_root or []))
    selected = tools or list(DEFAULT_HANDOFF_TOOLS)
    for tool in selected:
        if tool not in FINDERS:
            raise typer.BadParameter(
                f"handoff does not support tool: {tool} (supported: {', '.join(sorted(FINDERS))})"
            )
    return {tool: source_roots[tool] for tool in selected}


DEFAULT_HANDOFF_LEDGER = Path("~/.local/share/session2memory/handoff-ledger.json")
DEFAULT_CODEX_MEMORY_ROOT = Path("~/.codex/memories_v2")
DEFAULT_HOOK_MAX_CHARS = 3000


@app.command(
    "session-start-hook",
    help="SessionStart hook: add other agents' memory index and a one-time handoff.",
)
def session_start_hook(
    agent: Annotated[
        str,
        typer.Option("--agent", help="Agent this hook runs in (see hook-install)."),
    ] = "claude",
    event: Annotated[
        str, typer.Option("--event", help="Hook event this command is attached to.")
    ] = "SessionStart",
    once: Annotated[
        bool,
        typer.Option(
            "--once",
            help="For an event that repeats within a session: only its first firing injects.",
        ),
    ] = False,
    observe: Annotated[
        bool,
        typer.Option(
            "--observe",
            help="Inject nothing; only note a resumed session so that --once stays silent for it.",
        ),
    ] = False,
    seen_dir: Annotated[
        Path, typer.Option("--seen-dir", help="Where --once records the sessions it served.")
    ] = Path(DEFAULT_SEEN_DIR),
    max_chars: Annotated[
        int, typer.Option("--max-chars", help="Upper bound on the injected text.")
    ] = DEFAULT_HOOK_MAX_CHARS,
    max_age_hours: Annotated[
        float,
        typer.Option("--max-age-hours", help="Ignore a previous session older than this."),
    ] = DEFAULT_MAX_AGE_HOURS,
    include_automated: Annotated[
        bool,
        typer.Option(
            "--include-automated",
            help="Also run for headless (SDK) sessions and temporary directories.",
        ),
    ] = False,
    memory: Annotated[
        bool, typer.Option("--memory/--no-memory", help="Add the memory index.")
    ] = True,
    handoff: Annotated[
        bool, typer.Option("--handoff/--no-handoff", help="Add the one-time handoff.")
    ] = True,
    ledger: Annotated[
        Path, typer.Option("--ledger", help="File recording which handoffs were delivered.")
    ] = DEFAULT_HANDOFF_LEDGER,
    codex_memory_root: Annotated[
        Path, typer.Option("--codex-memory-root", help="Codex memory directory.")
    ] = DEFAULT_CODEX_MEMORY_ROOT,
    source_root: Annotated[
        list[str] | None, typer.Option("--source-root", help="Override source root as tool=path.")
    ] = None,
    explain: Annotated[
        bool, typer.Option("--explain", help="Print the decision notes to stderr.")
    ] = False,
    auto_store: Annotated[
        Path, typer.Option("--auto-store", help="Store of `auto run`, for distilled memory.")
    ] = Path("~/.local/share/session2memory/store"),
) -> None:
    """Reads the hook payload on stdin and prints the hook JSON. Never fails the session."""
    claimed = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        if observe:
            session = payload_session_id(payload)
            if session and is_resume(payload):
                served(seen_dir.expanduser(), agent, session)
            typer.echo("{}")
            return
        if once:
            claimed = payload_session_id(payload)
            if not first_time(seen_dir.expanduser(), agent, claimed):
                typer.echo("{}")
                return
            # The session's first tool call stands for its start, unless the agent says
            # what this session is (a resumed one gets nothing, as everywhere else).
            payload = {"source": "startup", **payload}
        source_roots = _default_source_roots()
        source_roots.update(_parse_source_root(source_root or []))
        config = HookConfig(
            agent=agent,
            session_roots={
                tool: source_roots[tool] for tool in ("claude", "codex", "grok", "antigravity")
            },
            codex_memory_root=codex_memory_root.expanduser(),
            claude_projects_root=source_roots["claude"],
            ledger_path=ledger.expanduser(),
            max_chars=max_chars,
            max_age_hours=max_age_hours,
            include_automated=include_automated,
            memory=memory,
            handoff=handoff,
            auto_db=auto_store.expanduser() / AUTO_DB_NAME,
            openclaw_state_dir=_openclaw_state_dir(),
        )
        result = run_hook(config, payload, os.environ)
    except Exception as exc:  # noqa: BLE001 - a hook must never break the session
        if claimed:
            release(seen_dir.expanduser(), agent, claimed)
        if explain:
            typer.echo(f"session-start-hook: {type(exc).__name__}: {exc}", err=True)
        typer.echo("{}")
        return
    if claimed:
        served(seen_dir.expanduser(), agent, claimed)
    if explain:
        for note in result.notes:
            typer.echo(note, err=True)
    style = AGENT_HOOKS[agent].output if agent in AGENT_HOOKS else "claude"
    typer.echo(hook_output(result.context, event, style=style))


@app.command("hook-install", help="Add or remove the memory hook in an agent's settings.")
def hook_install(
    agent: Annotated[
        str, typer.Option("--agent", help="claude, codex, grok, muse, antigravity or cursor.")
    ] = "claude",
    settings: Annotated[
        Path | None,
        typer.Option("--settings", help="Hook settings file to change (default: the agent's)."),
    ] = None,
    remove: Annotated[bool, typer.Option("--remove", help="Remove the hook instead.")] = False,
    apply: Annotated[
        bool, typer.Option("--apply", help="Write the change. Without it only the plan is shown.")
    ] = False,
) -> None:
    try:
        if agent not in AGENT_HOOKS:
            raise ValueError(f"unknown agent {agent!r}; expected one of {', '.join(AGENT_HOOKS)}")
        target = settings or Path(AGENT_HOOKS[agent].settings)
        plan = update_settings(target.expanduser(), agent=agent, remove=remove, apply=apply)
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"settings: {plan.settings_path.as_posix()}")
    typer.echo(f"action: {plan.action}" + ("" if apply or plan.action == "none" else " (dry run)"))
    typer.echo(f"command: {plan.command}")
    if plan.backup_path is not None:
        typer.echo(f"backup: {plan.backup_path.as_posix()}")


DEFAULT_AUTO_STORE = Path("~/.local/share/session2memory/store")


@app.command(
    "setup",
    help=(
        "After installing: add the memory hook to every agent on this machine and "
        "schedule the nightly run. Shows the plan; --apply makes the changes."
    ),
)
def setup_command(
    apply: Annotated[
        bool, typer.Option("--apply", help="Make the changes. Without it only the plan is shown.")
    ] = False,
    remove: Annotated[
        bool, typer.Option("--remove", help="Undo: take the hook and the nightly job out again.")
    ] = False,
    hooks: Annotated[
        bool, typer.Option("--hooks/--no-hooks", help="The memory hook in each agent.")
    ] = True,
    schedule: Annotated[
        bool, typer.Option("--schedule/--no-schedule", help="The nightly run.")
    ] = True,
    hour: Annotated[
        int, typer.Option("--hour", min=0, max=23, help="When the nightly run starts.")
    ] = 5,
    minute: Annotated[int, typer.Option("--minute", min=0, max=59)] = 30,
    compress: Annotated[
        bool,
        typer.Option(
            "--compress",
            help=(
                "Also compress old rollouts in OpenClaw agents' Codex homes each night. "
                "Off by default: it is the one step that replaces files in place."
            ),
        ),
    ] = False,
    store: Annotated[
        Path, typer.Option("--store", help="Directory holding the persistent state.")
    ] = DEFAULT_AUTO_STORE,
    json_output: Annotated[bool, typer.Option("--json", help="Print the plan as JSON.")] = False,
) -> None:
    plan = run_setup(
        store=store, apply=apply, remove=remove, hooks=hooks, schedule=schedule,
        hour=hour, minute=minute, compress=compress,
    )  # fmt: skip
    if json_output:
        typer.echo(json.dumps(plan.as_dict(), ensure_ascii=False, indent=1))
    else:
        typer.echo(render_setup(plan))
    if plan.failed:
        raise typer.Exit(code=1)


def _auto_config(
    *,
    store: Path,
    steps: list[str] | None,
    since_days: float,
    rescan: bool,
    max_llm_calls: int,
    max_reconcile_calls: int,
    curate_scope: list[str] | None,
    llm_cmd: str | None,
    index_cmd: str | None,
    include_openclaw: bool,
    dry_run: bool,
) -> AutoConfig:
    roots = _default_source_roots()
    state_dir = _openclaw_state_dir()
    homes = tuple(_codex_homes(roots, include_openclaw=include_openclaw))
    sources = ScanSources(
        codex_homes=homes,
        claude_root=roots["claude"],
        grok_root=roots["grok"],
        muse_root=roots["muse"],
        antigravity_root=roots["antigravity"],
        cursor_root=roots["cursor"],
        cursor_cli_root=roots["cursor-cli"],
        openclaw_agents_root=roots["openclaw-native"] if include_openclaw else None,
        codex_memory_root=DEFAULT_CODEX_MEMORY_ROOT.expanduser(),
        openclaw_roots=openclaw_roots(state_dir),
    )
    for step in steps or []:
        if step not in ALL_STEPS:
            raise typer.BadParameter(f"unknown step {step}; choose from {', '.join(ALL_STEPS)}")
    return AutoConfig(
        store_dir=store.expanduser(),
        sources=sources,
        compress_homes=tuple(home for home in homes if home.home_id != "codex@main"),
        steps=tuple(steps) if steps else ALL_STEPS,
        since_days=since_days,
        rescan=rescan,
        max_llm_calls=max_llm_calls,
        max_reconcile_calls=max_reconcile_calls,
        curate_scopes=tuple(curate_scope) if curate_scope else None,
        llm_command=llm_cmd,
        index_command=tuple(shlex.split(index_cmd)) if index_cmd else (),
        dry_run=dry_run,
    )


def _reader(requested: str) -> Reader:
    """The reader this process may act as; an OpenClaw agent cannot widen its own scope."""
    if requested not in ("coding", "openclaw"):
        raise typer.BadParameter("--reader must be coding or openclaw")
    forced = forced_reader(os.environ, Path.cwd(), _openclaw_state_dir())
    return forced or ("openclaw" if requested == "openclaw" else "coding")


@auto_app.command("run", help="Scan, compress/archive, curate memory, export and index once.")
def auto_run(
    store: Annotated[
        Path, typer.Option("--store", help="Directory holding the persistent state.")
    ] = DEFAULT_AUTO_STORE,
    step: Annotated[
        list[str] | None,
        typer.Option("--step", help=f"Run only these steps ({', '.join(ALL_STEPS)})."),
    ] = None,
    since_days: Annotated[
        float, typer.Option("--since-days", help="How far back the first scan looks.")
    ] = 30.0,
    rescan: Annotated[
        bool,
        typer.Option(
            "--rescan",
            help=(
                "Scan the whole --since-days window again instead of from the last scan, "
                "e.g. after a new kind of session store became readable."
            ),
        ),
    ] = False,
    max_llm_calls: Annotated[
        int, typer.Option("--max-llm-calls", help="Upper bound on curation calls in this run.")
    ] = 20,
    max_reconcile_calls: Annotated[
        int,
        typer.Option(
            "--max-reconcile-calls",
            help=(
                "Upper bound on calls that look for contradicting or repeated memories "
                "(one per project whose memory changed); 0 turns the step off."
            ),
        ),
    ] = 5,
    curate_scope: Annotated[
        list[str] | None,
        typer.Option(
            "--curate-scope",
            help=(
                "Only curate sessions of this scope (coding or openclaw; repeatable). "
                "Default: every scope. Other sessions stay registered and pending."
            ),
        ),
    ] = None,
    llm_cmd: Annotated[
        str | None,
        typer.Option("--llm-cmd", help="Curation command; the prompt is written to its stdin."),
    ] = None,
    index_cmd: Annotated[
        str | None,
        typer.Option(
            "--index-cmd",
            help="Command run after export; {memory_dir} is replaced by the exported tree.",
        ),
    ] = None,
    include_openclaw: Annotated[
        bool,
        typer.Option(
            "--openclaw/--no-openclaw", help="Include OpenClaw agents' own sessions and homes."
        ),
    ] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would be done; change no files.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print the full report.")] = False,
    save_report: Annotated[
        bool,
        typer.Option(
            "--save-report",
            help=(
                "Write the report to <store>/logs/run-<time>.json (the newest 60 are kept) "
                "and print only where it is, unless --json is given; for scheduled runs."
            ),
        ),
    ] = False,
) -> None:
    if curate_scope and set(curate_scope) - {"coding", "openclaw"}:
        raise typer.BadParameter("--curate-scope must be coding or openclaw")
    config = _auto_config(
        store=store,
        steps=step,
        since_days=since_days,
        rescan=rescan,
        max_llm_calls=max_llm_calls,
        max_reconcile_calls=max_reconcile_calls,
        curate_scope=curate_scope,
        llm_cmd=llm_cmd,
        index_cmd=index_cmd,
        include_openclaw=include_openclaw,
        dry_run=dry_run,
    )
    report = run_auto(config)
    if save_report and not dry_run:
        saved = _save_report(store.expanduser(), report)
        if not json_output:
            typer.echo(f"report={saved.as_posix()}")
            return
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=1))
        return
    for key, value in report.items():
        typer.echo(f"{key}: {json.dumps(value, ensure_ascii=False)}")


REPORTS_KEPT = 60
# Reports of scheduled runs, as this command and scripts/nightly-auto.sh name them. Other
# files in the folder (a report someone saved under another name) are never removed.
_SCHEDULED_REPORT = re.compile(r"run-\d{8}-\d{6}(-\d+)?\.json")


def _save_report(
    store_dir: Path, report: dict[str, object], *, now: datetime | None = None
) -> Path:
    """Keep a run's report beside the store, and only the most recent scheduled ones."""
    logs = store_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stem = f"run-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    path = logs / f"{stem}.json"
    extra = 0
    while path.exists():  # a second run within the same second keeps its own report
        extra += 1
        path = logs / f"{stem}-{extra}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    scheduled = [one for one in logs.glob("run-*.json") if _SCHEDULED_REPORT.fullmatch(one.name)]
    scheduled.sort(key=lambda one: (one.stat().st_mtime, one.name), reverse=True)
    for old in scheduled[REPORTS_KEPT:]:
        old.unlink(missing_ok=True)
    return path


@auto_app.command("status", help="Counts and the report of the last run.")
def auto_status(
    store: Annotated[Path, typer.Option("--store")] = DEFAULT_AUTO_STORE,
) -> None:
    opened = AutoStore.open_read_only(store.expanduser() / AUTO_DB_NAME)
    if opened is None:
        typer.echo("no store yet; run `session2memory auto run` first", err=True)
        raise typer.Exit(code=1)
    try:
        typer.echo(json.dumps(opened.counts(), ensure_ascii=False))
        last = opened.last_run()
        if last is not None:
            typer.echo(f"last run {last[0]} finished {last[1]}")
            typer.echo(json.dumps(last[2], ensure_ascii=False, indent=1))
    finally:
        opened.close()


@auto_app.command("search", help="Search distilled memory and session summaries.")
def auto_search(
    query: Annotated[str, typer.Argument(help="Keywords or a whole question.")],
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help=(
                "keyword: shared words only. semantic: local embeddings fused with keywords. "
                "auto: semantic when the embeddings exist, else keyword."
            ),
        ),
    ] = "auto",
    rerank: Annotated[
        bool,
        typer.Option(
            "--rerank/--no-rerank",
            help="Re-read the best candidates with a local cross-encoder (slower, more exact).",
        ),
    ] = True,
    workspace: Annotated[
        Path | None, typer.Option("--workspace", help="Limit to one workspace.")
    ] = None,
    reader: Annotated[
        str, typer.Option("--reader", help="coding (everything) or openclaw (its own only).")
    ] = "coding",
    limit: Annotated[int, typer.Option("--limit")] = 8,
    store: Annotated[Path, typer.Option("--store")] = DEFAULT_AUTO_STORE,
) -> None:
    scope = _reader(reader)
    opened = AutoStore.open_read_only(store.expanduser() / AUTO_DB_NAME)
    if opened is None:
        raise typer.Exit(code=1)
    try:
        project = workspace.expanduser().resolve(strict=False).as_posix() if workspace else None
        hits = None
        if mode not in ("auto", "keyword", "semantic"):
            raise typer.BadParameter("--mode must be auto, keyword or semantic")
        if mode != "keyword" and opened.has_vectors(
            embed_model_name(), project=project, reader=scope
        ):
            # Models are loaded only when this reader has something to compare, and any
            # failure there (a model that cannot be fetched) leaves keyword search.
            try:
                embedder = load_embedder()
                if embedder is not None:
                    hits = semantic_search(
                        opened,
                        query,
                        embedder=embedder,
                        reranker=load_reranker() if rerank else None,
                        project=project,
                        reader=scope,
                        limit=limit,
                    )
            except Exception as exc:  # noqa: BLE001 - search must still answer
                typer.echo(f"semantic search failed ({type(exc).__name__}: {exc})", err=True)
                hits = None
        if hits is None:
            if mode == "semantic":
                typer.echo(
                    "semantic search is unavailable (run `auto run --step embed` with the "
                    "`semantic` extra installed); using keyword search",
                    err=True,
                )
            hits = opened.search(query, project=project, reader=scope, limit=limit)
    finally:
        opened.close()
    if not hits:
        typer.echo("no match")
        return
    for kind, ref, hit_project, text in hits:
        typer.echo(f"[{kind}] {ref}｜{hit_project or '-'}\n  {text[:500]}")


@auto_app.command("memory", help="List or retire the distilled memory of a workspace.")
def auto_memory(
    workspace: Annotated[Path, typer.Option("--workspace")],
    retire: Annotated[
        str | None, typer.Option("--retire", help="Memory id to retire (kept, no longer used).")
    ] = None,
    all_statuses: Annotated[
        bool, typer.Option("--all", help="Include superseded, retired and stale entries.")
    ] = False,
    reader: Annotated[str, typer.Option("--reader")] = "coding",
    store: Annotated[Path, typer.Option("--store")] = DEFAULT_AUTO_STORE,
) -> None:
    scope = _reader(reader)
    project = workspace.expanduser().resolve(strict=False).as_posix()
    if retire is not None:
        if not (store.expanduser() / AUTO_DB_NAME).is_file():
            raise typer.Exit(code=1)
        writable = AutoStore.open(store.expanduser() / AUTO_DB_NAME)
        try:
            target = writable.get_memory(retire)
            allowed = (
                target is not None
                and target.project == project
                and (scope == "coding" or target.scope == "openclaw")
            )
            changed = allowed and writable.set_memory_status(
                retire, "retired", reason="retired by hand", run_id=None, evidence=[]
            )
        finally:
            writable.close()
        typer.echo("retired" if changed else "no active memory with that id")
        return
    opened = AutoStore.open_read_only(store.expanduser() / AUTO_DB_NAME)
    if opened is None:
        raise typer.Exit(code=1)
    try:
        rows = opened.memories(
            project=project, status=None if all_statuses else "active", reader=scope
        )
    finally:
        opened.close()
    for row in rows:
        typer.echo(f"{row.memory_id}｜{row.status}｜{row.kind}｜{row.text}")
    if not rows:
        typer.echo("no memory for this workspace")


CONSOLE_VIEWS = (
    "overview", "projects", "memories", "memory", "sessions", "session", "runs",
    "retire", "restore",
)  # fmt: skip


@auto_app.command(
    "console",
    help="JSON views of the store for the operator console; retire and restore change it.",
)
def auto_console(
    view: Annotated[str, typer.Argument(help=", ".join(CONSOLE_VIEWS))],
    project: Annotated[str | None, typer.Option("--project", help="Workspace path.")] = None,
    memory_id: Annotated[str | None, typer.Option("--id", help="Memory id.")] = None,
    key: Annotated[str | None, typer.Option("--key", help="Session key (tool:id).")] = None,
    status: Annotated[
        str, typer.Option("--status", help="active, superseded, retired, stale or all.")
    ] = "active",
    tool: Annotated[str | None, typer.Option("--tool")] = None,
    reason: Annotated[str, typer.Option("--reason", help="Why, for retire and restore.")] = "",
    limit: Annotated[int, typer.Option("--limit")] = 200,
    offset: Annotated[int, typer.Option("--offset")] = 0,
    store: Annotated[Path, typer.Option("--store")] = DEFAULT_AUTO_STORE,
) -> None:
    """Always prints one JSON object: ``{"ok": true, "data": ...}`` or
    ``{"ok": false, "error": "..."}`` (with exit status 1)."""

    def fail(message: str) -> None:
        typer.echo(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        raise typer.Exit(code=1)

    def need(value: str | None, option: str) -> str:
        if not value:
            fail(f"{view} needs {option}")
        assert value is not None
        return value

    if view not in CONSOLE_VIEWS:
        fail(f"unknown view {view}; choose from {', '.join(CONSOLE_VIEWS)}")
    if status not in (*console.STATUSES, "all"):
        fail(f"unknown status {status}")
    store_dir = store.expanduser()
    database = store_dir / AUTO_DB_NAME
    writes = view in ("retire", "restore")
    if not database.is_file():
        fail(f"no store at {store_dir.as_posix()}; run `session2memory auto run` first")
    opened = AutoStore.open(database) if writes else AutoStore.open_read_only(database)
    if opened is None:
        fail(f"cannot open {database.as_posix()}")
    assert opened is not None
    data: object
    try:
        if view == "overview":
            data = console.overview(opened, store_dir)
        elif view == "projects":
            data = console.projects(opened)
        elif view == "memories":
            data = console.memories(opened, need(project, "--project"), status=status)
        elif view == "memory":
            data = console.memory(opened, need(memory_id, "--id"))
        elif view == "sessions":
            data = console.sessions(opened, project=project, tool=tool, limit=limit, offset=offset)
        elif view == "session":
            data = console.session(opened, need(key, "--key"))
        elif view == "runs":
            data = console.runs(opened, limit=limit)
        elif view == "retire":
            data = console.retire(opened, need(memory_id, "--id"), need(reason, "--reason"))
        else:
            data = console.restore(opened, need(memory_id, "--id"), need(reason, "--reason"))
    except ValueError as exc:
        fail(str(exc))
    finally:
        opened.close()
    if data is None:
        fail("not found")
    typer.echo(json.dumps({"ok": True, "data": data}, ensure_ascii=False))


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
        if tool_name not in SUPPORTED_TOOLS:
            raise typer.BadParameter(f"Unsupported tool: {tool_name}")
        roots[tool_name] = Path(path).expanduser()
    return roots


def _selected_tools(requested_tools: list[str] | None, roots: dict[str, Path]) -> list[str]:
    selected: list[str] = []
    for tool in requested_tools or sorted(roots) or list(P0_TOOLS):
        if tool not in SUPPORTED_TOOLS:
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


def _openclaw_state_dir() -> Path:
    return Path(os.environ.get("OPENCLAW_STATE_DIR", "~/.openclaw")).expanduser()


def _default_source_roots() -> dict[str, Path]:
    roots = {tool: path.expanduser() for tool, path in DEFAULT_SOURCE_ROOTS.items()}
    roots["openclaw-native"] = _openclaw_state_dir() / "agents"
    return roots


def _codex_homes(source_roots: dict[str, Path], *, include_openclaw: bool) -> list[SourceHome]:
    return discover_codex_homes(
        source_roots["codex"],
        openclaw_state_dir=_openclaw_state_dir() if include_openclaw else None,
    )


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


def _build_adapters(
    tools: list[str],
    source_roots: dict[str, Path],
    *,
    codex_homes: list[SourceHome] | None = None,
) -> dict[str, PipelineAdapter]:
    factories = dict(ADAPTERS)
    factories.update(load_plugin_adapters())
    adapters = {tool: factories[tool](source_roots[tool]) for tool in tools}
    if codex_homes is not None and "codex" in adapters and factories["codex"] is CodexAdapter:
        adapters["codex"] = CodexAdapter(source_roots["codex"], homes=codex_homes)
    return adapters


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
    include_openclaw_codex_homes: Annotated[
        bool,
        typer.Option(
            "--include-openclaw-codex-homes",
            help="Also list each OpenClaw agent's own codex-home under OPENCLAW_STATE_DIR.",
        ),
    ] = False,
) -> None:
    overrides = _parse_source_root(source_root or [])
    source_roots = _default_source_roots()
    source_roots.update(overrides)
    selected_tools = _selected_tools(tool, source_roots)
    codex_homes = (
        _codex_homes(source_roots, include_openclaw=True)
        if include_openclaw_codex_homes and "codex" in selected_tools
        else []
    )
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
        if codex_homes:
            payload["codex_homes"] = [
                {"home_id": home.home_id, "agent": home.agent, "root": home.root.as_posix()}
                for home in codex_homes
            ]
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
    for home in codex_homes:
        typer.echo(f"codex-home id={home.home_id} agent={home.agent} root={home.root.as_posix()}")


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
    include_openclaw_codex_homes: Annotated[
        bool,
        typer.Option(
            "--include-openclaw-codex-homes",
            help=(
                "Also import each OpenClaw agent's own codex-home under OPENCLAW_STATE_DIR. "
                "Off by default: agent-private sessions then flow into the shared output."
            ),
        ),
    ] = False,
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
    codex_homes = (
        _codex_homes(source_roots, include_openclaw=include_openclaw_codex_homes)
        if "codex" in selected_tools
        else None
    )
    if state_store is not None:
        for home in codex_homes or []:
            state_store.sources.register_home(home)
    try:
        session_count, candidate_count = run_pipeline(
            adapters=_build_adapters(selected_tools, source_roots, codex_homes=codex_homes),
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


@app.command("worklog")
def worklog_command(
    period: Annotated[
        str | None,
        typer.Argument(help="Aggregation period: yesterday, last-week, or last-month."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", help="Generated session-memory folder with session2memory.db."),
    ] = Path("./out/session-memory"),
    date_from: Annotated[
        str | None,
        typer.Option("--from", help="Inclusive start date in YYYY-MM-DD format."),
    ] = None,
    date_to: Annotated[
        str | None,
        typer.Option("--to", help="Inclusive end date in YYYY-MM-DD format."),
    ] = None,
    state_db: Annotated[
        Path | None,
        typer.Option("--state-db", help="Override state database path."),
    ] = None,
) -> None:
    if date_from is not None:
        date_from = _parse_date(date_from)
    if date_to is not None:
        date_to = _parse_date(date_to)
    try:
        worklog_range = resolve_worklog_range(
            period=period,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    db_path = resolve_state_db_path(output_dir=output, state_db=state_db)
    if not db_path.is_file():
        raise typer.BadParameter(
            format_missing_state_db_error(
                output_dir=output,
                state_db=state_db,
                period=period,
                date_from=date_from,
                date_to=date_to,
            )
        )
    state_store = StateStore.open(db_path, output_dir=output)
    try:
        result = generate_worklog(
            output_dir=output,
            state_store=state_store,
            worklog_range=worklog_range,
        )
    finally:
        state_store.close()
    typer.echo(
        f"period={result.label} date_from={result.date_from} date_to={result.date_to} "
        f"entries={result.entry_count} workspaces={result.workspace_count} "
        f"worklog={result.path.as_posix()}"
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
