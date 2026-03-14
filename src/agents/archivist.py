"""
Archivist agent - Phase 4 artifact retrieval and living-context generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import networkx as nx

from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import DayOneCitation, SemanticEvidence

logger = logging.getLogger(__name__)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not load JSON from %s: %s", path, exc)
        return default


def _slugify(text: str, limit: int = 48) -> str:
    return (re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip().lower()).strip("-") or "query")[:limit]


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/")


def _tokenize(text: str) -> list[str]:
    stopwords = {
        "the", "this", "that", "what", "where", "which", "does", "do", "are", "and",
        "with", "from", "into", "most", "main", "data", "repo", "repository", "codebase",
        "module", "dataset", "changes", "change", "breaks", "if", "about", "contain",
        "contains", "logic", "business", "explain",
    }
    return [
        token for token in re.findall(r"[a-zA-Z0-9_./-]+", text.lower())
        if len(token) > 2 and token not in stopwords
    ]


@dataclass
class RetrievedContext:
    query_type: str
    summary: str
    citations: list[DayOneCitation] = field(default_factory=list)
    confidence: float = 0.0
    facts: dict[str, Any] = field(default_factory=dict)
    entity: Optional[str] = None


@dataclass
class ArchivistContext:
    artifact_root: Path
    module_graph: KnowledgeGraph
    lineage_graph: KnowledgeGraph
    semantic_enrichment: dict[str, Any] = field(default_factory=dict)
    semantic_index: dict[str, Any] = field(default_factory=dict)
    day_one_answers: dict[str, Any] = field(default_factory=dict)
    reading_order: list[dict[str, Any]] = field(default_factory=list)
    semantic_review_queue: list[dict[str, Any]] = field(default_factory=list)
    semantic_hotspots: list[dict[str, Any]] = field(default_factory=list)
    blind_spots: dict[str, Any] = field(default_factory=dict)
    high_risk_areas: dict[str, Any] = field(default_factory=dict)
    surveyor_stats: dict[str, Any] = field(default_factory=dict)
    hydrologist_stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArchivistResult:
    codebase_path: Path
    onboarding_brief_path: Path
    stats: dict[str, Any] = field(default_factory=dict)


class Archivist:
    """Loads saved artifacts, retrieves evidence, and writes living context files."""

    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = self.discover_artifact_root(artifact_root)
        self._context: Optional[ArchivistContext] = None

    @staticmethod
    def discover_artifact_root(path: Path) -> Path:
        if path.is_file():
            raise ValueError(f"Expected a cartography directory, got file: {path}")
        if (path / "module_graph" / "module_graph.json").exists():
            return path
        candidates = [
            child for child in path.iterdir()
            if child.is_dir() and (child / "module_graph" / "module_graph.json").exists()
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError(
                f"Multiple artifact roots found under {path}. Please pass one repo directory directly."
            )
        raise ValueError(f"Could not find a cartography artifact root under {path}")

    @property
    def context(self) -> ArchivistContext:
        if self._context is None:
            semantics_dir = self.artifact_root / "semantics"
            self._context = ArchivistContext(
                artifact_root=self.artifact_root,
                module_graph=KnowledgeGraph.load(self.artifact_root / "module_graph" / "module_graph.json"),
                lineage_graph=KnowledgeGraph.load_lineage_artifact(
                    self.artifact_root / "data_lineage" / "lineage_graph.json"
                ),
                semantic_enrichment=_load_json(semantics_dir / "semantic_enrichment.json", {}),
                semantic_index=_load_json(semantics_dir / "semantic_index.json", {}),
                day_one_answers=_load_json(semantics_dir / "day_one_answers.json", {}),
                reading_order=_load_json(semantics_dir / "reading_order.json", {}).get("reading_order", []),
                semantic_review_queue=_load_json(
                    semantics_dir / "semantic_review_queue.json", {}
                ).get("semantic_review_queue", []),
                semantic_hotspots=_load_json(
                    self.artifact_root / "semantic_hotspots.json", {}
                ).get("semantic_hotspots", []),
                blind_spots=_load_json(self.artifact_root / "blind_spots.json", {}),
                high_risk_areas=_load_json(self.artifact_root / "high_risk_areas.json", {}),
                surveyor_stats=_load_json(
                    self.artifact_root / "module_graph" / "surveyor_stats.json", {}
                ),
                hydrologist_stats=_load_json(
                    self.artifact_root / "data_lineage" / "hydrologist_stats.json", {}
                ),
            )
        return self._context

    @property
    def queries_dir(self) -> Path:
        return self.artifact_root / "queries"

    @property
    def codebase_md_path(self) -> Path:
        return self.artifact_root / "CODEBASE.md"

    @property
    def onboarding_brief_path(self) -> Path:
        return self.artifact_root / "onboarding_brief.md"

    def run(self) -> ArchivistResult:
        self.generate_codebase_md()
        self.generate_onboarding_brief_md()
        return ArchivistResult(
            codebase_path=self.codebase_md_path,
            onboarding_brief_path=self.onboarding_brief_path,
            stats={
                "modules_indexed": len(self.context.module_graph.all_modules()),
                "datasets_indexed": len(self.context.lineage_graph.all_datasets()),
                "semantic_hotspots": len(self.context.semantic_hotspots),
                "review_queue_items": len(self.context.semantic_review_queue),
            },
        )

    def generate_codebase_md(self) -> Path:
        ctx = self.context
        overview = self.repository_overview_context()
        critical_path = ctx.semantic_hotspots[:5] or [
            {"file_path": node, "hotspot_fusion_score": score}
            for node, score in ctx.surveyor_stats.get("top_hubs", [])[:5]
        ]
        sources = ctx.lineage_graph.find_sources()[:5]
        sinks = ctx.lineage_graph.find_sinks()[:5]
        high_velocity = ctx.surveyor_stats.get("high_velocity_files", [])[:5]
        lines = [
            "# CODEBASE",
            "",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            "## Architecture Overview",
            (
                f"This {ctx.surveyor_stats.get('project_type', 'unknown')} repository maps to "
                f"{len(ctx.module_graph.all_modules())} modules, {len(ctx.lineage_graph.all_datasets())} datasets, "
                f"and {len(ctx.lineage_graph.all_transformations())} transformations. {overview.summary}"
            ).strip(),
            "",
            "## Critical Path",
        ]
        for item in critical_path:
            module_path = item.get("file_path") or item.get("node") or "unknown"
            module = ctx.module_graph.get_module(module_path)
            purpose = module.purpose_statement if module else ""
            score = item.get("hotspot_fusion_score") or item.get("pagerank_score") or 0.0
            line = f"- `{module_path}` ({score:.2f})"
            if purpose:
                line += f": {purpose}"
            lines.append(line)
        lines.extend([
            "",
            "## Data Sources And Sinks",
            f"Sources: {', '.join(f'`{item}`' for item in sources) if sources else 'None detected.'}",
            f"Sinks: {', '.join(f'`{item}`' for item in sinks) if sinks else 'None detected.'}",
            "",
            "## Known Debt",
            (
                f"Circular dependency clusters: {ctx.surveyor_stats.get('circular_dependency_clusters', 0)}. "
                f"Blind spots: {ctx.blind_spots.get('summary', {}).get('total_blind_spots', 0)}. "
                f"Semantic review queue items: {len(ctx.semantic_review_queue)}."
            ),
            "",
            "## High-Velocity Files",
        ])
        if high_velocity:
            lines.extend(
                f"- `{file_path}` ({count} commits in the configured window)"
                for file_path, count in high_velocity
            )
        else:
            lines.append("- No high-velocity files were detected in the configured git window.")
        self.codebase_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Wrote CODEBASE.md -> %s", self.codebase_md_path)
        return self.codebase_md_path

    def generate_onboarding_brief_md(self) -> Path:
        lines = [
            "# Onboarding Brief",
            "",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
        ]
        for item in self.context.day_one_answers.get("questions", []):
            lines.append(f"## {item.get('question', 'Question')}")
            lines.append(str(item.get("answer", "")))
            lines.append("")
            citations = self._citations_from_payload(item.get("citations", []))
            if citations:
                lines.append("Supporting citations:")
                for citation in citations[:5]:
                    span = self._format_line_span(citation.line_start, citation.line_end)
                    lines.append(
                        f"- `{citation.file_path}`{span} [{citation.source_phase}/{citation.evidence_type}] "
                        f"{citation.description}"
                    )
                lines.append("")
        self.onboarding_brief_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Wrote onboarding_brief.md -> %s", self.onboarding_brief_path)
        return self.onboarding_brief_path

    def write_query_log(
        self,
        question: str,
        answer_payload: dict[str, Any],
        query_type: str,
        models_used: Optional[dict[str, str]] = None,
    ) -> Path:
        self.queries_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        log_path = self.queries_dir / f"{stamp}-{_slugify(question)}.json"
        log_path.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "artifact_root": str(self.artifact_root),
                    "query_type": query_type,
                    "models_used": models_used or {},
                    "answer": answer_payload,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        logger.info("Wrote query log -> %s", log_path)
        return log_path

    def repository_overview_context(self) -> RetrievedContext:
        answer = self._match_day_one_answer("high level")
        if answer is None:
            hotspots = self.context.semantic_hotspots[:3]
            module_paths = [item.get("file_path", "") for item in hotspots if item.get("file_path")]
            citations = [citation for path in module_paths for citation in self.module_citations(path, limit=2)]
            return RetrievedContext(
                query_type="repository_overview",
                summary=(
                    "The repository overview is grounded primarily in the top semantic hotspots: "
                    + ", ".join(f"`{path}`" for path in module_paths)
                ),
                citations=self._dedupe_citations(citations, limit=6),
                confidence=0.55 if citations else 0.3,
            )
        return RetrievedContext(
            query_type="repository_overview",
            summary=str(answer.get("answer", "")),
            citations=self._citations_from_payload(answer.get("citations", [])),
            confidence=float(answer.get("confidence", 0.8)),
            facts={
                "project_type": self.context.surveyor_stats.get("project_type", "unknown"),
                "modules_total": len(self.context.module_graph.all_modules()),
                "datasets_total": len(self.context.lineage_graph.all_datasets()),
                "transformations_total": len(self.context.lineage_graph.all_transformations()),
            },
        )

    def main_pipelines_context(self) -> RetrievedContext:
        answer = self._match_day_one_answer("data flows")
        if answer is not None:
            return RetrievedContext(
                query_type="main_pipelines",
                summary=str(answer.get("answer", "")),
                citations=self._citations_from_payload(answer.get("citations", [])),
                confidence=float(answer.get("confidence", 0.8)),
                facts={
                    "sources": self.context.lineage_graph.find_sources(),
                    "sinks": self.context.lineage_graph.find_sinks(),
                },
            )
        sink_datasets = [dataset for dataset in self.context.lineage_graph.find_sinks()[:3]]
        citations: list[DayOneCitation] = []
        summaries: list[str] = []
        for dataset_name in sink_datasets:
            trace = self.trace_lineage_context(dataset_name, direction="upstream")
            citations.extend(trace.citations[:2])
            summaries.append(trace.summary)
        return RetrievedContext(
            query_type="main_pipelines",
            summary=" ".join(summaries) if summaries else "No sink datasets were identified.",
            citations=self._dedupe_citations(citations, limit=8),
            confidence=0.65 if citations else 0.4,
        )

    def business_logic_context(self) -> RetrievedContext:
        hotspots = self.context.semantic_hotspots[:5]
        citations: list[DayOneCitation] = []
        for item in hotspots:
            citations.extend(self._citations_from_payload(item.get("supporting_evidence", []), evidence_type="hotspot"))
        top_labels = [
            f"`{item.get('file_path', 'unknown')}` ({item.get('hotspot_fusion_score', 0.0):.2f})"
            for item in hotspots[:3]
        ]
        return RetrievedContext(
            query_type="business_logic_hotspots",
            summary=(
                "The strongest business logic is concentrated in " + ", ".join(top_labels) + "."
                if top_labels else "No semantic hotspots were available in the saved artifacts."
            ),
            citations=self._dedupe_citations(citations, limit=8),
            confidence=0.85 if hotspots else 0.2,
            facts={"hotspots": hotspots[:5]},
        )

    def trace_lineage_context(self, dataset_name: str, direction: str = "both") -> RetrievedContext:
        resolved = self.resolve_dataset_name(dataset_name) or dataset_name
        known = {dataset.name for dataset in self.context.lineage_graph.all_datasets()}
        if resolved not in known:
            return RetrievedContext(
                query_type="trace_lineage",
                entity=dataset_name,
                summary=f"Dataset `{dataset_name}` was not found in the saved lineage graph.",
                confidence=0.0,
            )
        graph = self._lineage_digraph()
        upstream = sorted(nx.ancestors(graph, resolved)) if resolved in graph else []
        downstream = sorted(nx.descendants(graph, resolved)) if resolved in graph else []
        producers = self._producer_transformations(resolved)
        consumers = self._consumer_transformations(resolved)
        upstream_sources = [node for node in upstream if node in self.context.lineage_graph.find_sources()]
        citations = [self.transformation_citation(item, resolved) for item in producers + consumers]
        if direction in ("downstream", "both"):
            downstream_transformations = [
                item for item in self.context.lineage_graph.all_transformations()
                if item.id in downstream
            ]
            citations.extend(
                self.transformation_citation(item, resolved) for item in downstream_transformations
            )
        if direction == "upstream":
            summary = (
                f"`{resolved}` is produced through {len(producers)} transformations and traces back to "
                f"{', '.join(upstream_sources[:5]) or 'no upstream source datasets'}."
            )
        elif direction == "downstream":
            summary = (
                f"If `{resolved}` changes, downstream lineage dependents include "
                f"{', '.join(downstream[:6]) or 'none'}."
            )
        else:
            summary = (
                f"`{resolved}` has {len(upstream_sources)} upstream source datasets and "
                f"{len(downstream)} downstream lineage dependents."
            )
        return RetrievedContext(
            query_type="trace_lineage",
            entity=resolved,
            summary=summary,
            citations=self._dedupe_citations(citations, limit=8),
            confidence=0.9 if citations else 0.65,
            facts={
                "dataset": resolved,
                "upstream_nodes": upstream,
                "downstream_nodes": downstream,
                "upstream_sources": upstream_sources,
                "producers": [item.id for item in producers],
                "consumers": [item.id for item in consumers],
            },
        )

    def blast_radius_context(self, subject: str) -> RetrievedContext:
        dataset_name = self.resolve_dataset_name(subject)
        if dataset_name:
            trace = self.trace_lineage_context(dataset_name, direction="downstream")
            trace.query_type = "blast_radius"
            return trace
        module_path = self.resolve_module_path(subject)
        if module_path is None:
            return RetrievedContext(
                query_type="blast_radius",
                entity=subject,
                summary=f"Could not resolve `{subject}` to a known dataset or module path.",
                confidence=0.0,
            )
        import_dependents = self._module_dependents(module_path)
        produced = [
            dataset
            for transformation in self.context.lineage_graph.all_transformations()
            if transformation.source_file == module_path
            for dataset in transformation.target_datasets
        ]
        downstream = sorted({
            node for dataset in produced for node in self.context.lineage_graph.blast_radius(dataset)
        })
        citations = self.module_citations(module_path, limit=3)
        for transformation in self.context.lineage_graph.all_transformations():
            if transformation.source_file == module_path:
                citations.append(self.transformation_citation(transformation))
        return RetrievedContext(
            query_type="blast_radius",
            entity=module_path,
            summary=(
                f"If `{module_path}` changes, likely impact includes import dependents "
                f"{', '.join(import_dependents[:5]) or 'none'} and downstream lineage nodes "
                f"{', '.join(downstream[:5]) or 'none'}."
            ),
            citations=self._dedupe_citations(citations, limit=8),
            confidence=0.82 if citations else 0.55,
            facts={
                "module_path": module_path,
                "import_dependents": import_dependents,
                "produced_datasets": produced,
                "downstream_lineage": downstream,
            },
        )

    def find_implementation_context(self, concept: str) -> RetrievedContext:
        matches = self.find_implementation(concept)
        citations = [
            citation for path, _ in matches[:4] for citation in self.module_citations(path, limit=2)
        ]
        return RetrievedContext(
            query_type="find_implementation",
            entity=concept,
            summary=(
                "The strongest implementation matches are "
                + ", ".join(f"`{path}` ({score:.2f})" for path, score in matches[:5])
                if matches else
                f"No strong implementation match was found for `{concept}`."
            ),
            citations=self._dedupe_citations(citations, limit=8),
            confidence=0.8 if matches else 0.2,
            facts={"concept": concept, "matches": [{"file_path": path, "score": score} for path, score in matches[:10]]},
        )

    def explain_module_context(self, module_path: str) -> RetrievedContext:
        resolved = self.resolve_module_path(module_path)
        if resolved is None:
            return RetrievedContext(
                query_type="explain_module",
                entity=module_path,
                summary=f"Module `{module_path}` was not found in the saved module graph.",
                confidence=0.0,
            )
        module = self.context.module_graph.get_module(resolved)
        if module is None:
            return RetrievedContext(
                query_type="explain_module",
                entity=resolved,
                summary=f"Module `{resolved}` was not found in the saved module graph.",
                confidence=0.0,
            )
        purpose_entry = self._purpose_artifact_entry(resolved)
        semantic_index_entry = self.context.semantic_index.get("modules", {}).get(resolved, {})
        purpose_text = (
            module.purpose_statement
            or module.semantic_summary
            or purpose_entry.get("purpose_statement", "")
            or semantic_index_entry.get("purpose", "")
            or "No semantic summary was available."
        )
        business_logic_score = (
            module.business_logic_score
            or float(purpose_entry.get("business_logic_score", 0.0))
            or float(semantic_index_entry.get("business_logic_score", 0.0))
        )
        semantic_confidence = (
            module.semantic_confidence
            or float(purpose_entry.get("confidence", 0.0))
            or float(semantic_index_entry.get("confidence", 0.0))
        )
        return RetrievedContext(
            query_type="explain_module",
            entity=resolved,
            summary=(
                f"`{resolved}` is a {module.role} module. "
                f"{purpose_text} "
                f"It contains {len(module.functions)} functions, {len(module.classes)} classes, "
                f"and a business-logic score of {business_logic_score:.2f}."
            ),
            citations=self.module_citations(resolved, limit=6),
            confidence=max(semantic_confidence, 0.5) if self.module_citations(resolved, limit=1) else 0.4,
            facts={
                "module_path": resolved,
                "role": module.role,
                "business_logic_score": business_logic_score,
                "imports": [imp.module for imp in module.imports[:8]],
            },
        )

    def resolve_module_path(self, text: str) -> Optional[str]:
        normalized = _normalize_path(text.lower())
        module_paths = [module.path for module in self.context.module_graph.all_modules()]
        exact = [path for path in module_paths if path.lower() == normalized]
        if exact:
            return exact[0]
        contained = [path for path in module_paths if path.lower() in normalized]
        if contained:
            return min(contained, key=len)
        tokens = _tokenize(text)
        matches = [
            path for path in module_paths
            if any(Path(path).name.lower() == token or Path(path).stem.lower() == token for token in tokens)
        ]
        return matches[0] if len(matches) == 1 else None

    def resolve_dataset_name(self, text: str) -> Optional[str]:
        normalized = text.lower()
        dataset_names = [dataset.name for dataset in self.context.lineage_graph.all_datasets()]
        exact = [name for name in dataset_names if name.lower() == normalized]
        if exact:
            return exact[0]
        contained = [name for name in dataset_names if name.lower() in normalized]
        if contained:
            return max(contained, key=len)
        tokens = _tokenize(text)
        matches = [name for name in dataset_names if any(name.lower().endswith(token) for token in tokens)]
        return matches[0] if len(matches) == 1 else None

    def find_implementation(self, concept: str) -> list[tuple[str, float]]:
        tokens = _tokenize(concept)
        if not tokens:
            return []
        modules_meta = self.context.semantic_index.get("modules", {})
        matches: list[tuple[str, float]] = []
        for module in self.context.module_graph.all_modules():
            metadata = modules_meta.get(module.path, {})
            haystack = " ".join(
                [
                    module.path,
                    metadata.get("purpose", ""),
                    module.purpose_statement or "",
                    " ".join(metadata.get("key_concepts", [])),
                    " ".join(e.description for e in module.semantic_evidence[:8]),
                    " ".join(fn.name for fn in module.functions[:8]),
                    " ".join(cls.name for cls in module.classes[:8]),
                ]
            ).lower()
            hits = sum(1 for token in tokens if token in haystack)
            if hits == 0:
                continue
            score = hits / len(tokens)
            if any(token in Path(module.path).stem.lower() for token in tokens):
                score += 0.25
            score += min(module.business_logic_score, 1.0) * 0.25
            matches.append((module.path, round(score, 4)))
        matches.sort(key=lambda item: item[1], reverse=True)
        return matches

    def module_citations(self, module_path: str, evidence_type: str = "semantic", limit: int = 3) -> list[DayOneCitation]:
        module = self.context.module_graph.get_module(module_path)
        if module is None:
            return []
        evidence = list(module.semantic_evidence)
        if not evidence:
            purpose_entry = self._purpose_artifact_entry(module_path)
            raw_evidence = purpose_entry.get("evidence", []) if purpose_entry else []
            for item in raw_evidence:
                try:
                    evidence.append(SemanticEvidence.model_validate(item))
                except Exception:
                    continue
        if not evidence:
            evidence.extend(
                SemanticEvidence(
                    source_phase="phase1",
                    file_path=module.path,
                    line_start=function.line or None,
                    line_end=function.end_line or function.line or None,
                    extraction_method="phase1_symbol",
                    description=f"Function definition {function.name}",
                )
                for function in module.functions[:3]
            )
        citations = [
            DayOneCitation(
                file_path=_normalize_path(item.file_path or module.path),
                line_start=item.line_start,
                line_end=item.line_end,
                evidence_type=evidence_type,
                source_phase=item.source_phase,
                extraction_method=item.extraction_method,
                description=item.description,
            )
            for item in evidence
        ]
        return self._dedupe_citations(citations, limit=limit)

    def _purpose_artifact_entry(self, module_path: str) -> dict[str, Any]:
        for item in self.context.semantic_enrichment.get("purpose_statements", []):
            if isinstance(item, dict) and item.get("file_path") == module_path:
                return item
        return {}

    def transformation_citation(self, transformation: Any, dataset_name: Optional[str] = None) -> DayOneCitation:
        description = (
            f"{transformation.transformation_type} in {transformation.source_file} reads "
            f"{', '.join(transformation.source_datasets[:3]) or 'none'} and writes "
            f"{', '.join(transformation.target_datasets[:3]) or 'none'}"
        )
        if dataset_name:
            description += f" for dataset `{dataset_name}`"
        return DayOneCitation(
            file_path=_normalize_path(transformation.source_file),
            line_start=transformation.line_range[0] or None,
            line_end=transformation.line_range[1] or None,
            evidence_type="lineage",
            source_phase="phase2",
            extraction_method="phase2_lineage",
            description=description,
        )

    def _match_day_one_answer(self, phrase: str) -> Optional[dict[str, Any]]:
        for item in self.context.day_one_answers.get("questions", []):
            if phrase in str(item.get("question", "")).lower():
                return item
        return None

    def _lineage_digraph(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        for source, target, data in self.context.lineage_graph._g.edges(data=True):
            if data.get("edge_type") in ("PRODUCES", "CONSUMES"):
                graph.add_edge(source, target)
        return graph

    def _module_dependents(self, module_path: str) -> list[str]:
        graph = nx.DiGraph()
        for source, target, data in self.context.module_graph._g.edges(data=True):
            if data.get("edge_type") in ("IMPORTS", "DBT_REF"):
                graph.add_edge(source, target)
        return sorted(nx.ancestors(graph, module_path)) if module_path in graph else []

    def _producer_transformations(self, dataset_name: str) -> list[Any]:
        return [
            item for item in self.context.lineage_graph.all_transformations()
            if dataset_name in item.target_datasets
        ]

    def _consumer_transformations(self, dataset_name: str) -> list[Any]:
        return [
            item for item in self.context.lineage_graph.all_transformations()
            if dataset_name in item.source_datasets
        ]

    def _citations_from_payload(self, items: list[dict[str, Any]], evidence_type: Optional[str] = None) -> list[DayOneCitation]:
        citations = []
        for item in items:
            if not isinstance(item, dict):
                continue
            citations.append(
                DayOneCitation(
                    file_path=_normalize_path(str(item.get("file_path", ""))),
                    line_start=item.get("line_start"),
                    line_end=item.get("line_end"),
                    evidence_type=str(item.get("evidence_type", evidence_type or "semantic")),
                    source_phase=str(item.get("source_phase", "phase3")),
                    extraction_method=str(item.get("extraction_method", "artifact")),
                    description=str(item.get("description", "")),
                )
            )
        return self._dedupe_citations(citations, limit=8)

    def _dedupe_citations(self, citations: list[DayOneCitation], limit: int = 8) -> list[DayOneCitation]:
        deduped: list[DayOneCitation] = []
        seen: set[tuple[str, Optional[int], Optional[int], str, str]] = set()
        for citation in citations:
            if not citation.file_path:
                continue
            citation.file_path = _normalize_path(citation.file_path)
            key = (
                citation.file_path,
                citation.line_start,
                citation.line_end,
                citation.evidence_type,
                citation.description,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(citation)
        return deduped[:limit]

    def _format_line_span(self, line_start: Optional[int], line_end: Optional[int]) -> str:
        if line_start is None and line_end is None:
            return ""
        if line_start == line_end:
            return f":{line_start}"
        return f":{line_start}-{line_end}"
