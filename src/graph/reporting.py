"""
Reporting: blind spots and high-risk area reports.

Generates two JSON files after Phase 1 + Phase 2 have run:

  <output_dir>/blind_spots.json      — structured blind-spot metrics (parse failures,
                                        dynamic transforms, low-confidence items)
  <output_dir>/high_risk_areas.json  — structured risk metrics (hubs, cycles,
                                        high-velocity, fan-out, dynamic hotspots)

Design principles:
- Report only what is provably in the data — never fabricate issues.
- Fully metric-based JSON: every section has a count in summary + a detail array.
- Structured so the Archivist (Phase 4) can ingest these directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.graph.knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Blind spots / unresolved references
# ---------------------------------------------------------------------------

def write_blind_spots(
    graph: "KnowledgeGraph",
    surveyor_stats: dict[str, Any],
    hydrologist_stats: dict[str, Any],
    output_dir: Path,
) -> Path:
    """
    Write blind_spots.json — fully metric-based JSON.

    Returns the path to the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = _collect_blind_spots(graph, surveyor_stats, hydrologist_stats)

    json_path = output_dir / "blind_spots.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote blind spots JSON → %s", json_path)

    return json_path


def _collect_blind_spots(
    graph: "KnowledgeGraph",
    surveyor_stats: dict[str, Any],
    hydrologist_stats: dict[str, Any],
) -> dict[str, Any]:
    """Gather all blind-spot signals into a structured dict."""

    # 1. Parse failures
    parse_failures = [
        {"file": m.path, "error": m.parse_error}
        for m in graph.all_modules()
        if m.parse_error
    ]

    # 2. Grammar-missing files
    grammar_missing = [
        {"file": m.path, "error": m.parse_error}
        for m in graph.all_modules()
        if m.parse_error and "not installed" in m.parse_error
    ]

    # 3. Modules with no extracted imports or refs (possibly empty or unsupported file)
    structurally_empty = [
        {"file": m.path, "language": m.language.value, "lines": m.lines_of_code}
        for m in graph.all_modules()
        if (
            not m.imports
            and not m.dbt_refs
            and not m.functions
            and not m.classes
            and m.lines_of_code > 5
            and not m.parse_error
            and m.role != "macro"
            and m.language.value not in ("yaml", "shell", "unknown")
        )
    ]

    # 4. Dynamic / unresolved SQL transformations
    dynamic_transforms = [
        {
            "id": t.id,
            "source_file": t.source_file,
            "transformation_type": t.transformation_type,
            "confidence": t.confidence,
            "note": "Contains dynamic Jinja/SQL that could not be fully resolved",
        }
        for t in graph.all_transformations()
        if t.is_dynamic
    ]

    # 5. Low-confidence datasets
    low_confidence_datasets = [
        {
            "name": d.name,
            "dataset_type": d.dataset_type,
            "confidence": d.confidence,
            "is_source": d.is_source_dataset,
            "is_sink": d.is_sink_dataset,
        }
        for d in graph.all_datasets()
        if d.confidence < 0.70
    ]

    # 6. Low-confidence edges
    low_confidence_edges = []
    g = graph._g
    for src, tgt, data in g.edges(data=True):
        c = data.get("confidence", 1.0)
        if c < 0.70:
            low_confidence_edges.append({
                "from": src,
                "to": tgt,
                "edge_type": data.get("edge_type", "?"),
                "confidence": c,
                "evidence": data.get("evidence", {}),
            })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": {
            "parse_failures": len(parse_failures),
            "grammar_missing": surveyor_stats.get("grammar_not_available", 0),
            "structurally_empty_files": len(structurally_empty),
            "dynamic_transformations": len(dynamic_transforms),
            "low_confidence_datasets": len(low_confidence_datasets),
            "low_confidence_edges": len(low_confidence_edges),
            "total_blind_spots": (
                len(parse_failures)
                + len(structurally_empty)
                + len(dynamic_transforms)
                + len(low_confidence_datasets)
                + len(low_confidence_edges)
            ),
        },
        "parse_failures": parse_failures,
        "grammar_missing": grammar_missing,
        "structurally_empty_files": structurally_empty,
        "dynamic_transformations": dynamic_transforms,
        "low_confidence_datasets": low_confidence_datasets,
        "low_confidence_edges": low_confidence_edges,
    }


# ---------------------------------------------------------------------------
# High-risk areas
# ---------------------------------------------------------------------------

def write_high_risk_areas(
    graph: "KnowledgeGraph",
    surveyor_stats: dict[str, Any],
    hydrologist_stats: dict[str, Any],
    output_dir: Path,
) -> Path:
    """
    Write high_risk_areas.json — fully metric-based JSON.

    Returns the path to the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = _collect_high_risk(graph, surveyor_stats, hydrologist_stats)

    json_path = output_dir / "high_risk_areas.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote high-risk areas JSON → %s", json_path)
    return json_path


def _collect_high_risk(
    graph: "KnowledgeGraph",
    surveyor_stats: dict[str, Any],
    hydrologist_stats: dict[str, Any],
) -> dict[str, Any]:
    """Gather all high-risk signals into a structured dict."""

    # 1. High git-velocity files
    raw_velocity: list = surveyor_stats.get("high_velocity_files", [])
    velocity_files = [
        {"file": f, "commits_in_window": c}
        for f, c in raw_velocity[:20]
    ]

    # 2. Top architectural hubs (PageRank)
    raw_hubs: list = surveyor_stats.get("top_hubs", [])
    top_hubs = []
    for node, score in raw_hubs[:15]:
        attrs = graph._g.nodes.get(node, {})
        top_hubs.append({
            "node": node,
            "pagerank_score": round(score, 6),
            "role": attrs.get("role", "unknown"),
            "is_hub": attrs.get("is_hub", False),
            "in_cycle": attrs.get("in_cycle", False),
            "in_degree": graph._g.in_degree(node) if graph._g.is_directed() else graph._g.degree(node),
            "out_degree": graph._g.out_degree(node) if graph._g.is_directed() else 0,
        })

    # 3. Circular dependencies
    cycles = graph.strongly_connected_components()
    circular_deps = [
        {"cycle_id": i + 1, "size": len(cycle), "members": list(cycle)}
        for i, cycle in enumerate(cycles)
    ]

    # 4. Parse-warning files
    parse_warnings = [
        {"file": m.path, "language": m.language.value, "error": m.parse_error}
        for m in graph.all_modules()
        if m.parse_error
    ]

    # 5. High-fan-out transformations (produces ≥ 2 datasets)
    high_fanout = []
    for xform in graph.all_transformations():
        downstream = len(xform.target_datasets)
        if downstream >= 2:
            high_fanout.append({
                "source_file": xform.source_file,
                "transformation_id": xform.id,
                "transformation_type": xform.transformation_type,
                "downstream_dataset_count": downstream,
                "downstream_datasets": list(xform.target_datasets),
            })
    high_fanout.sort(key=lambda x: -x["downstream_dataset_count"])

    # 6. Dynamic / unresolved hotspots
    dynamic_hotspots = [
        {
            "id": t.id,
            "source_file": t.source_file,
            "transformation_type": t.transformation_type,
            "confidence": t.confidence,
        }
        for t in graph.all_transformations()
        if t.is_dynamic
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "velocity_window_days": surveyor_stats.get("velocity_days", 30),
        "summary": {
            "high_velocity_files": len(velocity_files),
            "top_hubs": len(top_hubs),
            "circular_dependency_clusters": len(circular_deps),
            "files_with_parse_warnings": len(parse_warnings),
            "high_fanout_transformations": len(high_fanout),
            "dynamic_hotspot_transformations": len(dynamic_hotspots),
        },
        "high_velocity_files": velocity_files,
        "top_hubs": top_hubs,
        "circular_dependencies": circular_deps,
        "parse_warnings": parse_warnings,
        "high_fanout_transformations": high_fanout,
        "dynamic_hotspots": dynamic_hotspots,
    }

