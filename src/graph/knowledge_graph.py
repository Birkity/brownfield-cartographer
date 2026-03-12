"""
KnowledgeGraph: NetworkX-backed graph store for the Brownfield Cartographer.

Wraps a NetworkX DiGraph with typed node/edge APIs and handles serialization.
Analytics, visualization, and lineage helpers are delegated to:

  - src.graph.graph_analytics  — PageRank, SCC, dead-code, summary
  - src.graph.graph_viz        — PNG (matplotlib) + HTML (PyVis) exports

Design notes:
  - Nodes are keyed by POSIX relative path (modules) or dataset name (datasets).
  - All node data is stored as JSON-serializable dicts.
  - Phase 1 builds the module IMPORTS graph.
  - Phase 2 adds PRODUCES/CONSUMES dataset edges.
  - The graph is persisted as NetworkX node-link JSON.
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
    Central graph store shared across all agents in a run.

    Usage::

        graph = KnowledgeGraph()
        graph.add_module(module_node)
        graph.add_import_edge("src/foo.py", "src/bar.py")
        graph.save(Path(".cartography/phase1/module_graph.json"))
    """

    def __init__(self) -> None:
        self._g: nx.DiGraph = nx.DiGraph()
        self._modules: dict[str, ModuleNode] = {}
        self._datasets: dict[str, DatasetNode] = {}
        self._transformations: dict[str, TransformationNode] = {}

    # ------------------------------------------------------------------
    # Module node management
    # ------------------------------------------------------------------

    def add_module(self, module: ModuleNode) -> None:
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
            # Polish-layer fields (set by enrichment.py)
            role=module.role,
            is_entry_point=module.is_entry_point,
            is_hub=module.is_hub,
            in_cycle=module.in_cycle,
            classification_confidence=module.classification_confidence,
            # Phase 3 (Semanticist) fields
            purpose_statement=module.purpose_statement,
            business_logic_score=module.business_logic_score,
            domain_cluster=module.domain_cluster,
            doc_drift_detected=module.doc_drift_detected,
            semantic_confidence=module.semantic_confidence,
        )

    def get_module(self, path: str) -> Optional[ModuleNode]:
        return self._modules.get(path)

    def all_modules(self) -> list[ModuleNode]:
        return list(self._modules.values())

    # ------------------------------------------------------------------
    # Import edge management
    # ------------------------------------------------------------------

    def add_import_edge(
        self,
        source: str,
        target: str,
        edge_type: str = "IMPORTS",
        confidence: float = 0.95,
        evidence: Optional[dict] = None,
    ) -> None:
        if not self._g.has_node(target):
            self._g.add_node(target, language="external", lines_of_code=0)
        if self._g.has_edge(source, target):
            self._g[source][target]["import_count"] += 1
        else:
            self._g.add_edge(
                source, target,
                edge_type=edge_type,
                import_count=1,
                confidence=confidence,
                evidence=evidence or {},
            )

    # ------------------------------------------------------------------
    # Dataset / transformation node management (Phase 2)
    # ------------------------------------------------------------------

    def add_dataset_node(self, dataset: DatasetNode) -> None:
        existing = self._datasets.get(dataset.name)
        if existing and existing.confidence >= dataset.confidence:
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
            is_source_dataset=dataset.is_source_dataset,
            is_sink_dataset=dataset.is_sink_dataset,
            is_final_model=dataset.is_final_model,
            is_intermediate_model=dataset.is_intermediate_model,
        )

    def add_transformation_node(self, transformation: TransformationNode) -> None:
        self._transformations[transformation.id] = transformation
        self._g.add_node(
            transformation.id,
            node_type="transformation",
            transformation_type=transformation.transformation_type,
            source_file=transformation.source_file,
            confidence=transformation.confidence,
            is_dynamic=transformation.is_dynamic,
        )

    def add_produces_edge(
        self,
        transformation_id: str,
        dataset_name: str,
        confidence: float = 1.0,
        evidence: Optional[dict] = None,
    ) -> None:
        if not self._g.has_node(dataset_name):
            self._g.add_node(dataset_name, node_type="dataset")
        if not self._g.has_node(transformation_id):
            self._g.add_node(transformation_id, node_type="transformation")
        self._g.add_edge(
            transformation_id, dataset_name,
            edge_type="PRODUCES",
            confidence=confidence,
            evidence=evidence or {},
        )

    def add_consumes_edge(
        self,
        transformation_id: str,
        dataset_name: str,
        confidence: float = 1.0,
        evidence: Optional[dict] = None,
    ) -> None:
        if not self._g.has_node(dataset_name):
            self._g.add_node(dataset_name, node_type="dataset")
        if not self._g.has_node(transformation_id):
            self._g.add_node(transformation_id, node_type="transformation")
        self._g.add_edge(
            dataset_name, transformation_id,
            edge_type="CONSUMES",
            confidence=confidence,
            evidence=evidence or {},
        )

    def get_dataset(self, name: str) -> Optional[DatasetNode]:
        return self._datasets.get(name)

    def all_datasets(self) -> list[DatasetNode]:
        return list(self._datasets.values())

    def all_transformations(self) -> list[TransformationNode]:
        return list(self._transformations.values())

    # ------------------------------------------------------------------
    # Analytics (delegated to graph_analytics)
    # ------------------------------------------------------------------

    def pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        from src.graph.graph_analytics import compute_pagerank
        return compute_pagerank(self._g, alpha=alpha)

    def strongly_connected_components(self) -> list[list[str]]:
        from src.graph.graph_analytics import compute_sccs
        return compute_sccs(self._g)

    def dead_code_candidates(self) -> list[str]:
        from src.graph.graph_analytics import compute_dead_code_candidates
        return compute_dead_code_candidates(self._g, self._modules)

    def hub_modules(self, top_n: int = 10) -> list[tuple[str, float]]:
        from src.graph.graph_analytics import compute_hub_modules
        return compute_hub_modules(self._g, self._modules, top_n=top_n)

    def summary(self) -> dict[str, Any]:
        from src.graph.graph_analytics import compute_graph_summary
        return compute_graph_summary(self._g, self._modules)

    def lineage_summary(self) -> dict[str, Any]:
        from src.graph.graph_analytics import compute_lineage_summary
        return compute_lineage_summary(self._g, self._datasets, self._transformations)

    # ------------------------------------------------------------------
    # Visualization (delegated to graph_viz)
    # ------------------------------------------------------------------

    def export_viz(self, output_path: Path) -> bool:
        from src.graph.graph_viz import export_module_viz_html
        return export_module_viz_html(self._g, output_path)

    def export_lineage_viz(self, output_path: Path) -> bool:
        from src.graph.graph_viz import export_lineage_viz
        return export_lineage_viz(self._g, self._datasets, self._transformations, output_path)

    # ------------------------------------------------------------------
    # Semantic metadata helpers (Phase 3)
    # ------------------------------------------------------------------

    def save_semantics(self, output_path: Path, semantics_data: dict) -> None:
        """Persist semantic enrichment data (purpose statements, domains, drift)."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(semantics_data, fh, indent=2, default=str)
        logger.info("Saved semantic enrichment → %s", output_path)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def save(self, output_path: Path) -> None:
        """Serialise graph topology + module records to JSON."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(self._g)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        logger.info("Saved module graph → %s", output_path)

        modules_path = output_path.with_stem(output_path.stem + "_modules")
        modules_data = {path: mod.model_dump(mode="json") for path, mod in self._modules.items()}
        with modules_path.open("w", encoding="utf-8") as fh:
            json.dump(modules_data, fh, indent=2, default=str)
        logger.info("Saved module details → %s", modules_path)

    def save_lineage(self, output_path: Path) -> None:
        """Serialise lineage datasets + transformations + edges to JSON."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lineage_data = {
            "datasets": {
                name: ds.model_dump(mode="json") for name, ds in self._datasets.items()
            },
            "transformations": {
                tid: xform.model_dump(mode="json") for tid, xform in self._transformations.items()
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
        """Load a previously-saved graph from node-link JSON."""
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
