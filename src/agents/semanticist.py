"""
Semanticist Agent â€” Phase 3 of the Brownfield Cartographer pipeline.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.analyzers.doc_drift_detector import DriftResult, detect_all_drift
from src.analyzers.domain_clusterer import ClusteringResult, cluster_into_domains
from src.analyzers.semantic_extractor import PurposeResult, extract_all_purposes
from src.graph.knowledge_graph import KnowledgeGraph
from src.llm.model_router import ModelRouter, TaskType
from src.llm.ollama_client import ContextWindowBudget, OllamaClient
from src.llm.prompt_builder import (
    DAY_ONE_SYNTHESIS_PROMPT,
    DAY_ONE_SYNTHESIS_PROMPT_VERSION,
    SYSTEM_SYNTHESIS,
)
from src.models.nodes import AnalysisMethod, DayOneCitation, ModuleNode, SemanticEvidence, TraceEntry

logger = logging.getLogger(__name__)


@dataclass
class SemanticsResult:
    purpose_results: list[PurposeResult] = field(default_factory=list)
    clustering: Optional[ClusteringResult] = None
    drift_results: list[DriftResult] = field(default_factory=list)
    day_one_answers: Optional[dict[str, Any]] = None
    reading_order: list[dict[str, Any]] = field(default_factory=list)
    hotspot_rankings: list[dict[str, Any]] = field(default_factory=list)
    review_queue: list[dict[str, Any]] = field(default_factory=list)
    budget_summary: dict[str, Any] = field(default_factory=dict)
    trace: list[TraceEntry] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    ollama_available: bool = False


def _reading_reason(module: ModuleNode, pr: Optional[PurposeResult]) -> str:
    reasons: list[str] = []
    if module.is_hub:
        reasons.append("architectural hub")
    if module.hotspot_fusion_score >= 0.7:
        reasons.append("high hotspot score")
    if pr and pr.business_logic_score >= 0.7:
        reasons.append("core business logic")
    elif pr and pr.business_logic_score >= 0.4:
        reasons.append("significant business logic")
    if module.role == "mart":
        reasons.append("analytical output")
    elif module.role == "staging":
        reasons.append("data foundation")
    elif module.role == "macro":
        reasons.append("shared utility")
    if module.is_entry_point:
        reasons.append("entry point")
    return "; ".join(reasons) if reasons else "supporting module"


class Semanticist:
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        override_model: Optional[str] = None,
        timeout: int = 120,
        max_modules: int = 100,
    ) -> None:
        self._ollama_url = ollama_url
        self._override_model = override_model
        self._timeout = timeout
        self._max_modules = max_modules

    def run(self, graph: KnowledgeGraph, repo_root: Path) -> SemanticsResult:
        t0 = time.monotonic()
        result = SemanticsResult()
        trace: list[TraceEntry] = []
        budget = ContextWindowBudget()

        client = OllamaClient(base_url=self._ollama_url, timeout=self._timeout)
        result.ollama_available = client.is_available()
        router = ModelRouter(client, override_model=self._override_model)

        trace.append(TraceEntry(
            agent="semanticist",
            action="init",
            target="ollama",
            result="available" if result.ollama_available else "unavailable",
            confidence=1.0,
            analysis_method=AnalysisMethod.LLM_INFERENCE,
        ))

        result.purpose_results = extract_all_purposes(
            graph,
            router if result.ollama_available else None,
            budget,
            max_modules=self._max_modules,
        )
        result.clustering = cluster_into_domains(
            graph,
            result.purpose_results,
            router=router if result.ollama_available else None,
            budget=budget if result.ollama_available else None,
        )
        result.drift_results = detect_all_drift(
            graph,
            result.purpose_results,
            router if result.ollama_available else None,
            budget if result.ollama_available else None,
            max_modules=min(self._max_modules, 50),
        ) if result.purpose_results else []
        result.hotspot_rankings = self._compute_hotspot_rankings(graph, result)
        result.review_queue = self._build_review_queue(graph, result)
        result.day_one_answers = self._synthesize_day_one(
            graph,
            result,
            router if result.ollama_available else None,
            budget,
        )
        self._enrich_graph(graph, result)
        result.reading_order = self._compute_reading_order(graph, result)

        drift_count = sum(1 for item in result.drift_results if item.drift_level in ("possible_drift", "likely_drift"))
        doc_missing = sum(1 for item in result.drift_results if item.documentation_missing)
        trace.extend([
            TraceEntry(
                agent="semanticist",
                action="purpose_extraction",
                target=f"{len(result.purpose_results)} modules",
                result=f"{sum(1 for item in result.purpose_results if item.purpose_statement)} purpose statements generated",
                confidence=0.8 if result.ollama_available else 0.3,
                analysis_method=AnalysisMethod.LLM_INFERENCE if result.ollama_available else AnalysisMethod.STATIC_ANALYSIS,
            ),
            TraceEntry(
                agent="semanticist",
                action="domain_clustering",
                target=f"{len(result.clustering.domains)} domains",
                result=f"method={result.clustering.method}, confidence={result.clustering.confidence:.2f}",
                confidence=result.clustering.confidence,
                analysis_method=AnalysisMethod.LLM_INFERENCE if result.clustering.method == "llm_refined" else AnalysisMethod.STATIC_ANALYSIS,
            ),
            TraceEntry(
                agent="semanticist",
                action="doc_drift_detection",
                target=f"{len(result.drift_results)} modules checked",
                result=f"{drift_count} with drift, {doc_missing} missing docs",
                confidence=0.7 if result.ollama_available else 0.5,
                analysis_method=AnalysisMethod.LLM_INFERENCE if result.ollama_available else AnalysisMethod.STATIC_ANALYSIS,
            ),
            TraceEntry(
                agent="semanticist",
                action="hotspot_fusion",
                target=f"{len(result.hotspot_rankings)} modules",
                result="semantic hotspot rankings generated",
                confidence=0.9,
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            ),
            TraceEntry(
                agent="semanticist",
                action="day_one_synthesis",
                target="onboarding_questions",
                result="generated" if result.day_one_answers else "failed",
                confidence=0.7 if result.day_one_answers else 0.0,
                analysis_method=AnalysisMethod.LLM_INFERENCE if result.ollama_available else AnalysisMethod.STATIC_ANALYSIS,
            ),
        ])

        elapsed = time.monotonic() - t0
        result.budget_summary = budget.summary()
        result.trace = trace
        result.stats = {
            "ollama_available": result.ollama_available,
            "purpose_statements_generated": sum(1 for item in result.purpose_results if item.purpose_statement),
            "purpose_statements_attempted": len(result.purpose_results),
            "domains_found": len(result.clustering.domains) if result.clustering else 0,
            "clustering_method": result.clustering.method if result.clustering else "none",
            "drift_checks_performed": len(result.drift_results),
            "drift_detected": drift_count,
            "documentation_missing_count": doc_missing,
            "day_one_answers_generated": bool(result.day_one_answers),
            "reading_order_items": len(result.reading_order),
            "semantic_hotspots": len(result.hotspot_rankings),
            "review_queue_items": len(result.review_queue),
            "llm_budget": result.budget_summary,
            "elapsed_seconds": round(elapsed, 2),
        }
        return result

    def _purpose_lookup(self, semantics: SemanticsResult) -> dict[str, PurposeResult]:
        return {item.file_path: item for item in semantics.purpose_results if item.purpose_statement}

    def _drift_lookup(self, semantics: SemanticsResult) -> dict[str, DriftResult]:
        return {item.file_path: item for item in semantics.drift_results}

    def _module_citations(self, module: ModuleNode, semantics: SemanticsResult, evidence_type: str = "semantic", limit: int = 3) -> list[DayOneCitation]:
        sources: list[SemanticEvidence] = []
        purpose = self._purpose_lookup(semantics).get(module.path)
        drift = self._drift_lookup(semantics).get(module.path)
        if purpose:
            sources.extend(purpose.evidence)
        if drift:
            sources.extend(drift.evidence)
        if not sources:
            sources.extend(module.semantic_evidence)
        citations: list[DayOneCitation] = []
        seen: set[tuple[str, Optional[int], Optional[int], str]] = set()
        for evidence in sources:
            evidence_path = evidence.file_path or module.path
            if evidence_path.endswith(module.path):
                evidence_path = module.path
            key = (evidence_path, evidence.line_start, evidence.line_end, evidence.description)
            if key in seen:
                continue
            seen.add(key)
            citations.append(DayOneCitation(
                file_path=evidence_path,
                line_start=evidence.line_start,
                line_end=evidence.line_end,
                evidence_type=evidence_type,
                source_phase=evidence.source_phase,
                extraction_method=evidence.extraction_method,
                description=evidence.description,
            ))
        return citations[:limit]

    def _normalize_day_one_answers(self, payload: dict[str, Any], graph: KnowledgeGraph, semantics: SemanticsResult) -> dict[str, Any]:
        module_map = {module.path: module for module in graph.all_modules()}
        normalized_questions: list[dict[str, Any]] = []
        for item in payload.get("questions", []):
            if not isinstance(item, dict):
                continue
            citations: list[DayOneCitation] = []
            for raw in item.get("citations", []):
                if not isinstance(raw, dict):
                    continue
                citations.append(DayOneCitation(
                    file_path=str(raw.get("file_path", "")),
                    line_start=raw.get("line_start"),
                    line_end=raw.get("line_end"),
                    evidence_type=str(raw.get("evidence_type", "semantic")),
                    source_phase=str(raw.get("source_phase", "phase3")),
                    extraction_method=str(raw.get("extraction_method", "llm_inference")),
                    description=str(raw.get("description", "")),
                ))
            enriched_citations: list[DayOneCitation] = []
            for citation in citations:
                if citation.line_start is not None and citation.line_end is not None:
                    enriched_citations.append(citation)
                    continue
                module = module_map.get(citation.file_path)
                if module is None:
                    enriched_citations.append(citation)
                    continue
                module_citations = self._module_citations(module, semantics, evidence_type=citation.evidence_type, limit=1)
                if module_citations:
                    fallback = module_citations[0]
                    citation.line_start = fallback.line_start
                    citation.line_end = fallback.line_end
                    citation.source_phase = fallback.source_phase
                    citation.extraction_method = fallback.extraction_method
                    if not citation.description:
                        citation.description = fallback.description
                enriched_citations.append(citation)
            citations = enriched_citations
            if not citations:
                for path in item.get("cited_files", []):
                    module = module_map.get(str(path))
                    if module:
                        citations.extend(self._module_citations(module, semantics))
            cited_files = list(dict.fromkeys([citation.file_path for citation in citations if citation.file_path] + [str(path) for path in item.get("cited_files", [])]))
            normalized_questions.append({
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "cited_files": cited_files,
                "citations": [citation.model_dump(mode="json") for citation in citations],
                "confidence": float(item.get("confidence", 0.5)),
            })
        return {"prompt_version": DAY_ONE_SYNTHESIS_PROMPT_VERSION, "questions": normalized_questions}

    def _heuristic_day_one(self, graph: KnowledgeGraph, semantics: SemanticsResult) -> dict[str, Any]:
        hotspots = semantics.hotspot_rankings[:3]
        top_modules = [graph.get_module(item["file_path"]) for item in hotspots]
        top_modules = [module for module in top_modules if module is not None]
        lineage_summary = graph.lineage_summary()
        questions = [
            {
                "question": "What does this codebase do at a high level?",
                "answer": "The highest-ranked modules point to the main business purpose of the codebase, and the answer is grounded in their extracted purpose statements.",
                "cited_files": [module.path for module in top_modules],
                "citations": [citation.model_dump(mode="json") for module in top_modules for citation in self._module_citations(module, semantics)],
                "confidence": 0.55,
            },
            {
                "question": "What are the main data flows and where does data come from?",
                "answer": f"The lineage graph reports {lineage_summary.get('datasets_total', 0)} datasets and {lineage_summary.get('transformations_total', 0)} transformations.",
                "cited_files": [module.path for module in top_modules],
                "citations": [citation.model_dump(mode="json") for module in top_modules for citation in self._module_citations(module, semantics, evidence_type="lineage")],
                "confidence": 0.6,
            },
            {
                "question": "What are the critical modules that a new engineer should understand first?",
                "answer": "Start with the top hotspot modules because they balance PageRank, git velocity, lineage fan-out, and business logic score.",
                "cited_files": [module.path for module in top_modules],
                "citations": [citation.model_dump(mode="json") for module in top_modules for citation in self._module_citations(module, semantics, evidence_type="hotspot")],
                "confidence": 0.7,
            },
            {
                "question": "Where are the highest-risk areas and technical debt?",
                "answer": f"Documentation drift was detected in {sum(1 for item in semantics.drift_results if item.drift_level in ('possible_drift', 'likely_drift'))} modules.",
                "cited_files": [module.path for module in top_modules],
                "citations": [citation.model_dump(mode="json") for module in top_modules for citation in self._module_citations(module, semantics, evidence_type="drift")],
                "confidence": 0.65,
            },
            {
                "question": "What are the blind spots — areas where the analysis may be incomplete?",
                "answer": "Modules in the semantic review queue are the main blind spots because they need human follow-up.",
                "cited_files": [item["file_path"] for item in semantics.review_queue[:3]],
                "citations": [],
                "confidence": 0.6,
            },
        ]
        return self._normalize_day_one_answers({"questions": questions}, graph, semantics)

    def _synthesize_day_one(self, graph: KnowledgeGraph, semantics: SemanticsResult, router: Optional[ModelRouter], budget: ContextWindowBudget) -> Optional[dict[str, Any]]:
        if router is None:
            return self._heuristic_day_one(graph, semantics)
        summary = graph.summary()
        lineage_sum = graph.lineage_summary()
        top_module_purposes = "\n".join(
            f"- {item.file_path}: {item.purpose_statement}"
            for item in semantics.purpose_results[:15]
            if item.purpose_statement
        ) or "No purpose statements available."
        domains = "\n".join(
            f"- {domain.domain_name} ({len(domain.members)} modules): {domain.description}"
            for domain in (semantics.clustering.domains if semantics.clustering else [])
        ) or "No domains computed."
        prompt = DAY_ONE_SYNTHESIS_PROMPT.format(
            project_type=summary.get("project_type", "unknown"),
            total_modules=len(graph.all_modules()),
            total_datasets=lineage_sum.get("datasets_total", 0),
            total_transformations=lineage_sum.get("transformations_total", 0),
            hubs=", ".join(item[0] for item in summary.get("top_hubs", [])[:5]) or "none",
            cycles=summary.get("circular_dependency_clusters", 0),
            dead_code=summary.get("dead_code_candidates", 0),
            domains=domains,
            top_module_purposes=top_module_purposes,
            lineage_summary=json.dumps(lineage_sum, indent=1, default=str)[:2000],
            blind_spots_summary=f"{summary.get('parse_errors', 0)} parse errors, {summary.get('dead_code_candidates', 0)} dead-code candidates",
            high_risk_summary=f"{len(summary.get('top_hubs', []))} hubs, {summary.get('circular_dependency_clusters', 0)} circular deps",
        )
        resp, selection = router.generate(task=TaskType.ONBOARDING_SYNTHESIS, prompt=prompt, system=SYSTEM_SYNTHESIS, temperature=0.2, max_tokens=3000, format_json=True)
        budget.record(resp)
        parsed = resp.parse_json() if resp.success else None
        if isinstance(parsed, dict) and "questions" in parsed:
            return self._normalize_day_one_answers(parsed, graph, semantics)
        return self._heuristic_day_one(graph, semantics)

    def _compute_lineage_fanout(self, graph: KnowledgeGraph) -> dict[str, float]:
        fanout: dict[str, float] = {module.path: 0.0 for module in graph.all_modules()}
        for xform in graph.all_transformations():
            if not xform.source_file:
                continue
            downstream: set[str] = set()
            for dataset in xform.target_datasets:
                downstream.add(dataset)
                if graph._g.has_node(dataset):
                    downstream.update(graph._g.successors(dataset))
            fanout[xform.source_file] = fanout.get(xform.source_file, 0.0) + float(len(downstream))
        return fanout

    def _normalize_scores(self, values: dict[str, float]) -> dict[str, float]:
        if not values:
            return {}
        minimum = min(values.values())
        maximum = max(values.values())
        if maximum == minimum:
            return {key: 0.0 for key in values}
        return {key: round((value - minimum) / (maximum - minimum), 6) for key, value in values.items()}

    def _compute_hotspot_rankings(self, graph: KnowledgeGraph, semantics: SemanticsResult) -> list[dict[str, Any]]:
        purpose_lookup = self._purpose_lookup(semantics)
        pagerank_scores = graph.pagerank()
        pagerank = {module.path: pagerank_scores.get(module.path, 0.0) for module in graph.all_modules()}
        velocity = {module.path: float(module.change_velocity_30d) for module in graph.all_modules()}
        fanout = self._compute_lineage_fanout(graph)
        business_logic = {
            module.path: purpose_lookup.get(module.path).business_logic_score if module.path in purpose_lookup else module.business_logic_score
            for module in graph.all_modules()
        }

        norm_pagerank = self._normalize_scores(pagerank)
        norm_velocity = self._normalize_scores(velocity)
        norm_fanout = self._normalize_scores(fanout)
        norm_business_logic = self._normalize_scores(business_logic)

        rankings: list[dict[str, Any]] = []
        for module in graph.all_modules():
            score = round((
                norm_pagerank.get(module.path, 0.0)
                + norm_velocity.get(module.path, 0.0)
                + norm_fanout.get(module.path, 0.0)
                + norm_business_logic.get(module.path, 0.0)
            ) / 4.0, 6)
            module.hotspot_fusion_score = score
            rankings.append({
                "file_path": module.path,
                "hotspot_fusion_score": score,
                "purpose": purpose_lookup.get(module.path).purpose_statement if module.path in purpose_lookup else module.purpose_statement,
                "signal_breakdown": {
                    "pagerank": {"raw": round(pagerank.get(module.path, 0.0), 6), "normalized": norm_pagerank.get(module.path, 0.0)},
                    "git_velocity": {"raw": velocity.get(module.path, 0.0), "normalized": norm_velocity.get(module.path, 0.0)},
                    "lineage_fanout": {"raw": fanout.get(module.path, 0.0), "normalized": norm_fanout.get(module.path, 0.0)},
                    "business_logic_score": {"raw": business_logic.get(module.path, 0.0), "normalized": norm_business_logic.get(module.path, 0.0)},
                },
                "supporting_evidence": [
                    evidence.model_dump(mode="json")
                    for evidence in (purpose_lookup.get(module.path).evidence if module.path in purpose_lookup else module.semantic_evidence)[:3]
                ],
            })
        rankings.sort(key=lambda item: item["hotspot_fusion_score"], reverse=True)
        return rankings

    def _has_unresolved_lineage(self, graph: KnowledgeGraph, module: ModuleNode) -> bool:
        relevant_ids = [
            xform.id for xform in graph.all_transformations()
            if xform.source_file == module.path and (xform.is_dynamic or xform.confidence < 0.7)
        ]
        if relevant_ids:
            return True
        for src, tgt, data in graph._g.edges(data=True):
            if data.get("edge_type") not in ("PRODUCES", "CONSUMES"):
                continue
            if data.get("confidence", 1.0) < 0.7 and (src in relevant_ids or tgt in relevant_ids):
                return True
        return False

    def _weak_evidence(self, module: ModuleNode, semantics: SemanticsResult) -> bool:
        purpose = self._purpose_lookup(semantics).get(module.path)
        evidence = purpose.evidence if purpose else module.semantic_evidence
        if not evidence:
            return True
        return not any(item.line_start is not None and item.line_end is not None for item in evidence)

    def _build_review_queue(self, graph: KnowledgeGraph, semantics: SemanticsResult) -> list[dict[str, Any]]:
        purpose_lookup = self._purpose_lookup(semantics)
        drift_lookup = self._drift_lookup(semantics)
        hotspot_lookup = {item["file_path"]: item["hotspot_fusion_score"] for item in semantics.hotspot_rankings}
        queue: list[dict[str, Any]] = []
        for module in graph.all_modules():
            reasons: list[str] = []
            purpose = purpose_lookup.get(module.path)
            drift = drift_lookup.get(module.path)
            hotspot = hotspot_lookup.get(module.path, 0.0)
            if purpose and purpose.confidence < 0.6:
                reasons.append("low-confidence semantic output")
            if drift and drift.drift_level in ("possible_drift", "likely_drift"):
                reasons.append(f"documentation drift ({drift.drift_level})")
            if drift and drift.documentation_missing:
                reasons.append("missing documentation")
            if hotspot >= 0.7 and self._weak_evidence(module, semantics):
                reasons.append("high hotspot score but weak evidence")
            if self._has_unresolved_lineage(graph, module):
                reasons.append("unresolved lineage case")
            if reasons:
                queue.append({
                    "file_path": module.path,
                    "hotspot_fusion_score": round(hotspot, 6),
                    "semantic_confidence": purpose.confidence if purpose else module.semantic_confidence,
                    "doc_drift_level": drift.drift_level if drift else None,
                    "reasons": reasons,
                    "evidence": [citation.model_dump(mode="json") for citation in self._module_citations(module, semantics, limit=2)],
                })
        queue.sort(key=lambda item: (item["hotspot_fusion_score"], item["semantic_confidence"]), reverse=True)
        return queue

    def _enrich_graph(self, graph: KnowledgeGraph, result: SemanticsResult) -> None:
        purpose_lookup = self._purpose_lookup(result)
        domain_lookup: dict[str, str] = {}
        if result.clustering:
            for domain in result.clustering.domains:
                for member in domain.members:
                    domain_lookup[member] = domain.domain_name
        drift_lookup = self._drift_lookup(result)
        hotspot_lookup = {item["file_path"]: item["hotspot_fusion_score"] for item in result.hotspot_rankings}
        for module in graph.all_modules():
            purpose = purpose_lookup.get(module.path)
            if purpose:
                module.purpose_statement = purpose.purpose_statement
                module.semantic_confidence = purpose.confidence
                module.business_logic_score = purpose.business_logic_score
                module.semantic_evidence = purpose.evidence
                module.semantic_model_used = purpose.model_used
                module.semantic_prompt_version = purpose.prompt_version
                module.semantic_generation_timestamp = purpose.generation_timestamp
                module.semantic_fallback_used = purpose.is_fallback
            if module.path in domain_lookup:
                module.domain_cluster = domain_lookup[module.path]
            drift = drift_lookup.get(module.path)
            if drift:
                module.doc_drift_level = drift.drift_level
                module.doc_drift_detected = drift.drift_level in ("possible_drift", "likely_drift")
            module.hotspot_fusion_score = hotspot_lookup.get(module.path, 0.0)
            graph.add_module(module)

    def _compute_reading_order(self, graph: KnowledgeGraph, result: SemanticsResult) -> list[dict[str, Any]]:
        purpose_lookup = self._purpose_lookup(result)
        domain_lookup: dict[str, str] = {}
        domain_scores: dict[str, float] = {}
        if result.clustering:
            for domain in result.clustering.domains:
                scores = [purpose_lookup[path].business_logic_score for path in domain.members if path in purpose_lookup]
                domain_scores[domain.domain_name] = sum(scores) / len(scores) if scores else 0.0
                for member in domain.members:
                    domain_lookup[member] = domain.domain_name
        ordered_domains = sorted(domain_scores.keys(), key=lambda key: domain_scores[key], reverse=True)
        reading_order: list[dict[str, Any]] = []
        seen: set[str] = set()
        step = 0

        def append_module(module: ModuleNode, domain_name: str) -> None:
            nonlocal step
            seen.add(module.path)
            step += 1
            purpose = purpose_lookup.get(module.path)
            reading_order.append({
                "step": step,
                "file_path": module.path,
                "domain": domain_name,
                "purpose": purpose.purpose_statement if purpose else f"{module.role} module",
                "business_logic_score": round(purpose.business_logic_score if purpose else 0.0, 2),
                "hotspot_fusion_score": round(module.hotspot_fusion_score, 2),
                "reason": _reading_reason(module, purpose),
            })

        for domain_name in ordered_domains:
            domain_modules = [module for module in graph.all_modules() if domain_lookup.get(module.path) == domain_name and module.path not in seen]
            domain_modules.sort(
                key=lambda module: (
                    module.hotspot_fusion_score,
                    purpose_lookup[module.path].business_logic_score if module.path in purpose_lookup else 0.0,
                ),
                reverse=True,
            )
            for module in domain_modules:
                append_module(module, domain_name)

        for module in graph.all_modules():
            if module.path not in seen:
                append_module(module, domain_lookup.get(module.path, "Uncategorized"))

        return reading_order
