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

Phase 3 integration point (Semanticist):
  - After Surveyor builds the graph, Semanticist enriches each ModuleNode
    with purpose_statement + domain_cluster by iterating graph.all_modules()
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.analyzers.tree_sitter_analyzer import analyze_file
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import AnalysisMethod, ModuleNode, TraceEntry
from src.utils.file_inventory import FileInventory
from src.utils.git_tools import GitVelocityResult, extract_git_velocity, get_last_commit_date

logger = logging.getLogger(__name__)


class SurveyorResult:
    """
    Output of a Surveyor run.

    Attributes:
        graph:   The populated KnowledgeGraph.
        trace:   Ordered list of audit events (will be written to cartography_trace.jsonl).
        stats:   High-level summary numbers for progress reporting.
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        trace: list[TraceEntry],
        stats: dict,
    ) -> None:
        self.graph = graph
        self.trace = trace
        self.stats = stats


class Surveyor:
    """
    Analyzes a repository's static structure and builds the module import graph.

    Usage::

        surveyor = Surveyor()
        result = surveyor.run(repo_root=Path("/path/to/repo"), velocity_days=30)
        result.graph.save(Path(".cartography/module_graph.json"))
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
        start_time = datetime.utcnow()

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

        for item in items:
            rel_posix = item.rel_posix()
            module_node = analyze_file(
                abs_path=item.abs_path,
                rel_path=rel_posix,
                language=item.language,
            )

            # Enrich with git data
            module_node.change_velocity_30d = velocity.for_file(rel_posix)
            module_node.last_modified = get_last_commit_date(
                repo_root, item.rel_path
            )

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
            parsed_ok,
            grammar_missing,
            parse_errors,
        )

        # ---- Step 4: build import edges ----------------------------------
        edge_count = self._build_import_edges(graph, repo_root)
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="build_import_graph",
                target=str(repo_root),
                result=f"Added {edge_count} import edges to the module graph",
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )

        # ---- Step 5: graph analytics ------------------------------------
        hubs = graph.hub_modules(top_n=10)
        cycles = graph.strongly_connected_components()
        dead = graph.dead_code_candidates()

        # Mark dead-code candidates on ModuleNodes
        for path in dead:
            mod = graph.get_module(path)
            if mod:
                mod.is_dead_code_candidate = True
                graph.add_module(mod)  # re-add to update graph node attrs

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

        elapsed = (datetime.utcnow() - start_time).total_seconds()
        stats = {
            "files_scanned": len(items),
            "files_parsed_ok": parsed_ok,
            "grammar_not_available": grammar_missing,
            "parse_errors": parse_errors,
            "import_edges": edge_count,
            "circular_dependency_clusters": len(cycles),
            "dead_code_candidates": len(dead),
            "top_hubs": hubs[:5],
            "high_velocity_files": velocity.top_files(10) if velocity.available else [],
            "pareto_core": velocity.pareto_core() if velocity.available else [],
            "elapsed_seconds": round(elapsed, 2),
        }

        logger.info(
            "Surveyor complete in %.1fs: %d modules, %d edges, %d cycles",
            elapsed,
            len(graph.all_modules()),
            edge_count,
            len(cycles),
        )
        return SurveyorResult(graph=graph, trace=trace, stats=stats)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_import_edges(self, graph: KnowledgeGraph, repo_root: Path) -> int:
        """
        Resolve imports in each Python module to graph edges.

        For absolute imports (e.g. `import src.utils.git_tools`), we try to
        match against known module paths.  For relative imports we resolve
        relative to the importing file's directory.

        Returns the total number of edges added.
        """
        tracked_paths = {mod.path for mod in graph.all_modules()}

        # Build a lookup: dotted module name → rel_path
        # e.g. "src.utils.git_tools" → "src/utils/git_tools.py"
        dotted_to_path: dict[str, str] = {}
        for path in tracked_paths:
            # Convert POSIX path to dotted module name (strip .py / .pyi)
            dotted = path.replace("/", ".").replace("\\", ".")
            for suffix in (".py", ".pyi"):
                if dotted.endswith(suffix):
                    dotted = dotted[: -len(suffix)]
            dotted_to_path[dotted] = path

            # Also index by just the module name (last component) for fuzzy matching
            parts = dotted.split(".")
            if parts:
                dotted_to_path.setdefault(parts[-1], path)

        edges_added = 0
        for module in graph.all_modules():
            for imp in module.imports:
                target = self._resolve_import(
                    imp.module,
                    module.path,
                    tracked_paths,
                    dotted_to_path,
                )
                if target:
                    graph.add_import_edge(module.path, target)
                    edges_added += 1

        return edges_added

    def _resolve_import(
        self,
        import_module: str,
        source_path: str,
        tracked_paths: set[str],
        dotted_to_path: dict[str, str],
    ) -> Optional[str]:
        """
        Try to resolve an import string to a known module path.

        Resolution order:
        1. Exact dotted-name match in our index
        2. Relative import resolution from source file's directory
        3. Partial (suffix) match as a last resort

        Returns None for third-party or unresolvable imports.
        """
        # Strip leading dots for relative imports
        clean = import_module.lstrip(".")
        is_relative = import_module.startswith(".")
        dot_count = len(import_module) - len(clean)

        # ---- Exact match ----
        if clean in dotted_to_path:
            return dotted_to_path[clean]

        # ---- Relative import resolution ----
        if is_relative:
            parts = source_path.replace("\\", "/").split("/")
            # Go up dot_count - 1 levels from source file's directory
            if dot_count <= len(parts):
                base_parts = parts[: -(dot_count)]  # parent dir
                if clean:
                    candidate = "/".join(base_parts + clean.split(".")) + ".py"
                    if candidate in tracked_paths:
                        return candidate
                    # Try as a package __init__
                    candidate_init = "/".join(base_parts + clean.split(".")) + "/__init__.py"
                    if candidate_init in tracked_paths:
                        return candidate_init

        # ---- Partial suffix match (e.g. "utils.git_tools" matches "src/utils/git_tools.py") ----
        if clean:
            candidate_suffix = clean.replace(".", "/") + ".py"
            for path in tracked_paths:
                if path.endswith(candidate_suffix):
                    return path

        return None  # third-party or unresolvable
