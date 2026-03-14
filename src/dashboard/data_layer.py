"""
Artifact-driven data access for the Streamlit dashboard.

This module keeps the dashboard testable by separating artifact loading,
graph summarization, evidence lookup, and Phase 4 query handoff from the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Optional

import networkx as nx

from src.agents.archivist import Archivist
from src.agents.navigator import Navigator
from src.models.nodes import DayOneCitation, ModuleNode


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "reports"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_markdown(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize_path(file_path: str) -> str:
    return str(file_path).replace("\\", "/")


def _escape_dot(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\"", "\\\"")


@dataclass
class CodeSnippet:
    file_path: str
    start_line: int
    end_line: int
    text: str
    resolved_path: Optional[Path] = None
    available: bool = True


@dataclass
class DashboardBundle:
    artifact_root: Path
    repo_root: Optional[Path]
    archivist: Archivist
    module_graph: Any
    lineage_graph: Any
    surveyor_stats: dict[str, Any] = field(default_factory=dict)
    hydrologist_stats: dict[str, Any] = field(default_factory=dict)
    semanticist_stats: dict[str, Any] = field(default_factory=dict)
    semantic_enrichment: dict[str, Any] = field(default_factory=dict)
    semantic_index: dict[str, Any] = field(default_factory=dict)
    day_one_answers: dict[str, Any] = field(default_factory=dict)
    reading_order: list[dict[str, Any]] = field(default_factory=list)
    semantic_review_queue: list[dict[str, Any]] = field(default_factory=list)
    semantic_hotspots: list[dict[str, Any]] = field(default_factory=list)
    blind_spots: dict[str, Any] = field(default_factory=dict)
    high_risk_areas: dict[str, Any] = field(default_factory=dict)
    query_logs: list[dict[str, Any]] = field(default_factory=list)
    reports: dict[str, str] = field(default_factory=dict)
    codebase_markdown: str = ""
    onboarding_brief_markdown: str = ""
    module_graph_html: str = ""
    lineage_graph_html: str = ""


def discover_artifact_roots(base_dir: Path) -> list[Path]:
    if (base_dir / "module_graph" / "module_graph.json").exists():
        return [base_dir]
    candidates = [
        child for child in base_dir.iterdir()
        if child.is_dir() and (child / "module_graph" / "module_graph.json").exists()
    ]
    return sorted(candidates)


def load_dashboard_bundle(artifact_root: Path) -> DashboardBundle:
    archivist = Archivist(artifact_root)
    ctx = archivist.context
    repo_root_raw = (
        ctx.surveyor_stats.get("repo_root")
        or ctx.surveyor_stats.get("target")
    )
    repo_root = Path(repo_root_raw) if repo_root_raw else None

    reports = {
        path.stem: _load_markdown(path)
        for path in sorted(REPORTS_DIR.glob("phase*.md"))
    }

    return DashboardBundle(
        artifact_root=archivist.artifact_root,
        repo_root=repo_root if repo_root and repo_root.exists() else repo_root,
        archivist=archivist,
        module_graph=ctx.module_graph,
        lineage_graph=ctx.lineage_graph,
        surveyor_stats=ctx.surveyor_stats,
        hydrologist_stats=ctx.hydrologist_stats,
        semanticist_stats=_load_json(
            archivist.artifact_root / "semantics" / "semanticist_stats.json", {}
        ),
        semantic_enrichment=ctx.semantic_enrichment,
        semantic_index=ctx.semantic_index,
        day_one_answers=ctx.day_one_answers,
        reading_order=ctx.reading_order,
        semantic_review_queue=ctx.semantic_review_queue,
        semantic_hotspots=ctx.semantic_hotspots,
        blind_spots=ctx.blind_spots,
        high_risk_areas=ctx.high_risk_areas,
        query_logs=_load_query_logs(archivist.queries_dir),
        reports=reports,
        codebase_markdown=_load_markdown(archivist.codebase_md_path),
        onboarding_brief_markdown=_load_markdown(archivist.onboarding_brief_path),
        module_graph_html=_load_markdown(
            archivist.artifact_root / "module_graph" / "module_graph.html"
        ),
        lineage_graph_html=_load_markdown(
            archivist.artifact_root / "data_lineage" / "lineage_graph.html"
        ),
    )


def _load_query_logs(queries_dir: Path) -> list[dict[str, Any]]:
    if not queries_dir.exists():
        return []
    logs: list[dict[str, Any]] = []
    for path in sorted(queries_dir.glob("*.json"), reverse=True):
        payload = _load_json(path, {})
        answer = payload.get("answer", {}) if isinstance(payload, dict) else {}
        logs.append(
            {
                "path": path,
                "timestamp": payload.get("timestamp"),
                "query_type": payload.get("query_type"),
                "models_used": payload.get("models_used", {}),
                "question": answer.get("question", ""),
                "answer": answer.get("answer", ""),
                "confidence": float(answer.get("confidence", 0.0) or 0.0),
                "citations": answer.get("citations", []),
            }
        )
    return logs


def build_overview_metrics(bundle: DashboardBundle) -> dict[str, Any]:
    modules = bundle.module_graph.all_modules()
    domains = sorted({module.domain_cluster for module in modules if module.domain_cluster})
    drift_counts = {"no_drift": 0, "possible_drift": 0, "likely_drift": 0}
    for module in modules:
        drift_counts[module.doc_drift_level or "no_drift"] = (
            drift_counts.get(module.doc_drift_level or "no_drift", 0) + 1
        )
    high_hotspots = [
        item for item in bundle.semantic_hotspots
        if float(item.get("hotspot_fusion_score", 0.0) or 0.0) >= 0.5
    ]
    return {
        "total_files": len(modules),
        "datasets": len(bundle.lineage_graph.all_datasets()),
        "transformations": len(bundle.lineage_graph.all_transformations()),
        "semantic_domains": len(domains),
        "hotspots": len(high_hotspots) or len(bundle.semantic_hotspots),
        "documentation_drift": drift_counts["possible_drift"] + drift_counts["likely_drift"],
        "review_queue_items": len(bundle.semantic_review_queue),
        "queries_logged": len(bundle.query_logs),
        "documentation_missing": int(
            bundle.semanticist_stats.get("documentation_missing_count", 0) or 0
        ),
        "drift_counts": drift_counts,
        "domains": domains,
    }


def module_records(bundle: DashboardBundle) -> list[dict[str, Any]]:
    return [
        {
            "file_path": module.path,
            "language": module.language.value,
            "role": module.role,
            "hub": module.is_hub,
            "entry_point": module.is_entry_point,
            "in_cycle": module.in_cycle,
            "velocity": module.change_velocity_30d,
            "business_logic_score": round(module.business_logic_score, 3),
            "hotspot_fusion_score": round(module.hotspot_fusion_score, 3),
            "domain_cluster": module.domain_cluster or "Unassigned",
            "doc_drift_level": module.doc_drift_level or "no_drift",
            "semantic_confidence": round(module.semantic_confidence, 3),
            "purpose_statement": module.purpose_statement or "",
        }
        for module in bundle.module_graph.all_modules()
    ]


def dataset_records(bundle: DashboardBundle) -> list[dict[str, Any]]:
    return [
        {
            "dataset": dataset.name,
            "dataset_type": dataset.dataset_type,
            "source_file": dataset.source_file or "",
            "confidence": dataset.confidence,
            "is_source": dataset.is_source_dataset,
            "is_sink": dataset.is_sink_dataset,
            "columns": len(dataset.columns),
        }
        for dataset in bundle.lineage_graph.all_datasets()
    ]


def hub_records(bundle: DashboardBundle) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for module_path, pagerank in bundle.surveyor_stats.get("top_hubs", []):
        module = bundle.module_graph.get_module(module_path)
        records.append(
            {
                "file_path": module_path,
                "pagerank": float(pagerank),
                "role": module.role if module else "unknown",
                "business_logic_score": module.business_logic_score if module else 0.0,
                "hotspot_fusion_score": module.hotspot_fusion_score if module else 0.0,
            }
        )
    return records


def velocity_records(bundle: DashboardBundle, limit: int = 12) -> list[dict[str, Any]]:
    ranked = sorted(
        bundle.module_graph.all_modules(),
        key=lambda module: (module.change_velocity_30d, module.hotspot_fusion_score),
        reverse=True,
    )
    return [
        {
            "file_path": module.path,
            "velocity": module.change_velocity_30d,
            "role": module.role,
            "hotspot_fusion_score": round(module.hotspot_fusion_score, 3),
        }
        for module in ranked[:limit]
    ]


def domain_records(bundle: DashboardBundle) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    logic_scores: dict[str, float] = {}
    for module in bundle.module_graph.all_modules():
        domain = module.domain_cluster or "Unassigned"
        counts[domain] = counts.get(domain, 0) + 1
        logic_scores[domain] = logic_scores.get(domain, 0.0) + module.business_logic_score
    return [
        {
            "domain": domain,
            "modules": counts[domain],
            "business_logic_total": round(logic_scores[domain], 3),
        }
        for domain in sorted(counts)
    ]


def review_queue_records(bundle: DashboardBundle) -> list[dict[str, Any]]:
    return [
        {
            "file_path": item.get("file_path", ""),
            "hotspot_fusion_score": float(item.get("hotspot_fusion_score", 0.0) or 0.0),
            "semantic_confidence": float(item.get("semantic_confidence", 0.0) or 0.0),
            "doc_drift_level": item.get("doc_drift_level", "no_drift"),
            "reasons": ", ".join(item.get("reasons", [])),
            "evidence_count": len(item.get("evidence", [])),
        }
        for item in bundle.semantic_review_queue
    ]


def reading_order_records(bundle: DashboardBundle, limit: int = 15) -> list[dict[str, Any]]:
    return bundle.reading_order[:limit]


def module_detail(bundle: DashboardBundle, module_path: str) -> Optional[dict[str, Any]]:
    module = bundle.module_graph.get_module(module_path)
    if module is None:
        return None
    lineage = module_lineage_detail(bundle, module_path)
    queue_item = next(
        (item for item in bundle.semantic_review_queue if item.get("file_path") == module_path),
        None,
    )
    return {
        "module": module,
        "imports_out": _module_dependencies(bundle, module_path, outgoing=True),
        "imports_in": _module_dependencies(bundle, module_path, outgoing=False),
        "lineage": lineage,
        "review_queue_item": queue_item,
        "citations": bundle.archivist.module_citations(module_path, limit=6),
    }


def module_lineage_detail(bundle: DashboardBundle, module_path: str) -> dict[str, Any]:
    produced: list[str] = []
    consumed: list[str] = []
    transformations: list[dict[str, Any]] = []
    for transformation in bundle.lineage_graph.all_transformations():
        if transformation.source_file != module_path:
            continue
        produced.extend(transformation.target_datasets)
        consumed.extend(transformation.source_datasets)
        transformations.append(
            {
                "id": transformation.id,
                "type": transformation.transformation_type,
                "line_range": transformation.line_range,
                "source_datasets": transformation.source_datasets,
                "target_datasets": transformation.target_datasets,
                "confidence": transformation.confidence,
            }
        )
    return {
        "produced_datasets": sorted(set(produced)),
        "consumed_datasets": sorted(set(consumed)),
        "transformations": transformations,
    }


def dataset_detail(bundle: DashboardBundle, dataset_name: str) -> Optional[dict[str, Any]]:
    dataset = bundle.lineage_graph.get_dataset(dataset_name)
    if dataset is None:
        return None
    producers = [
        transformation for transformation in bundle.lineage_graph.all_transformations()
        if dataset_name in transformation.target_datasets
    ]
    consumers = [
        transformation for transformation in bundle.lineage_graph.all_transformations()
        if dataset_name in transformation.source_datasets
    ]
    graph = _lineage_digraph(bundle)
    upstream = sorted(nx.ancestors(graph, dataset_name)) if dataset_name in graph else []
    downstream = sorted(nx.descendants(graph, dataset_name)) if dataset_name in graph else []
    return {
        "dataset": dataset,
        "producers": producers,
        "consumers": consumers,
        "upstream": upstream,
        "downstream": downstream,
    }


def build_module_focus_dot(bundle: DashboardBundle, module_path: str) -> str:
    module = bundle.module_graph.get_module(module_path)
    if module is None:
        return "digraph G { label=\"Unknown module\"; }"

    lines = [
        "digraph G {",
        "  rankdir=LR;",
        "  graph [bgcolor=\"transparent\", pad=\"0.2\"];",
        "  node [shape=box, style=\"rounded,filled\", fontname=\"Helvetica\", fontsize=11, color=\"#1f2937\", fillcolor=\"#f8fafc\"];",
        "  edge [fontname=\"Helvetica\", fontsize=10, color=\"#64748b\"];",
    ]

    selected = _escape_dot(module_path)
    lines.append(
        f"  \"{selected}\" [fillcolor=\"#0f766e\", fontcolor=\"white\", color=\"#115e59\", penwidth=2.2];"
    )

    for dependency in _module_dependencies(bundle, module_path, outgoing=True):
        dep = _escape_dot(dependency["file_path"])
        color = "#16a34a" if dependency["edge_type"] == "DBT_REF" else "#64748b"
        label = dependency["edge_type"].lower()
        lines.append(f"  \"{dep}\" [fillcolor=\"#e0f2fe\", color=\"#0284c7\"];")
        lines.append(f"  \"{selected}\" -> \"{dep}\" [label=\"{label}\", color=\"{color}\"];")

    for dependency in _module_dependencies(bundle, module_path, outgoing=False):
        dep = _escape_dot(dependency["file_path"])
        color = "#16a34a" if dependency["edge_type"] == "DBT_REF" else "#94a3b8"
        label = dependency["edge_type"].lower()
        lines.append(f"  \"{dep}\" [fillcolor=\"#fef3c7\", color=\"#d97706\"];")
        lines.append(f"  \"{dep}\" -> \"{selected}\" [label=\"{label}\", color=\"{color}\"];")

    lines.append("}")
    return "\n".join(lines)


def build_lineage_focus_dot(bundle: DashboardBundle, dataset_name: str) -> str:
    detail = dataset_detail(bundle, dataset_name)
    if detail is None:
        return "digraph G { label=\"Unknown dataset\"; }"

    selected = _escape_dot(dataset_name)
    lines = [
        "digraph G {",
        "  rankdir=LR;",
        "  graph [bgcolor=\"transparent\", pad=\"0.2\"];",
        "  node [fontname=\"Helvetica\", fontsize=11];",
        "  edge [fontname=\"Helvetica\", fontsize=10, color=\"#64748b\"];",
        f"  \"{selected}\" [shape=ellipse, style=\"filled\", fillcolor=\"#0f766e\", fontcolor=\"white\", color=\"#115e59\", penwidth=2.2];",
    ]

    for transformation in detail["producers"]:
        node_id = _escape_dot(transformation.id)
        label = _escape_dot(Path(transformation.source_file).name)
        lines.append(
            f"  \"{node_id}\" [shape=box, style=\"rounded,filled\", fillcolor=\"#fef3c7\", color=\"#d97706\", label=\"{label}\"];"
        )
        lines.append(f"  \"{node_id}\" -> \"{selected}\" [label=\"produces\", color=\"#16a34a\"];")
        for upstream in transformation.source_datasets[:6]:
            upstream_id = _escape_dot(upstream)
            lines.append(
                f"  \"{upstream_id}\" [shape=ellipse, style=\"filled\", fillcolor=\"#dbeafe\", color=\"#2563eb\"];"
            )
            lines.append(
                f"  \"{upstream_id}\" -> \"{node_id}\" [label=\"consumes\", color=\"#2563eb\"];"
            )

    for transformation in detail["consumers"]:
        node_id = _escape_dot(transformation.id)
        label = _escape_dot(Path(transformation.source_file).name)
        lines.append(
            f"  \"{node_id}\" [shape=box, style=\"rounded,filled\", fillcolor=\"#fee2e2\", color=\"#dc2626\", label=\"{label}\"];"
        )
        lines.append(f"  \"{selected}\" -> \"{node_id}\" [label=\"consumed by\", color=\"#dc2626\"];")
        for downstream in transformation.target_datasets[:6]:
            downstream_id = _escape_dot(downstream)
            lines.append(
                f"  \"{downstream_id}\" [shape=ellipse, style=\"filled\", fillcolor=\"#ecfccb\", color=\"#65a30d\"];"
            )
            lines.append(
                f"  \"{node_id}\" -> \"{downstream_id}\" [label=\"produces\", color=\"#16a34a\"];"
            )

    lines.append("}")
    return "\n".join(lines)


def resolve_repo_file(bundle: DashboardBundle, file_path: str) -> Optional[Path]:
    normalized = _normalize_path(file_path)
    if bundle.repo_root:
        candidate = bundle.repo_root / normalized
        if candidate.exists():
            return candidate
    module = bundle.module_graph.get_module(normalized)
    if module and Path(module.abs_path).exists():
        return Path(module.abs_path)
    return None


def load_code_snippet(
    bundle: DashboardBundle,
    file_path: str,
    line_start: Optional[int],
    line_end: Optional[int],
    context_lines: int = 2,
) -> CodeSnippet:
    resolved = resolve_repo_file(bundle, file_path)
    if resolved is None or not resolved.exists():
        return CodeSnippet(
            file_path=file_path,
            start_line=line_start or 1,
            end_line=line_end or (line_start or 1),
            text="Source file is not available from the saved artifact context.",
            resolved_path=None,
            available=False,
        )

    lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    total_lines = max(len(lines), 1)
    start = max(1, (line_start or 1) - context_lines)
    end = min(total_lines, (line_end or line_start or start) + context_lines)
    snippet_lines = [
        f"{number:>4} | {lines[number - 1]}"
        for number in range(start, end + 1)
    ]
    return CodeSnippet(
        file_path=file_path,
        start_line=start,
        end_line=end,
        text="\n".join(snippet_lines),
        resolved_path=resolved,
        available=True,
    )


def evidence_for_module(bundle: DashboardBundle, module_path: str) -> list[DayOneCitation]:
    detail = module_detail(bundle, module_path)
    if detail is None:
        return []
    return list(detail["citations"])


def run_navigator_query(
    artifact_root: Path,
    question: str,
    navigator_factory: Callable[..., Any] = Navigator,
) -> dict[str, Any]:
    navigator = navigator_factory(artifact_root)
    result = navigator.answer_question(question)
    return {
        "question": question,
        "answer": result.response.answer,
        "confidence": result.response.confidence,
        "citations": [citation.model_dump(mode="json") for citation in result.response.citations],
        "query_type": result.query_type,
        "models_used": result.models_used,
        "log_path": str(result.log_path) if result.log_path else "",
    }


def _module_dependencies(
    bundle: DashboardBundle,
    module_path: str,
    outgoing: bool,
) -> list[dict[str, Any]]:
    graph = bundle.module_graph._g
    iterator = (
        graph.out_edges(module_path, data=True)
        if outgoing else
        graph.in_edges(module_path, data=True)
    )
    records = []
    for source, target, data in iterator:
        edge_type = data.get("edge_type")
        if edge_type not in {"IMPORTS", "DBT_REF"}:
            continue
        peer = target if outgoing else source
        records.append(
            {
                "file_path": peer,
                "edge_type": edge_type,
                "confidence": data.get("confidence", 0.0),
            }
        )
    return sorted(records, key=lambda item: item["file_path"])


def _lineage_digraph(bundle: DashboardBundle) -> nx.DiGraph:
    graph = nx.DiGraph()
    for source, target, data in bundle.lineage_graph._g.edges(data=True):
        if data.get("edge_type") in {"PRODUCES", "CONSUMES"}:
            graph.add_edge(source, target)
    return graph
