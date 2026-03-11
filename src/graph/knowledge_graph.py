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

from src.models.nodes import DatasetNode, ModuleNode, TransformationNode

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
        # Phase 2 (Hydrologist): lineage objects
        self._datasets: dict[str, DatasetNode] = {}
        self._transformations: dict[str, TransformationNode] = {}

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

    def add_dataset_node(self, dataset: DatasetNode) -> None:
        """Add or update a DatasetNode in the graph."""
        # Don't overwrite a higher-confidence entry with a lower one
        existing = self._datasets.get(dataset.name)
        if existing and existing.confidence >= dataset.confidence:
            # Merge columns if the new one has more info
            if dataset.columns and not existing.columns:
                existing.columns = dataset.columns
            if dataset.description and not existing.description:
                existing.description = dataset.description
            return
        self._datasets[dataset.name] = dataset
        self._g.add_node(
            dataset.name,
            node_type="dataset",
            dataset_type=dataset.dataset_type,
            storage_type=dataset.storage_type.value,
            confidence=dataset.confidence,
        )

    def add_transformation_node(self, transformation: TransformationNode) -> None:
        """Add or update a TransformationNode in the graph."""
        self._transformations[transformation.id] = transformation
        self._g.add_node(
            transformation.id,
            node_type="transformation",
            transformation_type=transformation.transformation_type,
            source_file=transformation.source_file,
            confidence=transformation.confidence,
            is_dynamic=transformation.is_dynamic,
        )

    def add_produces_edge(self, transformation_id: str, dataset_name: str) -> None:
        """Record that *transformation_id* produces *dataset_name*."""
        if not self._g.has_node(dataset_name):
            self._g.add_node(dataset_name, node_type="dataset")
        if not self._g.has_node(transformation_id):
            self._g.add_node(transformation_id, node_type="transformation")
        self._g.add_edge(
            transformation_id, dataset_name, edge_type="PRODUCES"
        )

    def add_consumes_edge(self, transformation_id: str, dataset_name: str) -> None:
        """Record that *transformation_id* consumes *dataset_name*."""
        if not self._g.has_node(dataset_name):
            self._g.add_node(dataset_name, node_type="dataset")
        if not self._g.has_node(transformation_id):
            self._g.add_node(transformation_id, node_type="transformation")
        self._g.add_edge(
            dataset_name, transformation_id, edge_type="CONSUMES"
        )

    def get_dataset(self, name: str) -> Optional[DatasetNode]:
        """Return a DatasetNode by name, or None."""
        return self._datasets.get(name)

    def all_datasets(self) -> list[DatasetNode]:
        """Return all registered DatasetNodes."""
        return list(self._datasets.values())

    def all_transformations(self) -> list[TransformationNode]:
        """Return all registered TransformationNodes."""
        return list(self._transformations.values())

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

        Python:
            Any Python module with in-degree == 0 is a candidate.

        SQL:
            A SQL model with in-degree == 0 AND not in seeds/ or macros/
            is a candidate.  Only activated when dbt ref edges have been built.

        JavaScript / TypeScript:
            Any JS/TS file with in-degree == 0 that is not a known entry-point
            filename (index.js, main.ts, app.ts …) is a candidate.
            Only activated when at least one JS/TS file has imports (i.e. the
            import-resolution step has run).

        Other languages (Java, Go, Rust, …):
            Flagged when in-degree == 0 and the graph has non-trivial edges
            (i.e. some import resolution actually ran).
        """
        from src.models.nodes import Language

        # JS/TS conventional entry-point filenames — exempted from dead-code flagging.
        _JS_ENTRY_POINTS = frozenset({
            "index.js", "index.ts", "index.tsx", "index.jsx",
            "main.js", "main.ts", "main.jsx",
            "app.js", "app.ts", "app.tsx", "app.jsx",
            "server.js", "server.ts",
        })

        # Guard flags: only flag a language's files if its graph edges were built.
        has_dbt_refs = any(
            m.language == Language.SQL and len(m.dbt_refs) > 0
            for m in self._modules.values()
        )
        has_js_imports = any(
            m.language in (Language.JAVASCRIPT, Language.TYPESCRIPT) and len(m.imports) > 0
            for m in self._modules.values()
        )

        candidates = []
        for path, mod in self._modules.items():
            if self._g.in_degree(path) != 0:
                continue

            if mod.language == Language.PYTHON:
                candidates.append(path)

            elif mod.language == Language.SQL:
                posix = path.replace("\\", "/")
                if "/seeds/" not in posix and "/macros/" not in posix:
                    if has_dbt_refs:
                        candidates.append(path)

            elif mod.language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
                from pathlib import PurePosixPath
                fname = PurePosixPath(path).name
                if fname not in _JS_ENTRY_POINTS and has_js_imports:
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
                "python":     "#4B8BBE",
                "sql":        "#F0C62E",
                "yaml":       "#6ABE45",
                "javascript": "#F7DF1E",
                "typescript": "#3178C6",
                "java":       "#E76F00",
                "kotlin":     "#7F52FF",
                "scala":      "#DC322F",
                "go":         "#00ADD8",
                "rust":       "#CE422B",
                "csharp":     "#239120",
                "ruby":       "#CC342D",
                "shell":      "#89E051",
                "external":   "#BBBBBB",
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

    def export_lineage_viz(self, output_path: Path) -> bool:
        """
        Export the data-lineage subgraph as an interactive PyVis HTML file.

        Only includes dataset and transformation nodes (not module import edges).
        Returns True if the HTML was written, False on failure.
        """
        if not self._datasets and not self._transformations:
            logger.warning("No lineage data — skipping PyVis visualization")
            return False

        try:
            from pyvis.network import Network  # type: ignore[import]
        except ImportError:
            logger.warning("pyvis not installed — skipping lineage visualization")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        net = Network(
            height="800px",
            width="100%",
            directed=True,
            bgcolor="#ffffff",
            font_color="#333333",
        )
        net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=200)

        # Colour scheme
        _DS_COLORS = {
            "dbt_source": "#FF6B6B",
            "dbt_model":  "#4ECDC4",
            "dbt_seed":   "#45B7D1",
            "table_ref":  "#96CEB4",
            "file_read":  "#DDA0DD",
            "file_write": "#DDA0DD",
            "api_call":   "#FFD93D",
            "unknown":    "#CCCCCC",
        }
        _XFORM_COLOR = "#FFA07A"

        # Add dataset nodes
        for ds in self._datasets.values():
            label = ds.name.split(".")[-1] if "." in ds.name else ds.name
            title = (
                f"<b>{ds.name}</b><br>"
                f"Type: {ds.dataset_type}<br>"
                f"Source: {ds.source_file or 'N/A'}<br>"
                f"Confidence: {ds.confidence}"
            )
            if ds.description:
                title += f"<br>Description: {ds.description}"
            if ds.columns:
                title += f"<br>Columns: {', '.join(ds.columns[:10])}"

            net.add_node(
                ds.name,
                label=label,
                title=title,
                color=_DS_COLORS.get(ds.dataset_type, "#CCCCCC"),
                shape="ellipse",
                size=20,
            )

        # Add transformation nodes
        for xform in self._transformations.values():
            label = xform.source_file.split("/")[-1] if "/" in xform.source_file else xform.source_file
            title = (
                f"<b>{xform.id}</b><br>"
                f"Type: {xform.transformation_type}<br>"
                f"File: {xform.source_file}<br>"
                f"Confidence: {xform.confidence}"
            )
            if xform.is_dynamic:
                title += "<br><i>⚠ Dynamic/unresolved</i>"

            net.add_node(
                xform.id,
                label=label,
                title=title,
                color=_XFORM_COLOR,
                shape="box",
                size=15,
            )

        # Add edges — only PRODUCES and CONSUMES
        for u, v, data in self._g.edges(data=True):
            edge_type = data.get("edge_type", "")
            if edge_type == "PRODUCES":
                net.add_edge(u, v, color="#2ECC71", title="produces", arrows="to")
            elif edge_type == "CONSUMES":
                net.add_edge(u, v, color="#E74C3C", title="consumes", arrows="to")

        try:
            net.save_graph(str(output_path))
            logger.info("Saved lineage visualization (PyVis) → %s", output_path)
            return True
        except Exception as exc:
            logger.warning("PyVis save failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Lineage analytics
    # ------------------------------------------------------------------

    def lineage_summary(self) -> dict[str, Any]:
        """Return summary statistics for the lineage subgraph."""
        produces_edges = sum(
            1 for _, _, d in self._g.edges(data=True) if d.get("edge_type") == "PRODUCES"
        )
        consumes_edges = sum(
            1 for _, _, d in self._g.edges(data=True) if d.get("edge_type") == "CONSUMES"
        )
        by_type: dict[str, int] = {}
        for ds in self._datasets.values():
            by_type[ds.dataset_type] = by_type.get(ds.dataset_type, 0) + 1

        return {
            "total_datasets": len(self._datasets),
            "total_transformations": len(self._transformations),
            "produces_edges": produces_edges,
            "consumes_edges": consumes_edges,
            "datasets_by_type": by_type,
            "dynamic_transformations": sum(
                1 for t in self._transformations.values() if t.is_dynamic
            ),
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

    def save_lineage(self, output_path: Path) -> None:
        """
        Save lineage data (datasets + transformations) to a JSON file.

        Separate from the module graph so Phase 2 artifacts are cleanly isolated.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        lineage_data = {
            "datasets": {
                name: ds.model_dump(mode="json")
                for name, ds in self._datasets.items()
            },
            "transformations": {
                tid: xform.model_dump(mode="json")
                for tid, xform in self._transformations.items()
            },
            "edges": [
                {"source": u, "target": v, **d}
                for u, v, d in self._g.edges(data=True)
                if d.get("edge_type") in ("PRODUCES", "CONSUMES")
            ],
        }
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(lineage_data, fh, indent=2, default=str)
        logger.info("Saved lineage graph → %s", output_path)

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
