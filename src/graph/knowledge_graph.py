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
        )

    def get_module(self, path: str) -> Optional[ModuleNode]:
        return self._modules.get(path)

    def all_modules(self) -> list[ModuleNode]:
        return list(self._modules.values())

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    def add_import_edge(self, source: str, target: str) -> None:
        """
        Record that *source* imports *target*.

        If the edge already exists, increment the import_count weight.
        *target* may not be a tracked module (e.g. third-party package) —
        we still create a node for it so PageRank works correctly.
        """
        if not self._g.has_node(target):
            self._g.add_node(target, language="external", lines_of_code=0)

        if self._g.has_edge(source, target):
            self._g[source][target]["import_count"] += 1
        else:
            self._g.add_edge(source, target, edge_type="IMPORTS", import_count=1)

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
        Return Python module paths whose in-degree is 0 within the tracked set.

        Only Python files are considered — SQL/YAML files are data artifacts,
        not modules, so 'not imported' is meaningless for them.

        These are candidates for dead code, entry-point scripts, or standalone
        utilities.  They are NOT confirmed dead code without call-graph analysis.
        """
        from src.models.nodes import Language  # avoid circular at module level

        return sorted(
            path
            for path, mod in self._modules.items()
            if mod.language == Language.PYTHON and self._g.in_degree(path) == 0
        )

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
