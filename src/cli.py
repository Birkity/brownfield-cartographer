"""
CLI entry point for the Brownfield Cartographer.

Commands:
  analyze   Run Phase 1 (Surveyor) analysis on a local path or GitHub URL.
            Full pipeline (Phases 1-4) will be available as analysis is complete.

  query     [TODO Phase 4] Interactive Navigator query mode.

Usage examples:

  # Analyse a local dbt project
  uv run cartographer analyze /path/to/jaffle-shop

  # Analyse from GitHub (clones automatically)
  uv run cartographer analyze https://github.com/dbt-labs/jaffle-shop

  # Custom output dir and 90-day velocity window
  uv run cartographer analyze /path/to/repo --output-dir ./my-output --velocity-days 90

  # Verbose logging
  uv run cartographer --verbose analyze /path/to/repo
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

from src.orchestrator import DEFAULT_OUTPUT_DIR, run_phase1
from src.utils.repo_loader import RepoLoadError

console = Console()


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable DEBUG-level logging.",
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Brownfield Cartographer — codebase intelligence for FDE onboarding."""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------


@cli.command("analyze")
@click.argument("target")
@click.option(
    "--output-dir",
    "-o",
    default=str(DEFAULT_OUTPUT_DIR),
    show_default=True,
    help="Directory to write .cartography/ artifacts into.",
)
@click.option(
    "--velocity-days",
    default=30,
    show_default=True,
    help="Git log window (days) for change-velocity analysis.",
)
@click.option(
    "--clone-base",
    default=None,
    help="Directory to clone remote repos into (default: system temp).",
)
def analyze(
    target: str,
    output_dir: str,
    velocity_days: int,
    clone_base: str | None,
) -> None:
    """
    Analyse TARGET and write cartography artifacts.

    TARGET can be:
    \b
      - A local filesystem path to a repository directory
      - A GitHub HTTPS URL (https://github.com/<owner>/<repo>)
    """
    output_path = Path(output_dir)
    clone_path = Path(clone_base) if clone_base else None

    console.print(
        Panel.fit(
            f"[bold cyan]Brownfield Cartographer[/] — Phase 1 (Surveyor)\n"
            f"[dim]Target:[/] {target}\n"
            f"[dim]Output:[/] {output_path.resolve()}",
            border_style="cyan",
        )
    )

    try:
        artifacts = run_phase1(
            target=target,
            output_dir=output_path,
            velocity_days=velocity_days,
            clone_base=clone_path,
        )
    except RepoLoadError as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Unexpected error:[/] {exc}")
        logging.exception("Unhandled exception in analyze command")
        sys.exit(1)

    # ---- Print summary --------------------------------------------------
    _print_summary(artifacts)


def _print_summary(artifacts) -> None:
    """Print a Rich-formatted summary after a successful Phase 1 run."""
    stats_path = artifacts.stats_json
    if not stats_path.exists():
        console.print("[yellow]No stats file found — skipping summary.[/]")
        return

    with stats_path.open() as fh:
        stats = json.load(fh)

    console.print("\n[bold green]Phase 1 complete![/]\n")

    # ---- Overview table ----
    overview = Table(title="Surveyor Overview", show_header=False, box=None)
    overview.add_column("Metric", style="dim")
    overview.add_column("Value", style="bold")

    overview.add_row("Files scanned", str(stats.get("files_scanned", "?")))
    overview.add_row("Files parsed OK", str(stats.get("files_parsed_ok", "?")))
    grammar_missing = stats.get("grammar_not_available", 0)
    if grammar_missing:
        overview.add_row("Grammar not available (SQL)", str(grammar_missing))
    parse_errors = stats.get("parse_errors", 0)
    if parse_errors:
        overview.add_row("[red]Real parse errors[/red]", str(parse_errors))
    overview.add_row("Import edges", str(stats.get("import_edges", "?")))
    overview.add_row(
        "Circular dependency clusters", str(stats.get("circular_dependency_clusters", "?"))
    )
    overview.add_row("Dead-code candidates", str(stats.get("dead_code_candidates", "?")))
    overview.add_row("Elapsed", f"{stats.get('elapsed_seconds', '?')}s")
    console.print(overview)

    # ---- Top hubs ----
    hubs = stats.get("top_hubs", [])
    if hubs:
        hub_table = Table(title="\nArchitectural Hubs (PageRank)", show_header=True)
        hub_table.add_column("Module", style="cyan")
        hub_table.add_column("PageRank Score", justify="right")
        for path, score in hubs:
            hub_table.add_row(path, f"{score:.5f}")
        console.print(hub_table)

    # ---- High-velocity files ----
    velocity_files = stats.get("high_velocity_files", [])
    if velocity_files:
        vel_table = Table(title="\nHigh-Velocity Files (last 30d commits)", show_header=True)
        vel_table.add_column("File", style="yellow")
        vel_table.add_column("Commits", justify="right")
        for path, count in velocity_files[:10]:
            vel_table.add_row(path, str(count))
        console.print(vel_table)

    # ---- Output paths ----
    console.print("\n[bold]Artifacts written:[/]")
    for name in [
        "module_graph_json",
        "module_graph_modules_json",
        "trace_jsonl",
        "stats_json",
    ]:
        p = getattr(artifacts, name)
        if p.exists():
            console.print(f"  [green]✓[/] {p.resolve()}")
        else:
            console.print(f"  [red]✗[/] {p.resolve()} (not found)")


# ---------------------------------------------------------------------------
# query command (placeholder for Phase 4 Navigator)
# ---------------------------------------------------------------------------


@cli.command("query")
@click.option(
    "--cartography-dir",
    default=str(DEFAULT_OUTPUT_DIR),
    show_default=True,
    help="Path to the .cartography/ directory produced by analyze.",
)
def query(cartography_dir: str) -> None:
    """
    [TODO Phase 4] Interactive query interface (Navigator agent).

    Launches a conversational agent with four tools:
      find_implementation(concept)
      trace_lineage(dataset, direction)
      blast_radius(module_path)
      explain_module(path)
    """
    console.print(
        "[yellow]The Navigator query interface will be available in Phase 4.[/]\n"
        "Run [bold]cartographer analyze[/] first to produce the knowledge graph."
    )


if __name__ == "__main__":
    cli()
