"""
Semanticist Agent — Phase 3 of the Brownfield Cartographer pipeline.

Adds semantic understanding on top of Phase 1 (structure) and Phase 2 (lineage).

Responsibilities:
  1. Purpose extraction — LLM-generated business-purpose statements per module
  2. Business logic concentration — identify files with concentrated business rules
  3. Domain clustering — group modules into logical domains
  4. Documentation drift detection — flag docs that diverge from implementation
  5. Day-One synthesis — produce structured onboarding evidence for the Navigator

Ollama model routing:
  - qwen3-coder:480b-cloud → code-focused extraction, file-level summarization
  - deepseek-v3.1:671b-cloud → synthesis, clustering, high-level summaries

Graceful degradation: every step works without an LLM (heuristic fallbacks).
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
from src.llm.ollama_client import ContextWindowBudget, OllamaClient, OllamaResponse
from src.llm.prompt_builder import DAY_ONE_SYNTHESIS_PROMPT, SYSTEM_SYNTHESIS
from src.models.nodes import AnalysisMethod, TraceEntry

logger = logging.getLogger(__name__)


@dataclass
class SemanticsResult:
    """Output of a Semanticist run."""

    purpose_results: list[PurposeResult] = field(default_factory=list)
    clustering: Optional[ClusteringResult] = None
    drift_results: list[DriftResult] = field(default_factory=list)
    day_one_answers: Optional[dict[str, Any]] = None
    budget_summary: dict[str, Any] = field(default_factory=dict)
    trace: list[TraceEntry] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    ollama_available: bool = False


class Semanticist:
    """
    Phase 3 agent: LLM-powered semantic analysis.

    Usage::

        semanticist = Semanticist()
        result = semanticist.run(graph, repo_root)
    """

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

    def run(
        self,
        graph: KnowledgeGraph,
        repo_root: Path,
    ) -> SemanticsResult:
        """Execute full Phase 3 semantic analysis pipeline.

        Steps:
          1. Initialize Ollama client + model router
          2. Extract purpose statements for all eligible modules
          3. Cluster modules into business domains
          4. Detect documentation drift
          5. Synthesize Day-One onboarding answers
          6. Attach semantic metadata back to the graph

        Returns SemanticsResult (always — never raises).
        """
        t0 = time.monotonic()
        result = SemanticsResult()
        trace: list[TraceEntry] = []
        budget = ContextWindowBudget()

        # ---- Initialize LLM client ----------------------------------
        client = OllamaClient(
            base_url=self._ollama_url,
            timeout=self._timeout,
        )
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

        if not result.ollama_available:
            logger.warning(
                "Ollama not available — Phase 3 will use heuristic fallbacks only"
            )

        # ---- Step 1: Purpose extraction ------------------------------
        logger.info("=== Semanticist Step 1: Purpose Extraction ===")
        if result.ollama_available:
            result.purpose_results = extract_all_purposes(
                graph, router, budget, max_modules=self._max_modules,
            )
            success_count = sum(1 for r in result.purpose_results if r.purpose_statement)
            trace.append(TraceEntry(
                agent="semanticist",
                action="purpose_extraction",
                target=f"{len(result.purpose_results)} modules",
                result=f"{success_count} purpose statements generated",
                confidence=0.8,
                analysis_method=AnalysisMethod.LLM_INFERENCE,
            ))
            logger.info(
                "Purpose extraction: %d/%d modules got statements",
                success_count, len(result.purpose_results),
            )
        else:
            logger.info("Skipping purpose extraction (no LLM)")

        # ---- Step 2: Domain clustering -------------------------------
        logger.info("=== Semanticist Step 2: Domain Clustering ===")
        result.clustering = cluster_into_domains(
            graph,
            result.purpose_results,
            router=router if result.ollama_available else None,
            budget=budget if result.ollama_available else None,
        )
        trace.append(TraceEntry(
            agent="semanticist",
            action="domain_clustering",
            target=f"{len(result.clustering.domains)} domains",
            result=f"method={result.clustering.method}, confidence={result.clustering.confidence:.2f}",
            confidence=result.clustering.confidence,
            analysis_method=AnalysisMethod.LLM_INFERENCE
            if result.clustering.method == "llm_refined"
            else AnalysisMethod.STATIC_ANALYSIS,
        ))
        logger.info(
            "Domain clustering: %d domains (%s)",
            len(result.clustering.domains), result.clustering.method,
        )

        # ---- Step 3: Documentation drift detection -------------------
        logger.info("=== Semanticist Step 3: Documentation Drift Detection ===")
        if result.ollama_available and result.purpose_results:
            result.drift_results = detect_all_drift(
                graph, result.purpose_results, router, budget,
                max_modules=min(self._max_modules, 50),
            )
            drift_count = sum(
                1 for d in result.drift_results
                if d.drift_level in ("possible_drift", "likely_drift")
            )
            trace.append(TraceEntry(
                agent="semanticist",
                action="doc_drift_detection",
                target=f"{len(result.drift_results)} modules checked",
                result=f"{drift_count} modules with drift detected",
                confidence=0.7,
                analysis_method=AnalysisMethod.LLM_INFERENCE,
            ))
            logger.info(
                "Doc drift detection: %d/%d modules have drift",
                drift_count, len(result.drift_results),
            )
        else:
            logger.info("Skipping drift detection (no LLM or no purpose statements)")

        # ---- Step 4: Day-One synthesis --------------------------------
        logger.info("=== Semanticist Step 4: Day-One Synthesis ===")
        if result.ollama_available:
            result.day_one_answers = self._synthesize_day_one(
                graph, result, router, budget,
            )
            trace.append(TraceEntry(
                agent="semanticist",
                action="day_one_synthesis",
                target="onboarding_questions",
                result="generated" if result.day_one_answers else "failed",
                confidence=0.7 if result.day_one_answers else 0.0,
                analysis_method=AnalysisMethod.LLM_INFERENCE,
            ))
        else:
            logger.info("Skipping day-one synthesis (no LLM)")

        # ---- Step 5: Attach semantic metadata to graph ---------------
        logger.info("=== Semanticist Step 5: Enriching Graph ===")
        self._enrich_graph(graph, result)

        # ---- Finalize ------------------------------------------------
        elapsed = time.monotonic() - t0
        result.budget_summary = budget.summary()
        result.trace = trace
        result.stats = {
            "ollama_available": result.ollama_available,
            "purpose_statements_generated": sum(
                1 for r in result.purpose_results if r.purpose_statement
            ),
            "purpose_statements_attempted": len(result.purpose_results),
            "domains_found": len(result.clustering.domains) if result.clustering else 0,
            "clustering_method": result.clustering.method if result.clustering else "none",
            "drift_checks_performed": len(result.drift_results),
            "drift_detected": sum(
                1 for d in result.drift_results
                if d.drift_level in ("possible_drift", "likely_drift")
            ),
            "day_one_answers_generated": bool(result.day_one_answers),
            "llm_budget": result.budget_summary,
            "elapsed_seconds": round(elapsed, 2),
        }

        logger.info(
            "Phase 3 complete in %.1fs — %d purposes, %d domains, %d drift flags",
            elapsed,
            result.stats["purpose_statements_generated"],
            result.stats["domains_found"],
            result.stats["drift_detected"],
        )
        return result

    # ------------------------------------------------------------------
    # Day-One synthesis
    # ------------------------------------------------------------------

    def _synthesize_day_one(
        self,
        graph: KnowledgeGraph,
        semantics: SemanticsResult,
        router: ModelRouter,
        budget: ContextWindowBudget,
    ) -> Optional[dict[str, Any]]:
        """Generate answers to the Five FDE Day-One Questions."""
        summary = graph.summary()
        lineage_sum = graph.lineage_summary()

        # Top module purposes
        top_purposes = []
        for pr in semantics.purpose_results[:15]:
            if pr.purpose_statement:
                top_purposes.append(f"- {pr.file_path}: {pr.purpose_statement}")
        top_module_purposes = "\n".join(top_purposes) if top_purposes else "No purpose statements available."

        # Domain summary
        domains_str = "No domains computed."
        if semantics.clustering and semantics.clustering.domains:
            domain_parts = []
            for d in semantics.clustering.domains:
                domain_parts.append(
                    f"- {d.domain_name} ({len(d.members)} modules): {d.description}"
                )
            domains_str = "\n".join(domain_parts)

        # Hubs and cycles
        hubs = summary.get("top_hubs", [])
        hubs_str = ", ".join(h[0] for h in hubs[:5]) if hubs else "none"
        cycles = summary.get("circular_dependency_clusters", 0)
        dead_code = summary.get("dead_code_candidates", 0)

        prompt = DAY_ONE_SYNTHESIS_PROMPT.format(
            project_type=summary.get("project_type", "unknown"),
            total_modules=summary.get("total_nodes", 0),
            total_datasets=lineage_sum.get("datasets_total", 0),
            total_transformations=lineage_sum.get("transformations_total", 0),
            hubs=hubs_str,
            cycles=cycles,
            dead_code=dead_code,
            domains=domains_str,
            top_module_purposes=top_module_purposes,
            lineage_summary=json.dumps(lineage_sum, indent=1, default=str)[:2000],
            blind_spots_summary=f"{summary.get('parse_errors', 0)} parse errors, "
            f"{dead_code} dead-code candidates",
            high_risk_summary=f"{len(hubs)} hubs, {cycles} circular deps",
        )

        resp, selection = router.generate(
            task=TaskType.ONBOARDING_SYNTHESIS,
            prompt=prompt,
            system=SYSTEM_SYNTHESIS,
            temperature=0.2,
            max_tokens=3000,
            format_json=True,
        )
        budget.record(resp)

        if not resp.success:
            logger.warning("Day-One synthesis failed: %s", resp.error)
            return None

        parsed = resp.parse_json()
        if parsed and "questions" in parsed:
            return parsed
        logger.warning("Day-One synthesis: unparseable response")
        return None

    # ------------------------------------------------------------------
    # Graph enrichment
    # ------------------------------------------------------------------

    def _enrich_graph(
        self,
        graph: KnowledgeGraph,
        result: SemanticsResult,
    ) -> None:
        """Attach semantic metadata to graph nodes."""
        purpose_lookup = {
            pr.file_path: pr for pr in result.purpose_results if pr.purpose_statement
        }

        # Build domain lookup
        domain_lookup: dict[str, str] = {}
        if result.clustering:
            for domain in result.clustering.domains:
                for member in domain.members:
                    domain_lookup[member] = domain.domain_name

        # Build drift lookup
        drift_lookup: dict[str, DriftResult] = {
            d.file_path: d for d in result.drift_results
        }

        enriched = 0
        for module in graph.all_modules():
            changed = False

            pr = purpose_lookup.get(module.path)
            if pr:
                module.purpose_statement = pr.purpose_statement
                module.semantic_confidence = pr.confidence
                module.business_logic_score = pr.business_logic_score
                module.semantic_evidence = pr.evidence
                changed = True

            domain = domain_lookup.get(module.path)
            if domain:
                module.domain_cluster = domain
                changed = True

            drift = drift_lookup.get(module.path)
            if drift and drift.drift_level in ("possible_drift", "likely_drift"):
                module.doc_drift_detected = True
                module.doc_drift_level = drift.drift_level
                changed = True

            if changed:
                graph.add_module(module)
                enriched += 1

        logger.info("Enriched %d modules with semantic metadata", enriched)
