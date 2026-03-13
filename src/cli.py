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

from src.orchestrator import DEFAULT_OUTPUT_DIR, run_phase1, run_phase2, run_phase3
from src.utils.repo_loader import RepoLoadError

console = Console(highlight=False)

# Ensure UTF-8 output on Windows (prevents charmap codec errors for ✓ etc.)
import sys as _sys
if _sys.stdout and hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


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
    default=None,
    show_default=True,
    help=(
        "Directory to write artifacts into.  "
        "Defaults to .cartography/<repo-name>/ — auto-derived from the target path or URL."
    ),
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
@click.option(
    "--full-history",
    is_flag=True,
    default=False,
    help=(
        "Clone the full git history (no --depth limit). Slower but gives accurate "
        "change-velocity data. Ignored for local paths."
    ),
)
def analyze(
    target: str,
    output_dir: str | None,
    velocity_days: int,
    clone_base: str | None,
    full_history: bool,
) -> None:
    """
    Analyse TARGET and write cartography artifacts.

    TARGET can be:
    \b
      - A local filesystem path to a repository directory
      - A GitHub HTTPS URL (https://github.com/<owner>/<repo>)

    By default artifacts are written to .cartography/<repo-name>/ so that
    multiple repos can coexist in the same .cartography/ folder.
    Pass --output-dir to override and write to an exact path.
    """
    # ------------------------------------------------------------------
    # Resolve output path: auto-derive subfolder when no --output-dir given
    # ------------------------------------------------------------------
    if output_dir is None:
        repo_name = _derive_repo_name(target)
        output_path = DEFAULT_OUTPUT_DIR / repo_name
        auto_subfolder = True
    else:
        output_path = Path(output_dir)
        auto_subfolder = False
        repo_name = output_path.name

    clone_path = Path(clone_base) if clone_base else None

    console.print(
        Panel.fit(
            f"[bold cyan]Brownfield Cartographer[/] — Phase 1, 2 & 3\n"
            f"[dim]Target:[/] {target}\n"
            f"[dim]Output:[/] {output_path.resolve()}"
            + (f"\n[dim]Repo name:[/] {repo_name}  [dim](auto-derived)[/]" if auto_subfolder else ""),
            border_style="cyan",
        )
    )

    try:
        artifacts, graph, repo_root = run_phase1(
            target=target,
            output_dir=output_path,
            velocity_days=velocity_days,
            clone_base=clone_path,
            full_history=full_history,
        )
    except RepoLoadError as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Unexpected error:[/] {exc}")
        logging.exception("Unhandled exception in analyze command")
        sys.exit(1)

    # ---- Run Phase 2 (Hydrologist) --------------------------------------
    try:
        hydro_result = run_phase2(artifacts, graph, repo_root)
    except Exception as exc:
        console.print(f"[bold yellow]Phase 2 warning:[/] {exc}")
        logging.exception("Phase 2 (Hydrologist) failed — Phase 1 artifacts still available")
        hydro_result = None

    # ---- Run Phase 3 (Semanticist) --------------------------------------
    semantics_result = None
    try:
        semantics_result = run_phase3(artifacts, graph, repo_root)
    except Exception as exc:
        console.print(f"[bold yellow]Phase 3 warning:[/] {exc}")
        logging.exception("Phase 3 (Semanticist) failed — Phase 1/2 artifacts still available")

    # ---- Print summary --------------------------------------------------
    _print_summary(artifacts, hydro_result, semantics_result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_repo_name(target: str) -> str:
    """
    Derive a filesystem-safe folder name from a target path or GitHub URL.

    Examples::
        "https://github.com/dbt-labs/jaffle-shop"  → "jaffle-shop"
        "/tmp/my-project"                           → "my-project"
        "."                                         → current directory name
    """
    import re
    t = target.strip().rstrip("/\\")
    if t.startswith("http://") or t.startswith("https://"):
        # Extract last URL segment, strip .git suffix
        name = re.sub(r"\.git$", "", t.split("/")[-1])
        return name or "unknown-repo"
    # Local path — use last path component (resolve "." to actual dir name)
    return Path(t).resolve().name or "unknown-repo"


def _print_summary(artifacts, hydro_result=None, semantics_result=None) -> None:
    """Print a Rich-formatted summary after a successful run."""
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

    project_type = stats.get("project_type")
    if project_type and project_type != "unknown":
        overview.add_row("[cyan]Project type[/cyan]", project_type)
    overview.add_row("Files scanned", str(stats.get("files_scanned", "?")))
    overview.add_row("Files parsed OK", str(stats.get("files_parsed_ok", "?")))
    grammar_missing = stats.get("grammar_not_available", 0)
    if grammar_missing:
        overview.add_row("Grammar not available (SQL)", str(grammar_missing))
    parse_errors = stats.get("parse_errors", 0)
    if parse_errors:
        overview.add_row("[red]Real parse errors[/red]", str(parse_errors))
    overview.add_row("Import edges", str(stats.get("import_edges", "?")))
    dbt_edges = stats.get("dbt_ref_edges", 0)
    if dbt_edges:
        overview.add_row("[green]dbt {{ ref() }} edges[/green]", str(dbt_edges))
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

    # ---- Phase 2 lineage summary ----
    if hydro_result is not None:
        hs = hydro_result.stats
        console.print("\n[bold green]Phase 2 (Hydrologist) complete![/]\n")

        lineage_table = Table(title="Lineage Overview", show_header=False, box=None)
        lineage_table.add_column("Metric", style="dim")
        lineage_table.add_column("Value", style="bold")

        if hs.get("is_dbt_project"):
            lineage_table.add_row("[cyan]Project type[/cyan]", "dbt")
        lineage_table.add_row("SQL files analyzed", str(hs.get("sql_files_analyzed", 0)))
        lineage_table.add_row("Python files analyzed", str(hs.get("python_files_analyzed", 0)))
        lineage_table.add_row("Datasets discovered", str(hs.get("datasets_total", 0)))
        lineage_table.add_row("Transformations", str(hs.get("transformations_total", 0)))
        lineage_table.add_row(
            "Lineage edges",
            f"{hs.get('produces_edges', 0)} produces + {hs.get('consumes_edges', 0)} consumes",
        )
        lineage_table.add_row("Sources registered", str(hs.get("sources_registered", 0)))
        lineage_table.add_row("Seeds found", str(hs.get("seeds_found", 0)))
        dynamic = hs.get("dynamic_transformations", 0)
        if dynamic:
            lineage_table.add_row(
                "[yellow]Dynamic transformations[/yellow]", str(dynamic)
            )
        lineage_table.add_row("Elapsed", f"{hs.get('elapsed_seconds', '?')}s")
        console.print(lineage_table)

    # ---- Phase 3 semantics summary ----
    if semantics_result is not None:
        console.print("\n[bold green]Phase 3 (Semanticist) complete![/]\n")

        sem_table = Table(title="Semantics Overview", show_header=False, box=None)
        sem_table.add_column("Metric", style="dim")
        sem_table.add_column("Value", style="bold")

        sem_table.add_row(
            "Ollama available",
            "[green]yes[/]" if semantics_result.ollama_available else "[yellow]no (heuristic only)[/]",
        )
        sem_table.add_row(
            "Purpose statements",
            str(len(semantics_result.purpose_results)),
        )
        if semantics_result.clustering:
            sem_table.add_row(
                "Domain clusters",
                str(len(semantics_result.clustering.domains)),
            )
            sem_table.add_row(
                "Clustering method",
                semantics_result.clustering.method or "unknown",
            )
        sem_table.add_row(
            "Doc-drift detections",
            str(len(semantics_result.drift_results)),
        )
        drift_count = sum(1 for d in semantics_result.drift_results if d.drift_level != "no_drift")
        if drift_count:
            sem_table.add_row(
                "[yellow]Files with drift[/yellow]",
                str(drift_count),
            )
        if semantics_result.day_one_answers:
            question_count = len(semantics_result.day_one_answers.get("questions", []))
            sem_table.add_row("Day-One answers", str(question_count))
        doc_missing = sum(
            1 for d in semantics_result.drift_results
            if getattr(d, "documentation_missing", False)
        )
        if doc_missing:
            sem_table.add_row(
                "[dim]Files missing documentation[/dim]",
                str(doc_missing),
            )
        reading_order = getattr(semantics_result, "reading_order", [])
        if reading_order:
            sem_table.add_row("Reading order items", str(len(reading_order)))
        hotspots = getattr(semantics_result, "hotspot_rankings", [])
        if hotspots:
            sem_table.add_row("Semantic hotspots", str(len(hotspots)))
        review_queue = getattr(semantics_result, "review_queue", [])
        if review_queue:
            sem_table.add_row("Review queue items", str(len(review_queue)))
        if semantics_result.budget_summary:
            bs = semantics_result.budget_summary
            sem_table.add_row(
                "LLM calls / tokens",
                f"{bs.get('total_calls', 0)} calls, ~{bs.get('total_prompt_tokens', 0)} prompt tokens",
            )
        console.print(sem_table)

    # ---- Output paths ----
    console.print("\n[bold]Artifacts written:[/]")
    artifact_names = [
        "module_graph_json",
        "module_graph_modules_json",
        "trace_jsonl",
        "stats_json",
        "viz_html",
    ]
    if hydro_result is not None:
        artifact_names.extend([
            "lineage_graph_json",
            "lineage_viz_html",
            "hydrologist_stats_json",
            "blind_spots_json",
            "high_risk_json",
        ])
    if semantics_result is not None:
        artifact_names.extend([
            "semantic_enrichment_json",
            "semantic_index_json",
            "day_one_answers_json",
            "semanticist_stats_json",
            "reading_order_json",
            "semantic_review_queue_json",
            "semantic_hotspots_json",
        ])
    for name in artifact_names:
        p = getattr(artifacts, name)
        if p.exists():
            console.print(f"  [green][OK][/] {p.resolve()}")
        else:
            console.print(f"  [red][--][/] {p.resolve()} (not found)")


def _resolve_lineage_artifact(path: Path) -> Path:
    """Accept a repo artifact dir, data_lineage dir, or direct lineage file."""
    if path.is_file():
        return path
    direct = path / "lineage_graph.json"
    if direct.exists():
        return direct
    nested = path / "data_lineage" / "lineage_graph.json"
    if nested.exists():
        return nested
    raise click.ClickException(f"Could not find lineage_graph.json under {path}")


def _print_lineage_summary_table(title: str, items: list[str], limit: int) -> None:
    table = Table(title=title, show_header=True)
    table.add_column("Node", style="cyan")
    for item in items[:limit]:
        table.add_row(item)
    console.print(table)


@cli.command("lineage-summary")
@click.argument("artifact_root", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--node",
    default=None,
    help="Optional dataset/transformation id to compute blast radius for.",
)
@click.option(
    "--limit",
    default=10,
    show_default=True,
    help="Maximum number of nodes to display per section.",
)
def lineage_summary(artifact_root: Path, node: str | None, limit: int) -> None:
    """
    Print source, sink, and blast-radius summaries from a saved lineage artifact.
    """
    from src.graph.knowledge_graph import KnowledgeGraph

    lineage_path = _resolve_lineage_artifact(artifact_root)
    graph = KnowledgeGraph.load_lineage_artifact(lineage_path)

    sources = graph.find_sources()
    sinks = graph.find_sinks()

    console.print(
        Panel.fit(
            f"[bold cyan]Lineage Summary[/]\n[dim]Artifact:[/] {lineage_path.resolve()}",
            border_style="cyan",
        )
    )

    _print_lineage_summary_table("Source Datasets", sources, limit)
    _print_lineage_summary_table("Sink Datasets", sinks, limit)

    if node:
        blast = graph.blast_radius(node)
        title = f"Blast Radius: {node}"
        _print_lineage_summary_table(title, blast, limit)
        return

    candidate_nodes = sources[: min(len(sources), limit)]
    if not candidate_nodes:
        console.print("[yellow]No lineage nodes available for blast-radius summary.[/]")
        return

    blast_table = Table(title="Blast Radius Summary", show_header=True)
    blast_table.add_column("Node", style="cyan")
    blast_table.add_column("Downstream dependents", justify="right")
    blast_table.add_column("Preview", style="dim")
    for candidate in candidate_nodes:
        blast = graph.blast_radius(candidate)
        preview = ", ".join(blast[:3]) if blast else "-"
        blast_table.add_row(candidate, str(len(blast)), preview)
    console.print(blast_table)


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
