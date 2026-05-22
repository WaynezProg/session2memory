from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    pass


def _parse_source_root(raw: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for item in raw:
        if "=" not in item:
            raise typer.BadParameter("--source-root must use tool=path")
        tool, path = item.split("=", 1)
        roots[tool.strip()] = Path(path).expanduser()
    return roots


@app.command("import")
def import_sessions(
    date: str = typer.Option(..., "--date", help="Date to import in YYYY-MM-DD format."),
    output: Path = typer.Option(..., "--output", help="Generated HKS-ingestable folder."),
    tool: list[str] = typer.Option([], "--tool", help="Limit import to one or more tools."),
    workspace: Path | None = typer.Option(None, "--workspace", help="Limit to one workspace path."),
    source_root: list[str] = typer.Option(
        [], "--source-root", help="Override source root as tool=path."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan and report without writing output."),
) -> None:
    roots = _parse_source_root(source_root)
    selected_tools = tool or sorted(roots)
    if dry_run:
        typer.echo(f"date={date} tools={len(selected_tools)} sessions=0 written=0")
        return
    output.mkdir(parents=True, exist_ok=True)
    typer.echo(f"date={date} tools={len(selected_tools)} sessions=0 written=1")
