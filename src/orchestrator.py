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

        .cartography/<repo-name>/          (or .cartography/ for explicit --output-dir)
        ├── cartography_trace.jsonl        # shared audit log (all agents)
        ├── blind_spots.json               # unresolved refs + low-confidence metrics
        ├── high_risk_areas.json           # hubs, cycles, velocity, fan-out metrics
        ├── module_graph/                  # Surveyor — static code structure
        │   ├── module_graph.json
        │   ├── module_graph_modules.json
        │   ├── module_graph.html
        │   └── surveyor_stats.json
        ├── data_lineage/                  # Hydrologist — data flow & lineage
        │   ├── lineage_graph.json
        │   ├── lineage_graph.html
        │   └── hydrologist_stats.json
        └── semantics/                     # Semanticist — LLM-powered analysis
            ├── semantic_enrichment.json
            ├── semantic_index.json
            ├── day_one_answers.json
            └── semanticist_stats.json
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.module_graph_dir = output_dir / "module_graph"
        self.data_lineage_dir = output_dir / "data_lineage"
        self.semantics_dir = output_dir / "semantics"
        self.queries_dir = output_dir / "queries"

        # Shared
        self.trace_jsonl = output_dir / "cartography_trace.jsonl"
        self.codebase_md = output_dir / "CODEBASE.md"
        self.onboarding_brief_md = output_dir / "onboarding_brief.md"

        # Surveyor — static code structure
        self.module_graph_json = self.module_graph_dir / "module_graph.json"
        self.module_graph_modules_json = self.module_graph_dir / "module_graph_modules.json"
        self.stats_json = self.module_graph_dir / "surveyor_stats.json"
        self.viz_html = self.module_graph_dir / "module_graph.html"

        # Hydrologist — data flow & lineage
        self.lineage_graph_json = self.data_lineage_dir / "lineage_graph.json"
        self.lineage_viz_html = self.data_lineage_dir / "lineage_graph.html"
        self.hydrologist_stats_json = self.data_lineage_dir / "hydrologist_stats.json"

        # Polish layer — enrichment reports
        self.blind_spots_json = output_dir / "blind_spots.json"
        self.high_risk_json = output_dir / "high_risk_areas.json"

        # Semanticist — LLM-powered semantic analysis
        self.semantic_enrichment_json = self.semantics_dir / "semantic_enrichment.json"
        self.semantic_index_json = self.semantics_dir / "semantic_index.json"
        self.day_one_answers_json = self.semantics_dir / "day_one_answers.json"
        self.fde_day_one_answers_json = self.semantics_dir / "fde_day_one_answers.json"
        self.semanticist_stats_json = self.semantics_dir / "semanticist_stats.json"
        self.reading_order_json = self.semantics_dir / "reading_order.json"
        self.semantic_review_queue_json = self.semantics_dir / "semantic_review_queue.json"
        self.semantic_hotspots_json = self.output_dir / "semantic_hotspots.json"

    def ensure_dirs(self) -> None:
        """Create all output subdirectories if they don't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.module_graph_dir.mkdir(parents=True, exist_ok=True)
        self.data_lineage_dir.mkdir(parents=True, exist_ok=True)
        self.semantics_dir.mkdir(parents=True, exist_ok=True)
        self.queries_dir.mkdir(parents=True, exist_ok=True)


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

    # ---- Enrich graph with classification + confidence ------------------
    from src.graph.enrichment import classify_module_roles
    classify_module_roles(result.graph)

    # ---- Persist graph --------------------------------------------------
    result.graph.save(artifacts.module_graph_json)
    result.graph.export_viz(artifacts.viz_html)

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

    # ---- Enrich dataset classification (before saving!) -----------------
    from src.graph.enrichment import classify_dataset_roles
    classify_dataset_roles(graph)

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

    # ---- Load surveyor stats for reports --------------------------------
    surveyor_stats: dict = {}
    try:
        import json as _json
        surveyor_stats = _json.loads(artifacts.stats_json.read_text(encoding="utf-8"))
    except Exception:
        pass

    # ---- Write blind-spots and high-risk reports ------------------------
    from src.graph.reporting import write_blind_spots, write_high_risk_areas
    write_blind_spots(graph, surveyor_stats, result.stats, artifacts.output_dir)
    write_high_risk_areas(graph, surveyor_stats, result.stats, artifacts.output_dir)

    logger.info(
        "Phase 2 complete.  Lineage artifacts written to: %s",
        artifacts.output_dir.resolve(),
    )
    return result


# ---------------------------------------------------------------------------
# Phase 3: Semanticist (LLM-powered semantic analysis)
# ---------------------------------------------------------------------------


def run_phase3(
    artifacts: CartographyArtifacts,
    graph: "KnowledgeGraph",
    repo_root: Path,
) -> "SemanticsResult":
    """
    Run Phase 3 (Semanticist) — LLM-powered semantic analysis.

    Must be called after run_phase1() and run_phase2() with the same graph.
    Gracefully degrades if Ollama is unavailable (heuristic-only mode).
    """
    from src.agents.semanticist import Semanticist, SemanticsResult

    logger.info("=== Brownfield Cartographer — Phase 3 (Semanticist) ===")

    semanticist = Semanticist()
    result: SemanticsResult = semanticist.run(graph, repo_root)

    # ---- Append trace entries ------------------------------------------
    _write_trace_entries(artifacts.trace_jsonl, result.trace)

    # ---- Write semantic enrichment (all purpose statements + domains) --
    enrichment_data = {
        "purpose_statements": [
            pr.model_dump(mode="json") for pr in result.purpose_results
        ],
        "domain_clustering": result.clustering.model_dump(mode="json")
        if result.clustering else None,
        "documentation_drift": [
            dr.model_dump(mode="json") for dr in result.drift_results
        ],
        "semantic_hotspots": result.hotspot_rankings,
    }
    graph.save_semantics(artifacts.semantic_enrichment_json, enrichment_data)

    # ---- Write semantic index (compact lookup for Navigator Phase 4) ---
    index_data = {
        "modules": {
            pr.file_path: {
                "purpose": pr.purpose_statement,
                "business_logic_score": pr.business_logic_score,
                "key_concepts": pr.key_concepts,
                "confidence": pr.confidence,
            }
            for pr in result.purpose_results
            if pr.purpose_statement
        },
        "domains": {
            d.domain_name: {
                "description": d.description,
                "members": d.members,
            }
            for d in (result.clustering.domains if result.clustering else [])
        },
        "business_logic_hotspots": [
            {
                "file": pr.file_path,
                "score": pr.business_logic_score,
                "purpose": pr.purpose_statement,
            }
            for pr in sorted(result.purpose_results, key=lambda x: x.business_logic_score, reverse=True)[:10]
            if pr.business_logic_score > 0.3
        ],
        "semantic_hotspots": result.hotspot_rankings[:10],
        "reading_order": result.reading_order[:20],
    }
    graph.save_semantics(artifacts.semantic_index_json, index_data)

    # ---- Write Day-One answers -----------------------------------------
    if result.day_one_answers:
        import json as _json
        artifacts.day_one_answers_json.parent.mkdir(parents=True, exist_ok=True)
        artifacts.day_one_answers_json.write_text(
            _json.dumps(result.day_one_answers, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Wrote day-one answers → %s", artifacts.day_one_answers_json)
    if result.fde_day_one_answers:
        import json as _json
        artifacts.fde_day_one_answers_json.parent.mkdir(parents=True, exist_ok=True)
        artifacts.fde_day_one_answers_json.write_text(
            _json.dumps(result.fde_day_one_answers, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Wrote FDE day-one answers -> %s", artifacts.fde_day_one_answers_json)
    # ---- Write reading order for new-engineer onboarding ---------------
    if result.reading_order:
        import json as _json
        artifacts.reading_order_json.parent.mkdir(parents=True, exist_ok=True)
        artifacts.reading_order_json.write_text(
            _json.dumps({"reading_order": result.reading_order}, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Wrote reading order \u2192 %s", artifacts.reading_order_json)
    if result.hotspot_rankings:
        import json as _json
        artifacts.semantic_hotspots_json.parent.mkdir(parents=True, exist_ok=True)
        artifacts.semantic_hotspots_json.write_text(
            _json.dumps({"semantic_hotspots": result.hotspot_rankings}, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Wrote semantic hotspots \u2192 %s", artifacts.semantic_hotspots_json)
    import json as _json
    artifacts.semantic_review_queue_json.parent.mkdir(parents=True, exist_ok=True)
    artifacts.semantic_review_queue_json.write_text(
        _json.dumps({"semantic_review_queue": result.review_queue}, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Wrote semantic review queue \u2192 %s", artifacts.semantic_review_queue_json)
    # ---- Write semanticist stats ----------------------------------------
    _write_semanticist_stats(artifacts.semanticist_stats_json, result.stats)

    # ---- Re-save unified graph (now with semantic metadata) -------------
    graph.save(artifacts.module_graph_json)

    logger.info(
        "Phase 3 complete.  Semantic artifacts written to: %s",
        artifacts.semantics_dir.resolve(),
    )
    return result


def run_phase4(artifacts: CartographyArtifacts) -> "ArchivistResult":
    """
    Run Phase 4 (Archivist) â€” living context generation from saved artifacts.
    """
    from src.agents.archivist import Archivist, ArchivistResult

    logger.info("=== Brownfield Cartographer â€” Phase 4 (Archivist) ===")

    archivist = Archivist(artifacts.output_dir)
    result: ArchivistResult = archivist.run()

    logger.info(
        "Phase 4 complete.  Living context written to: %s",
        artifacts.output_dir.resolve(),
    )
    return result


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


def _write_semanticist_stats(stats_path: Path, stats: dict) -> None:
    """Write the Semanticist stats summary to a JSON file."""
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    with stats_path.open("w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2, default=str)
    logger.info("Wrote semanticist stats → %s", stats_path)
