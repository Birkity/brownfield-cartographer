"""
Domain clusterer — groups modules/transformations into logical business domains.

Strategy:
  1. Heuristic-first: obvious groupings from path patterns and roles.
  2. Semantic refinement: uses LLM to label and refine clusters using
     purpose statements produced by the semantic extractor.
  3. Explainable: every domain assignment has a reasoning field.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from src.llm.model_router import ModelRouter, TaskType
from src.llm.ollama_client import ContextWindowBudget
from src.llm.prompt_builder import DOMAIN_CLUSTERING_PROMPT, SYSTEM_SYNTHESIS

if TYPE_CHECKING:
    from src.analyzers.semantic_extractor import PurposeResult
    from src.graph.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


class DomainCluster(BaseModel):
    """A single domain cluster with its member modules."""

    domain_name: str
    description: str = ""
    members: list[str] = Field(default_factory=list)
    reasoning: str = ""


class ClusteringResult(BaseModel):
    """Full domain clustering output."""

    domains: list[DomainCluster] = Field(default_factory=list)
    confidence: float = 0.0
    method: str = "heuristic"
    model_used: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Heuristic clustering (always works, no LLM needed)
# ---------------------------------------------------------------------------

_HEURISTIC_DOMAINS: dict[str, str] = {
    "staging": "Data Staging",
    "source": "Data Ingestion",
    "intermediate": "Data Transformation",
    "mart": "Analytics & Marts",
    "macro": "Shared Macros & Utilities",
    "test": "Testing",
    "config": "Configuration & Orchestration",
    "utility": "Utilities & Helpers",
    "unknown": "Uncategorized",
}


def _extract_subject_from_dataset(ds_name: str) -> str:
    """Extract a business subject noun from a dataset reference name.

    Examples::

        'model.stg_orders'   → 'orders'
        'source.raw.customers' → 'customers'
        'stg_order_items'    → 'order_items'
    """
    name = ds_name.rsplit(".", 1)[-1].lower()
    for prefix in ("stg_", "int_", "fct_", "dim_", "mart_", "raw_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name if len(name) >= 3 else ""


def _heuristic_cluster(
    graph: "KnowledgeGraph",
) -> ClusteringResult:
    """Group modules using lineage dataset subjects with a role-based fallback.

    For each SQL transformation, the dominant dataset subject (e.g. 'orders',
    'customers') is used as the domain key.  Modules not touched by any
    transformation fall back to the role-based ``_HEURISTIC_DOMAINS`` mapping.
    This produces subject-oriented groups ("Orders Pipeline") rather than
    purely structural ones ("Data Staging").
    """
    subject_by_module: dict[str, str] = {}
    for xform in graph.all_transformations():
        if not xform.source_file:
            continue
        subject_counts: dict[str, int] = defaultdict(int)
        for ds in xform.source_datasets + xform.target_datasets:
            subj = _extract_subject_from_dataset(ds)
            if subj:
                subject_counts[subj] += 1
        if subject_counts:
            dominant = max(subject_counts, key=subject_counts.__getitem__)
            subject_by_module[xform.source_file] = dominant

    groups: dict[str, list[str]] = defaultdict(list)
    for module in graph.all_modules():
        subject = subject_by_module.get(module.path)
        if subject:
            domain = f"{subject.replace('_', ' ').title()} Pipeline"
        else:
            domain = _HEURISTIC_DOMAINS.get(module.role, "Uncategorized")
        groups[domain].append(module.path)

    domains = [
        DomainCluster(
            domain_name=name,
            description=(
                f"Modules grouped into the {name.lower()} domain "
                f"via lineage dataset subjects and role heuristics."
            ),
            members=sorted(members),
            reasoning="Heuristic: lineage dataset subject extraction + role-based fallback.",
        )
        for name, members in sorted(groups.items())
        if members
    ]

    return ClusteringResult(
        domains=domains,
        confidence=0.7,
        method="heuristic",
    )


# ---------------------------------------------------------------------------
# LLM-refined clustering
# ---------------------------------------------------------------------------

def _llm_refine_clusters(
    graph: "KnowledgeGraph",
    purpose_results: list["PurposeResult"],
    router: ModelRouter,
    budget: ContextWindowBudget,
) -> Optional[ClusteringResult]:
    """Use the LLM to produce more semantically meaningful domain clusters.

    Falls back to None if the LLM call fails.
    """
    # Build compact module summaries for the prompt
    module_data = []
    for pr in purpose_results:
        if pr.purpose_statement and pr.confidence >= 0.3:
            module = graph.get_module(pr.file_path)
            module_data.append({
                "path": pr.file_path,
                "role": module.role if module else "unknown",
                "language": module.language.value if module else "unknown",
                "purpose": pr.purpose_statement,
                "key_concepts": pr.key_concepts[:5],
                "business_logic_score": pr.business_logic_score,
            })

    if len(module_data) < 3:
        logger.info("Too few purpose statements (%d) for LLM clustering", len(module_data))
        return None

    # Truncate to fit context window
    modules_json = json.dumps(module_data[:60], indent=1)
    if not budget.can_fit(modules_json):
        modules_json = json.dumps(module_data[:30], indent=1)

    prompt = DOMAIN_CLUSTERING_PROMPT.format(modules_json=modules_json)

    resp, selection = router.generate(
        task=TaskType.DOMAIN_CLUSTERING,
        prompt=prompt,
        system=SYSTEM_SYNTHESIS,
        temperature=0.2,
        max_tokens=2048,
        format_json=True,
    )
    budget.record(resp)

    if not resp.success:
        logger.warning("LLM clustering failed: %s", resp.error)
        return None

    parsed = resp.parse_json()
    if not parsed or "domains" not in parsed:
        logger.warning("LLM clustering returned unparseable response")
        return None

    domains = []
    for d in parsed["domains"]:
        domains.append(DomainCluster(
            domain_name=d.get("domain_name", "Unknown"),
            description=d.get("description", ""),
            members=d.get("members", []),
            reasoning=d.get("reasoning", ""),
        ))

    return ClusteringResult(
        domains=domains,
        confidence=float(parsed.get("confidence", 0.6)),
        method="llm_refined",
        model_used=resp.model,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cluster_into_domains(
    graph: "KnowledgeGraph",
    purpose_results: list["PurposeResult"],
    router: Optional[ModelRouter] = None,
    budget: Optional[ContextWindowBudget] = None,
) -> ClusteringResult:
    """Cluster modules into business domains.

    Uses heuristic grouping as the baseline.  If an LLM is available and
    purpose statements exist, refines with semantic clustering.
    """
    # Always compute heuristic baseline
    heuristic = _heuristic_cluster(graph)

    # Attempt LLM refinement
    if router and budget and purpose_results:
        llm_result = _llm_refine_clusters(graph, purpose_results, router, budget)
        if llm_result and llm_result.domains:
            logger.info(
                "LLM clustering produced %d domains (confidence=%.2f)",
                len(llm_result.domains), llm_result.confidence,
            )
            return llm_result

    logger.info("Using heuristic clustering (%d domains)", len(heuristic.domains))
    return heuristic
