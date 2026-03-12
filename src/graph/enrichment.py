"""
Enrichment: confidence scoring and node classification.

Applied after Phase 1 (Surveyor) and Phase 2 (Hydrologist) have finished
building the KnowledgeGraph.  All functions are pure: they accept the
KnowledgeGraph as an argument rather than operating as class methods.

Design principles:
- Never fabricate certainty — prefer explicit "unknown" over wrong labels.
- Heuristics are documented inline so they can be audited or overridden.
- Works for dbt repos (primary target) and general codebases (graceful fallback).
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.graph.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Confidence scoring for edges
# ---------------------------------------------------------------------------

_EXTRACTION_CONFIDENCE: dict[str, float] = {
    "tree_sitter_ast":       1.0,   # deterministic AST-level extraction
    "dbt_jinja_regex":       1.0,   # exact {{ ref() }} / {{ source() }} match
    "sqlglot":               0.90,  # sqlglot + AST, occasionally ambiguous due to CTEs
    "sqlglot_dynamic":       0.55,  # sqlglot on Jinja-heavy SQL with unresolved vars
    "regex":                 0.65,  # plain regex extraction (fallback)
    "config_parsing":        0.95,  # YAML config declared explicitly
    "inferred":              0.40,  # heuristic / pattern-match with no AST backing
}


def confidence_for_method(extraction_method: str) -> float:
    """Return a 0–1 confidence score for a given extraction method label."""
    return _EXTRACTION_CONFIDENCE.get(extraction_method, 0.60)


# ---------------------------------------------------------------------------
# 2. Module role classification
# ---------------------------------------------------------------------------

def _infer_module_role(path: str, language: str) -> tuple[str, float]:
    """
    Infer the semantic role of a module from its path and language.

    Returns (role, confidence) where confidence reflects how strong the
    path-only heuristic is.

    dbt heuristics:
      - seeds/                → source  (CSV inputs)
      - sources.yml / __sources.yml  → config
      - macros/               → macro
      - tests/                → test
      - stg_*                 → staging
      - int_*                 → intermediate
      - fct_* / dim_*         → mart
      - models/marts/*        → mart
      - models/staging/*      → staging
      - models/intermediate/* → intermediate
      - dbt_project.yml / packages.yml → config

    General heuristics:
      - conftest / test_*     → test
      - __init__ / setup.py   → utility
      - config.* / settings.* → config
      - utils / helpers       → utility
    """
    posix = path.replace("\\", "/").lower()
    stem = PurePosixPath(posix).stem
    parts = posix.split("/")

    # dbt-specific path patterns
    if "seeds/" in posix:
        return "source", 1.0
    if "macros/" in posix:
        return "macro", 0.95
    if "/tests/" in posix or posix.startswith("tests/"):
        return "test", 0.95
    if stem in ("dbt_project", "packages", "profiles") and language == "yaml":
        return "config", 1.0
    if "sources" in stem and language == "yaml":
        return "config", 0.90
    if stem.startswith("stg_"):
        return "staging", 0.95
    if stem.startswith("int_"):
        return "intermediate", 0.90
    if stem.startswith("fct_") or stem.startswith("dim_"):
        return "mart", 0.95
    if "marts/" in posix or "/mart/" in posix:
        return "mart", 0.85
    if "staging/" in posix:
        return "staging", 0.80
    if "intermediate/" in posix:
        return "intermediate", 0.80

    # General heuristics
    if stem.startswith("test_") or stem == "conftest":
        return "test", 0.90
    if stem in ("__init__", "setup", "setup.cfg"):
        return "utility", 0.80
    if stem in ("config", "settings", "configuration"):
        return "config", 0.80
    if language == "yaml":
        return "config", 0.60
    if "utils" in parts or "helpers" in parts or "util" in parts:
        return "utility", 0.75

    return "unknown", 0.0


def classify_module_roles(graph: "KnowledgeGraph") -> int:
    """
    Set role, is_entry_point, is_hub, in_cycle, classification_confidence
    on every ModuleNode.

    Returns the number of modules whose role changed from 'unknown'.
    """
    import networkx as nx  # already a dependency

    g = graph._g

    # Compute graph metrics needed for classification
    hubs_set = {path for path, _ in graph.hub_modules(top_n=10)}
    cycle_nodes: set[str] = set()
    for scc in graph.strongly_connected_components():
        cycle_nodes.update(scc)

    classified = 0
    for module in graph.all_modules():
        role, conf = _infer_module_role(module.path, module.language.value)
        module.role = role
        module.classification_confidence = conf if conf > 0 else 0.5  # at least 0.5 for unknown
        module.is_entry_point = (g.in_degree(module.path) == 0) if g.has_node(module.path) else False
        module.is_hub = module.path in hubs_set
        module.in_cycle = module.path in cycle_nodes

        # Re-add to graph so the node attributes are updated
        graph.add_module(module)

        if role != "unknown":
            classified += 1

    logger.info("Enrichment: classified %d/%d modules with a named role",
                classified, len(graph.all_modules()))
    return classified


# ---------------------------------------------------------------------------
# 3. Dataset role classification
# ---------------------------------------------------------------------------

def classify_dataset_roles(graph: "KnowledgeGraph") -> int:
    """
    Set is_source_dataset, is_sink_dataset, is_final_model, is_intermediate_model
    on every DatasetNode.

    Returns the number of datasets whose classification was updated.
    """
    g = graph._g
    updated = 0

    for dataset in graph.all_datasets():
        n = dataset.name
        if not g.has_node(n):
            continue

        # is_source_dataset: nothing produces this dataset (no incoming PRODUCES edges)
        incoming_produces = any(
            d.get("edge_type") == "PRODUCES"
            for _, _, d in g.in_edges(n, data=True)
        )
        # is_sink_dataset: no transformation consumes this dataset
        outgoing_consumes = any(
            d.get("edge_type") == "CONSUMES"
            for _, _, d in g.out_edges(n, data=True)
        )

        dataset.is_source_dataset = not incoming_produces
        dataset.is_sink_dataset = not outgoing_consumes

        # dbt-specific classification
        stem = n.split(".")[-1].lower() if "." in n else n.lower()
        if dataset.dataset_type in ("dbt_model", "table_ref"):
            if stem.startswith(("fct_", "dim_")) or "/marts/" in n:
                dataset.is_final_model = True
                dataset.is_intermediate_model = False
            elif stem.startswith(("stg_", "int_")):
                dataset.is_intermediate_model = True
                dataset.is_final_model = False
        elif dataset.dataset_type in ("dbt_source", "dbt_seed"):
            dataset.is_source_dataset = True

        # Re-register to update graph node attrs
        graph.add_dataset_node(dataset)
        updated += 1

    logger.info("Enrichment: classified %d datasets (source/sink/final/intermediate)", updated)
    return updated
