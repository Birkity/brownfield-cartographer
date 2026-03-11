"""
Graph analytics — PageRank, SCC, dead-code detection, and summary statistics.

These functions are mixed into KnowledgeGraph via composition: they receive
the graph state as arguments rather than `self`, making them unit-testable
without a full KnowledgeGraph instance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import networkx as nx

if TYPE_CHECKING:
    from src.graph.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


def compute_pagerank(g: nx.DiGraph, alpha: float = 0.85) -> dict[str, float]:
    """Return PageRank scores ({node: score}).  Falls back to degree centrality."""
    if g.number_of_nodes() == 0:
        return {}
    try:
        return nx.pagerank(g, alpha=alpha)
    except nx.PowerIterationFailedConvergence:
        logger.warning("PageRank did not converge; returning degree centrality")
        return nx.degree_centrality(g)


def compute_sccs(g: nx.DiGraph) -> list[list[str]]:
    """Return non-trivial strongly-connected components (circular deps)."""
    sccs = list(nx.strongly_connected_components(g))
    cycles = [sorted(scc) for scc in sccs if len(scc) > 1]
    return sorted(cycles, key=len, reverse=True)


def compute_dead_code_candidates(g: nx.DiGraph, modules: dict) -> list[str]:
    """
    Return module paths whose in-degree is 0.

    Applies language-specific guards:
    - SQL: only flagged when dbt ref edges have been built
    - JS/TS: only flagged when at least one import edge exists
    - Python: always flagged when in-degree == 0
    """
    from src.models.nodes import Language

    _JS_ENTRY_POINTS = frozenset({
        "index.js", "index.ts", "index.tsx", "index.jsx",
        "main.js", "main.ts", "main.jsx",
        "app.js", "app.ts", "app.tsx", "app.jsx",
        "server.js", "server.ts",
    })

    has_dbt_refs = any(
        m.language == Language.SQL and len(m.dbt_refs) > 0
        for m in modules.values()
    )
    has_js_imports = any(
        m.language in (Language.JAVASCRIPT, Language.TYPESCRIPT) and len(m.imports) > 0
        for m in modules.values()
    )

    candidates = []
    for path, mod in modules.items():
        if g.in_degree(path) != 0:
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


def compute_hub_modules(
    g: nx.DiGraph,
    modules: dict,
    top_n: int = 10,
) -> list[tuple[str, float]]:
    """Return the top-N modules by PageRank score (filtered to tracked modules)."""
    scores = compute_pagerank(g)
    module_scores = [
        (path, score) for path, score in scores.items() if path in modules
    ]
    return sorted(module_scores, key=lambda x: x[1], reverse=True)[:top_n]


def compute_graph_summary(g: nx.DiGraph, modules: dict) -> dict[str, Any]:
    """Return a human-readable summary dict for logging."""
    sccs = compute_sccs(g)
    by_lang: dict[str, int] = {}
    parse_errors = 0
    grammar_missing = 0
    for mod in modules.values():
        by_lang[mod.language.value] = by_lang.get(mod.language.value, 0) + 1
        if mod.parse_error:
            if "not installed" in mod.parse_error:
                grammar_missing += 1
            else:
                parse_errors += 1
    return {
        "total_modules": len(modules),
        "by_language": by_lang,
        "total_nodes": g.number_of_nodes(),
        "total_edges": g.number_of_edges(),
        "circular_dependency_clusters": len(sccs),
        "largest_cycle_size": len(sccs[0]) if sccs else 0,
        "dead_code_candidates": len(compute_dead_code_candidates(g, modules)),
        "grammar_not_available": grammar_missing,
        "real_parse_errors": parse_errors,
        "hub_modules": compute_hub_modules(g, modules, top_n=5),
    }


def compute_lineage_summary(g: nx.DiGraph, datasets: dict, transformations: dict) -> dict[str, Any]:
    """Return summary statistics for the lineage subgraph."""
    produces_edges = sum(
        1 for _, _, d in g.edges(data=True) if d.get("edge_type") == "PRODUCES"
    )
    consumes_edges = sum(
        1 for _, _, d in g.edges(data=True) if d.get("edge_type") == "CONSUMES"
    )
    by_type: dict[str, int] = {}
    for ds in datasets.values():
        by_type[ds.dataset_type] = by_type.get(ds.dataset_type, 0) + 1

    return {
        "total_datasets": len(datasets),
        "total_transformations": len(transformations),
        "produces_edges": produces_edges,
        "consumes_edges": consumes_edges,
        "datasets_by_type": by_type,
        "dynamic_transformations": sum(
            1 for t in transformations.values() if t.is_dynamic
        ),
    }
