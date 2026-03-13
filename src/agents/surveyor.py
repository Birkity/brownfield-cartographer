"""
Surveyor Agent — Phase 1 of the Brownfield Cartographer pipeline.

The Surveyor performs deep static analysis of the codebase:
  1. Walks all files via FileInventory
  2. Routes each file to the correct tree-sitter grammar (LanguageRouter)
  3. Calls TreeSitterAnalyzer.analyze_file() for AST-level extraction
  4. Enriches ModuleNodes with git velocity data
  5. Builds a NetworkX DiGraph of module imports
  6. Runs PageRank (architectural hubs) and SCC (circular dependencies)
  7. Marks dead-code candidates (modules with in-degree 0)
  8. Returns the populated KnowledgeGraph + a list of TraceEntry audit events

Phase 2 integration point (Hydrologist):
  - Call surveyor.run() first; pass its KnowledgeGraph into Hydrologist.run()
  - Hydrologist will add DatasetNodes and PRODUCES/CONSUMES edges
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.analyzers.import_resolver import (
    build_import_edges,
    build_dbt_ref_edges,
    detect_project_type,
)
from src.analyzers.tree_sitter_analyzer import analyze_file
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import AnalysisMethod, Language, ModuleNode, TraceEntry
from src.utils.file_inventory import FileInventory
from src.utils.git_tools import GitVelocityResult, extract_git_velocity, get_last_commit_date

logger = logging.getLogger(__name__)


class SurveyorResult:
    """
    Output of a Surveyor run.

    Attributes:
        graph:  The populated KnowledgeGraph.
        trace:  Ordered list of audit events (written to cartography_trace.jsonl).
        stats:  High-level summary numbers for progress reporting.
    """

    def __init__(self, graph: KnowledgeGraph, trace: list[TraceEntry], stats: dict) -> None:
        self.graph = graph
        self.trace = trace
        self.stats = stats


class Surveyor:
    """
    Analyzes a repository's static structure and builds the module import graph.

    Usage::

        surveyor = Surveyor()
        result = surveyor.run(repo_root=Path("/path/to/repo"), velocity_days=30)
        result.graph.save(Path(".cartography/phase1/module_graph.json"))
    """

    def __init__(
        self,
        max_file_bytes: int = 512 * 1024,
        velocity_days: int = 30,
    ) -> None:
        self._max_file_bytes = max_file_bytes
        self._velocity_days = velocity_days

    def run(
        self,
        repo_root: Path,
        velocity_days: Optional[int] = None,
    ) -> SurveyorResult:
        """
        Execute the full Surveyor analysis on *repo_root*.

        Args:
            repo_root:      Absolute path to the local repository.
            velocity_days:  Override the default window for git velocity analysis.

        Returns:
            SurveyorResult containing the KnowledgeGraph and audit trace.
        """
        days = velocity_days if velocity_days is not None else self._velocity_days
        trace: list[TraceEntry] = []
        graph = KnowledgeGraph()

        logger.info("Surveyor starting analysis of %s", repo_root)
        start_time = datetime.now(timezone.utc)

        # ---- Step 1: inventory all files --------------------------------
        inventory = FileInventory()
        items = inventory.scan(repo_root, max_bytes=self._max_file_bytes)
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="file_inventory",
                target=str(repo_root),
                result=f"Found {len(items)} analyzable files",
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )

        # ---- Step 2: git velocity ---------------------------------------
        velocity: GitVelocityResult = extract_git_velocity(repo_root, days=days)
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="extract_git_velocity",
                target=str(repo_root),
                result=(
                    f"{len(velocity.commit_counts)} files with git activity in last {days}d"
                    if velocity.available
                    else "git unavailable — velocity data skipped"
                ),
                analysis_method=AnalysisMethod.GIT_ANALYSIS,
                error=None if velocity.available else "git not available",
            )
        )

        # ---- Step 3: analyse each file ----------------------------------
        parsed_ok = 0
        parse_errors = 0
        grammar_missing = 0
        tracked_rel_paths = {item.rel_posix() for item in items}

        for item in items:
            rel_posix = item.rel_posix()
            module_node = analyze_file(
                abs_path=item.abs_path,
                rel_path=rel_posix,
                language=item.language,
            )
            module_node.change_velocity_30d = velocity.for_file(rel_posix)
            module_node.last_modified = get_last_commit_date(repo_root, item.rel_path)
            graph.add_module(module_node)

            if module_node.parse_error:
                if "not installed" in module_node.parse_error:
                    grammar_missing += 1
                else:
                    parse_errors += 1
                trace.append(
                    TraceEntry(
                        agent="Surveyor",
                        action="analyze_module",
                        target=rel_posix,
                        result="parse_error",
                        analysis_method=AnalysisMethod.STATIC_ANALYSIS,
                        error=module_node.parse_error,
                    )
                )
            else:
                parsed_ok += 1

        logger.info(
            "Surveyor: parsed %d files OK, %d grammar-missing, %d real errors",
            parsed_ok, grammar_missing, parse_errors,
        )

        # ---- Step 4: build import edges ----------------------------------
        edge_count = build_import_edges(graph, repo_root)
        dbt_edge_count = build_dbt_ref_edges(graph)
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="build_import_graph",
                target=str(repo_root),
                result=(
                    f"Added {edge_count} Python import edges and "
                    f"{dbt_edge_count} dbt {{{{ ref() }}}} edges"
                ),
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )

        # ---- Step 5: graph analytics ------------------------------------
        hubs = graph.hub_modules(top_n=10)
        cycles = graph.strongly_connected_components()
        dead = graph.dead_code_candidates()

        for path in dead:
            mod = graph.get_module(path)
            if mod:
                mod.is_dead_code_candidate = True
                graph.add_module(mod)

        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="pagerank_analysis",
                target="module_graph",
                result=(
                    f"Top hub: {hubs[0][0]} (score={hubs[0][1]:.4f})"
                    if hubs
                    else "No modules in graph"
                ),
                confidence=1.0,
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="scc_analysis",
                target="module_graph",
                result=(
                    f"{len(cycles)} circular dependency cluster(s) detected"
                    if cycles
                    else "No circular dependencies detected"
                ),
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        project_type = detect_project_type(repo_root)
        stats = {
            "project_type": project_type,
            "files_scanned": len(items),
            "files_parsed_ok": parsed_ok,
            "grammar_not_available": grammar_missing,
            "parse_errors": parse_errors,
            "import_edges": edge_count,
            "dbt_ref_edges": dbt_edge_count,
            "circular_dependency_clusters": len(cycles),
            "dead_code_candidates": len(dead),
            "top_hubs": hubs[:5],
            "high_velocity_files": [
                (path, count)
                for path, count in velocity.top_files(len(velocity.commit_counts))
                if path in tracked_rel_paths
            ][:10] if velocity.available else [],
            "pareto_core": [
                path for path in velocity.pareto_core()
                if path in tracked_rel_paths
            ] if velocity.available else [],
            "elapsed_seconds": round(elapsed, 2),
        }

        logger.info(
            "Surveyor complete in %.1fs: %d modules, %d edges, %d cycles",
            elapsed, len(graph.all_modules()), edge_count, len(cycles),
        )
        return SurveyorResult(graph=graph, trace=trace, stats=stats)
