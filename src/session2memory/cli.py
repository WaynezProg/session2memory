from collections.abc import Callable
from datetime import date as Date
from pathlib import Path
from typing import Annotated

import typer

from session2memory.adapters import ClaudeAdapter, CodexAdapter, OpenCodeAdapter, QwenAdapter
from session2memory.pipeline import PipelineAdapter, run_pipeline
from session2memory.review import promote_reviews

app = typer.Typer(no_args_is_help=True)

AdapterFactory = Callable[[Path], PipelineAdapter]
P0_TOOLS = ("codex", "claude", "qwen", "opencode")
DEFAULT_SOURCE_ROOTS = {
    "codex": Path("~/.codex/sessions"),
    "claude": Path("~/.claude/projects"),
    "qwen": Path("~/.qwen"),
    "opencode": Path("~/.local/share/opencode/opencode.db"),
}
ADAPTERS: dict[str, AdapterFactory] = {
    "codex": CodexAdapter,
    "claude": ClaudeAdapter,
    "qwen": QwenAdapter,
    "opencode": OpenCodeAdapter,
}


@app.callback()
def main() -> None:
    pass


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


def _parse_date(value: str) -> str:
    try:
        return Date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise typer.BadParameter("--date must use YYYY-MM-DD") from exc


def _default_source_roots() -> dict[str, Path]:
    return {tool: path.expanduser() for tool, path in DEFAULT_SOURCE_ROOTS.items()}


def _build_adapters(tools: list[str], source_roots: dict[str, Path]) -> dict[str, PipelineAdapter]:
    return {tool: ADAPTERS[tool](source_roots[tool]) for tool in tools}


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
) -> None:
    parsed_date = _parse_date(date)
    overrides = _parse_source_root(source_root or [])
    selected_tools = _selected_tools(tool, overrides)
    source_roots = _default_source_roots()
    source_roots.update(overrides)
    selected_source_roots = {tool_name: source_roots[tool_name] for tool_name in selected_tools}
    session_count, candidate_count = run_pipeline(
        adapters=_build_adapters(selected_tools, source_roots),
        output_dir=output,
        date=parsed_date,
        source_roots=selected_source_roots,
        dry_run=dry_run,
        workspace=workspace,
    )
    typer.echo(
        f"date={parsed_date} tools={len(selected_tools)} sessions={session_count} "
        f"written={0 if dry_run else 1} candidates={candidate_count}"
    )


@app.command("promote")
def promote(
    date: Annotated[str, typer.Option("--date", help="Date to promote in YYYY-MM-DD format.")],
    output: Annotated[Path, typer.Option("--output", help="Generated session-memory folder.")],
) -> None:
    parsed_date = _parse_date(date)
    result = promote_reviews(output_dir=output, date=parsed_date)
    typer.echo(f"date={parsed_date} reviewed={result.reviewed} promoted={result.promoted}")
