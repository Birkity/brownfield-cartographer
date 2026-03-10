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
    """Paths to all files written by the orchestrator for a given run."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.module_graph_json = output_dir / "module_graph.json"
        self.module_graph_modules_json = output_dir / "module_graph_modules.json"
        self.trace_jsonl = output_dir / "cartography_trace.jsonl"
        self.stats_json = output_dir / "surveyor_stats.json"
        # TODO Phase 2: self.lineage_graph_json = output_dir / "lineage_graph.json"
        # TODO Phase 4: self.codebase_md = output_dir / "CODEBASE.md"
        # TODO Phase 4: self.onboarding_brief_md = output_dir / "onboarding_brief.md"


def run_phase1(
    target: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    velocity_days: int = 30,
    clone_base: Optional[Path] = None,
) -> CartographyArtifacts:
    """
    Run the full Phase 1 pipeline (Surveyor only).

    Args:
        target:        Local repo path or GitHub HTTPS URL.
        output_dir:    Where to write .cartography/ artifacts.
        velocity_days: Git log window for change-velocity analysis.
        clone_base:    Override clone destination (useful for tests).

    Returns:
        CartographyArtifacts with paths to all written files.

    Raises:
        RepoLoadError: if the target cannot be resolved.
    """
    logger.info("=== Brownfield Cartographer — Phase 1 (Surveyor) ===")
    logger.info("Target: %s", target)

    # ---- Resolve repo ---------------------------------------------------
    try:
        repo_root = resolve_repo(target, clone_base=clone_base)
    except RepoLoadError as exc:
        logger.error("Could not resolve repo: %s", exc)
        raise

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = CartographyArtifacts(output_dir)

    # ---- Run Surveyor ---------------------------------------------------
    surveyor = Surveyor(velocity_days=velocity_days)
    result: SurveyorResult = surveyor.run(repo_root, velocity_days=velocity_days)

    # ---- Persist graph --------------------------------------------------
    result.graph.save(artifacts.module_graph_json)

    # ---- Write trace log ------------------------------------------------
    _write_trace(artifacts.trace_jsonl, result)

    # ---- Write stats ----------------------------------------------------
    _write_stats(artifacts.stats_json, result, repo_root, target)

    logger.info(
        "Phase 1 complete.  Artifacts written to: %s",
        output_dir.resolve(),
    )
    return artifacts


# ---------------------------------------------------------------------------
# TODO Phase 2: add run_phase2(artifacts, repo_root) that calls Hydrologist
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
