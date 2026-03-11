"""
Orchestrator: wires the agent pipeline together and writes all outputs.

Phase 1 pipeline:
    resolve_repo()  →  Surveyor.run()  →  KnowledgeGraph.save()
                                        →  cartography_trace.jsonl

This module is the only place that knows which agents run in which order.
Later phases slot in here:
    Phase 2: call Hydrologist.run(graph, repo_root) after Surveyor
    Phase 3: call Semanticist.run(graph, repo_root) after Hydrologist
    Phase 4: call Archivist.run(graph, repo_root) last

Usage (programmatic)::

    from src.orchestrator import run_phase1
    artifacts = run_phase1(target="/path/or/github-url", output_dir=Path(".cartography"))
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from src.agents.surveyor import Surveyor, SurveyorResult
from src.utils.repo_loader import RepoLoadError, resolve_repo

logger = logging.getLogger(__name__)

# Default output directory relative to the working directory where the CLI runs
DEFAULT_OUTPUT_DIR = Path(".cartography")


class CartographyArtifacts:
    """
    Paths to all files written by the orchestrator for a given run.

    Directory layout::

        .cartography/
        ├── cartography_trace.jsonl      # shared audit log (all agents)
        ├── module_graph/                # Surveyor — static code structure
        │   ├── module_graph.json
        │   ├── module_graph_modules.json
        │   ├── module_graph.png
        │   └── surveyor_stats.json
        └── data_lineage/                # Hydrologist — data flow & lineage
            ├── lineage_graph.json
            ├── lineage_graph.html
            └── hydrologist_stats.json
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.module_graph_dir = output_dir / "module_graph"
        self.data_lineage_dir = output_dir / "data_lineage"

        # Shared
        self.trace_jsonl = output_dir / "cartography_trace.jsonl"

        # Surveyor — static code structure
        self.module_graph_json = self.module_graph_dir / "module_graph.json"
        self.module_graph_modules_json = self.module_graph_dir / "module_graph_modules.json"
        self.stats_json = self.module_graph_dir / "surveyor_stats.json"
        self.viz_png = self.module_graph_dir / "module_graph.png"

        # Hydrologist — data flow & lineage
        self.lineage_graph_json = self.data_lineage_dir / "lineage_graph.json"
        self.lineage_viz_html = self.data_lineage_dir / "lineage_graph.html"
        self.hydrologist_stats_json = self.data_lineage_dir / "hydrologist_stats.json"

    def ensure_dirs(self) -> None:
        """Create all output subdirectories if they don't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.module_graph_dir.mkdir(parents=True, exist_ok=True)
        self.data_lineage_dir.mkdir(parents=True, exist_ok=True)


def run_phase1(
    target: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    velocity_days: int = 30,
    clone_base: Optional[Path] = None,
    full_history: bool = False,
) -> tuple["CartographyArtifacts", "KnowledgeGraph", Path]:
    """
    Run the full Phase 1 pipeline (Surveyor only).

    Args:
        target:        Local repo path or GitHub HTTPS URL.
        output_dir:    Where to write .cartography/ artifacts.
        velocity_days: Git log window for change-velocity analysis.
        clone_base:    Override clone destination (useful for tests).
        full_history:  If True, clone full git history (accurate velocity).
                       Ignored for local paths.  Default: shallow --depth=50.

    Returns:
        Tuple of (CartographyArtifacts, KnowledgeGraph, repo_root).

    Raises:
        RepoLoadError: if the target cannot be resolved.
    """
    logger.info("=== Brownfield Cartographer — Phase 1 (Surveyor) ===")
    logger.info("Target: %s", target)

    # ---- Resolve repo ---------------------------------------------------
    try:
        repo_root = resolve_repo(target, clone_base=clone_base, full_history=full_history)
    except RepoLoadError as exc:
        logger.error("Could not resolve repo: %s", exc)
        raise

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = CartographyArtifacts(output_dir)
    artifacts.ensure_dirs()

    # ---- Run Surveyor ---------------------------------------------------
    surveyor = Surveyor(velocity_days=velocity_days)
    result: SurveyorResult = surveyor.run(repo_root, velocity_days=velocity_days)

    # ---- Persist graph --------------------------------------------------
    result.graph.save(artifacts.module_graph_json)
    result.graph.export_viz(artifacts.viz_png)

    # ---- Write trace log ------------------------------------------------
    _write_trace(artifacts.trace_jsonl, result)

    # ---- Write stats ----------------------------------------------------
    _write_stats(artifacts.stats_json, result, repo_root, target)

    logger.info(
        "Phase 1 complete.  Artifacts written to: %s",
        output_dir.resolve(),
    )
    return artifacts, result.graph, repo_root


# ---------------------------------------------------------------------------
# Phase 2: Hydrologist (data lineage)
# ---------------------------------------------------------------------------


def run_phase2(
    artifacts: CartographyArtifacts,
    graph: "KnowledgeGraph",
    repo_root: Path,
) -> "HydrologistResult":
    """
    Run Phase 2 (Hydrologist) — data-flow and lineage analysis.

    Must be called after run_phase1() with the same graph instance.
    """
    from src.agents.hydrologist import Hydrologist, HydrologistResult
    from src.graph.knowledge_graph import KnowledgeGraph

    logger.info("=== Brownfield Cartographer — Phase 2 (Hydrologist) ===")

    hydrologist = Hydrologist()
    result: HydrologistResult = hydrologist.run(graph, repo_root)

    # ---- Persist lineage graph -----------------------------------------
    graph.save_lineage(artifacts.lineage_graph_json)

    # ---- Persist lineage visualization ---------------------------------
    graph.export_lineage_viz(artifacts.lineage_viz_html)

    # ---- Append trace entries ------------------------------------------
    _write_trace_entries(artifacts.trace_jsonl, result.trace)

    # ---- Write stats ----------------------------------------------------
    _write_hydrologist_stats(artifacts.hydrologist_stats_json, result.stats)

    # ---- Re-save the unified graph (now with lineage edges) ------------
    graph.save(artifacts.module_graph_json)

    logger.info(
        "Phase 2 complete.  Lineage artifacts written to: %s",
        artifacts.output_dir.resolve(),
    )
    return result


# ---------------------------------------------------------------------------
# TODO Phase 3: add run_phase3(artifacts, repo_root) that calls Semanticist
# TODO Phase 4: add run_phase4(artifacts, repo_root) that calls Archivist
# These will be chained inside a run_full_pipeline() function.
# ---------------------------------------------------------------------------


def _write_trace(trace_path: Path, result: SurveyorResult) -> None:
    """Append all TraceEntry records to the JSONL audit log."""
    with trace_path.open("a", encoding="utf-8") as fh:
        for entry in result.trace:
            fh.write(entry.model_dump_json() + "\n")
    logger.info("Wrote %d trace entries → %s", len(result.trace), trace_path)


def _write_stats(
    stats_path: Path,
    result: SurveyorResult,
    repo_root: Path,
    target: str,
) -> None:
    """Write the Surveyor stats summary to a JSON file."""
    payload = {
        "target": target,
        "repo_root": str(repo_root),
        **result.stats,
    }
    with stats_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    logger.info("Wrote surveyor stats → %s", stats_path)


def _write_trace_entries(trace_path: Path, trace: list) -> None:
    """Append TraceEntry records from any agent to the JSONL audit log."""
    with trace_path.open("a", encoding="utf-8") as fh:
        for entry in trace:
            fh.write(entry.model_dump_json() + "\n")
    logger.info("Wrote %d trace entries → %s", len(trace), trace_path)


def _write_hydrologist_stats(stats_path: Path, stats: dict) -> None:
    """Write the Hydrologist stats summary to a JSON file."""
    with stats_path.open("w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2, default=str)
    logger.info("Wrote hydrologist stats → %s", stats_path)
