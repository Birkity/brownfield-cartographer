"""
KnowledgeGraph: NetworkX-backed graph store for the Brownfield Cartographer.

Wraps a NetworkX DiGraph with typed node/edge APIs and handles serialization
to `.cartography/module_graph.json`.

Design notes:
  - Nodes are keyed by POSIX relative path (modules) or dataset name (datasets).
  - All node data is stored as JSON-serializable dicts — Pydantic models are
    serialized on add and deserialized on retrieval.
  - Phase 1 builds the module IMPORTS graph.
    Phase 2 will add PRODUCES/CONSUMES dataset edges.
  - The graph is persisted as NetworkX's node-link JSON format.

Graph analytics exposed:
  - pagerank()            : identify architectural hubs
  - strongly_connected()  : circular dependency clusters
  - dead_code_candidates(): exported modules with no in-edges
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import networkx as nx

from src.models.nodes import ModuleNode

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """
    Central graph store.  One instance is shared across all agents in a run.

    Usage::

        graph = KnowledgeGraph()
        graph.add_module(module_node)
        graph.add_import_edge("src/foo.py", "src/bar.py")
        graph.save(Path(".cartography/module_graph.json"))
    """

    def __init__(self) -> None:
        # Directed graph: edge A → B means "A imports B"
        self._g: nx.DiGraph = nx.DiGraph()
        # Rich module objects, keyed by rel_path
        self._modules: dict[str, ModuleNode] = {}

    # ------------------------------------------------------------------
    # Node management
    # ------------------------------------------------------------------

    def add_module(self, module: ModuleNode) -> None:
        """Add or update a ModuleNode in the graph."""
        self._modules[module.path] = module
        self._g.add_node(
            module.path,
            language=module.language.value,
            lines_of_code=module.lines_of_code,
            change_velocity_30d=module.change_velocity_30d,
            is_dead_code_candidate=module.is_dead_code_candidate,
            parse_error=module.parse_error,
            function_count=len(module.functions),
            class_count=len(module.classes),
            import_count=len(module.imports),
            dbt_ref_count=len(module.dbt_refs),
            complexity_score=module.complexity_score,
        )

    def get_module(self, path: str) -> Optional[ModuleNode]:
        return self._modules.get(path)

    def all_modules(self) -> list[ModuleNode]:
        return list(self._modules.values())

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    def add_import_edge(self, source: str, target: str, edge_type: str = "IMPORTS") -> None:
        """
        Record that *source* imports *target*.

        If the edge already exists, increment the import_count weight.
        *target* may not be a tracked module (e.g. third-party package) —
        we still create a node for it so PageRank works correctly.

        Args:
            edge_type: "IMPORTS" for Python imports, "DBT_REF" for {{ ref() }} calls.
        """
        if not self._g.has_node(target):
            self._g.add_node(target, language="external", lines_of_code=0)

        if self._g.has_edge(source, target):
            self._g[source][target]["import_count"] += 1
        else:
            self._g.add_edge(source, target, edge_type=edge_type, import_count=1)

    # TODO Phase 2 (Hydrologist): add_produces_edge(transformation_id, dataset_name)
    # TODO Phase 2 (Hydrologist): add_consumes_edge(dataset_name, transformation_id)
    # TODO Phase 2 (Hydrologist): add_dataset_node(DatasetNode)

    # ------------------------------------------------------------------
    # Graph analytics
    # ------------------------------------------------------------------

    def pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        """
        Compute PageRank over the import graph.

        High-PageRank nodes are the modules most frequently imported by others —
        the architectural hubs.  Returns {module_path: score}.
        """
        if self._g.number_of_nodes() == 0:
            return {}
        try:
            scores: dict[str, float] = nx.pagerank(self._g, alpha=alpha)
            return scores
        except nx.PowerIterationFailedConvergence:
            logger.warning("PageRank did not converge; returning degree centrality")
            return nx.degree_centrality(self._g)

    def strongly_connected_components(self) -> list[list[str]]:
        """
        Return non-trivial strongly-connected components (circular dependencies).

        A component with >1 node means those modules form a circular import chain.
        """
        sccs = list(nx.strongly_connected_components(self._g))
        # Only return cycles (>1 node) — singletons are not circles
        cycles = [sorted(scc) for scc in sccs if len(scc) > 1]
        return sorted(cycles, key=len, reverse=True)

    def dead_code_candidates(self) -> list[str]:
        """
        Return module paths whose in-degree is 0 within the tracked set.

        Python files:
            Any Python module with in-degree == 0 is a candidate (may be an
            entry-point script, standalone utility, or genuinely dead code).

        SQL files:
            A SQL model with in-degree == 0 AND not located in seeds/ or macros/
            is likely an unreferenced terminal model or an unused model.
            Seeds and macros are exempted — they are not meant to be ref'd by others.
        """
        from src.models.nodes import Language  # avoid circular at module level

        candidates = []
        for path, mod in self._modules.items():
            if self._g.in_degree(path) != 0:
                continue
            if mod.language == Language.PYTHON:
                candidates.append(path)
            elif mod.language == Language.SQL:
                posix = path.replace("\\", "/")
                # Exempt seeds/ and macros/ — they are not dbt models
                if "/seeds/" not in posix and "/macros/" not in posix:
                    # Only flag if dbt ref edges have been built (i.e., other SQL files
                    # were analysed) — avoids false positives on partial scans.
                    if any(
                        m.language == Language.SQL and len(m.dbt_refs) > 0
                        for m in self._modules.values()
                    ):
                        candidates.append(path)
        return sorted(candidates)

    def hub_modules(self, top_n: int = 10) -> list[tuple[str, float]]:
        """Return the top-N modules by PageRank score."""
        scores = self.pagerank()
        # Filter to tracked modules only
        module_scores = [
            (path, score) for path, score in scores.items() if path in self._modules
        ]
        return sorted(module_scores, key=lambda x: x[1], reverse=True)[:top_n]

    def summary(self) -> dict[str, Any]:
        """Return a human-readable summary dict for logging and the Archivist."""
        from src.models.nodes import Language

        sccs = self.strongly_connected_components()
        by_lang: dict[str, int] = {}
        parse_errors = 0
        grammar_missing = 0
        for mod in self._modules.values():
            by_lang[mod.language.value] = by_lang.get(mod.language.value, 0) + 1
            if mod.parse_error:
                if "not installed" in mod.parse_error:
                    grammar_missing += 1
                else:
                    parse_errors += 1
        return {
            "total_modules": len(self._modules),
            "by_language": by_lang,
            "total_nodes": self._g.number_of_nodes(),
            "total_edges": self._g.number_of_edges(),
            "circular_dependency_clusters": len(sccs),
            "largest_cycle_size": len(sccs[0]) if sccs else 0,
            "dead_code_candidates": len(self.dead_code_candidates()),
            "grammar_not_available": grammar_missing,
            "real_parse_errors": parse_errors,
            "hub_modules": self.hub_modules(5),
        }

    # ------------------------------------------------------------------
    # Visualization
    # ------------------------------------------------------------------

    def export_viz(self, output_path: Path) -> bool:
        """
        Export the import graph to a PNG image.

        Attempts two rendering engines in order:
        1. pydot / Graphviz dot (better hierarchical layout — needs graphviz binary)
        2. matplotlib spring-layout (always works if matplotlib is installed)

        Returns True if the PNG was written, False if both engines failed.
        """
        if self._g.number_of_nodes() == 0:
            logger.warning("Graph is empty — skipping visualization")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # ---- Attempt 1: pydot (requires graphviz system binary) ----
        try:
            from networkx.drawing.nx_pydot import to_pydot  # type: ignore[import]
            dot = to_pydot(self._g)
            dot.write_png(str(output_path))
            logger.info("Saved graph visualization (pydot) → %s", output_path)
            return True
        except Exception as exc:
            logger.debug("pydot visualization failed (%s), falling back to matplotlib", exc)

        # ---- Attempt 2: matplotlib (no system deps) ----
        try:
            import matplotlib  # type: ignore[import]
            matplotlib.use("Agg")  # headless — no display required
            import matplotlib.pyplot as plt  # type: ignore[import]

            # Colour nodes by language
            _LANG_COLOURS: dict[str, str] = {
                "python": "#4B8BBE",
                "sql": "#F0C62E",
                "yaml": "#6ABE45",
                "javascript": "#F7DF1E",
                "typescript": "#3178C6",
                "external": "#BBBBBB",
            }

            node_colors = [
                _LANG_COLOURS.get(
                    self._g.nodes[n].get("language", "external"), "#CCCCCC"
                )
                for n in self._g.nodes()
            ]

            n_nodes = self._g.number_of_nodes()
            fig_size = max(14, min(n_nodes // 2, 40))
            fig, ax = plt.subplots(figsize=(fig_size, fig_size * 0.7))

            # Pick a layout that scales reasonably
            if n_nodes <= 40:
                pos = nx.spring_layout(self._g, k=2.0 / (n_nodes ** 0.5 + 1), seed=42)
            else:
                pos = nx.kamada_kawai_layout(self._g)

            nx.draw_networkx(
                self._g,
                pos=pos,
                ax=ax,
                node_color=node_colors,
                node_size=max(80, 400 - n_nodes * 3),
                font_size=max(4, 8 - n_nodes // 20),
                arrows=True,
                arrowsize=10,
                alpha=0.85,
                with_labels=True,
            )
            ax.set_title(
                f"Module Import Graph — {n_nodes} nodes, "
                f"{self._g.number_of_edges()} edges",
                fontsize=12,
            )
            ax.axis("off")
            plt.tight_layout()
            plt.savefig(str(output_path), dpi=120, bbox_inches="tight")
            plt.close(fig)
            logger.info("Saved graph visualization (matplotlib) → %s", output_path)
            return True
        except Exception as exc:
            logger.warning("matplotlib visualization also failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def save(self, output_path: Path) -> None:
        """
        Serialise the graph to *output_path* as NetworkX node-link JSON.

        Also writes a companion `<stem>_modules.json` with the full ModuleNode
        records so agents in later phases don't have to re-parse.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save graph topology
        data = nx.node_link_data(self._g)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        logger.info("Saved module graph → %s", output_path)

        # Save full module records alongside
        modules_path = output_path.with_stem(output_path.stem + "_modules")
        modules_data = {
            path: mod.model_dump(mode="json")
            for path, mod in self._modules.items()
        }
        with modules_path.open("w", encoding="utf-8") as fh:
            json.dump(modules_data, fh, indent=2, default=str)
        logger.info("Saved module details → %s", modules_path)

    @classmethod
    def load(cls, input_path: Path) -> "KnowledgeGraph":
        """
        Load a previously-saved graph from node-link JSON.

        Also attempts to reload the companion `_modules.json` if present.
        """
        graph = cls()

        with input_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        graph._g = nx.node_link_graph(data, directed=True, multigraph=False)

        modules_path = input_path.with_stem(input_path.stem + "_modules")
        if modules_path.exists():
            with modules_path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for path, mod_data in raw.items():
                try:
                    graph._modules[path] = ModuleNode.model_validate(mod_data)
                except Exception as exc:
                    logger.warning("Could not deserialise ModuleNode %s: %s", path, exc)

        logger.info(
            "Loaded graph: %d nodes, %d edges from %s",
            graph._g.number_of_nodes(),
            graph._g.number_of_edges(),
            input_path,
        )
        return graph
