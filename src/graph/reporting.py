"""
Reporting: blind spots and high-risk area reports.

Generates two output files after Phase 1 + Phase 2 have run:

  .cartography/unresolved_references.json  — machine-readable blind spots
  .cartography/blind_spots.md              — human-readable blind spots narrative
  .cartography/high_risk_areas.md          — aggregated risk summary

Design principles:
- Report only what is provably in the data — never fabricate issues.
- Graceful: if a section has no data, say so rather than omitting it.
- Structured so the Archivist (Phase 4) can ingest these directly.
"""

from __future__ import annotations

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
) -> tuple[Path, Path]:
    """
    Write unresolved_references.json and blind_spots.md.

    Returns (json_path, md_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = _collect_blind_spots(graph, surveyor_stats, hydrologist_stats)

    json_path = output_dir / "unresolved_references.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote blind spots JSON → %s", json_path)

    md_path = output_dir / "blind_spots.md"
    md_path.write_text(_render_blind_spots_md(payload), encoding="utf-8")
    logger.info("Wrote blind spots report → %s", md_path)

    return json_path, md_path


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
            and m.language.value not in ("yaml", "shell", "unknown")
        )
    ]

    # 4. Dynamic / unresolved SQL transformations
    dynamic_transforms = [
        {
            "id": t.id,
            "source_file": t.source_file,
            "confidence": t.confidence,
            "note": "Contains dynamic Jinja/SQL that could not be fully resolved",
        }
        for t in graph.all_transformations()
        if t.is_dynamic
    ]

    # 5. Low-confidence datasets
    low_confidence_datasets = [
        {"name": d.name, "type": d.dataset_type, "confidence": d.confidence}
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
        "summary": {
            "parse_failures": len(parse_failures),
            "grammar_missing": surveyor_stats.get("grammar_not_available", 0),
            "structurally_empty_files": len(structurally_empty),
            "dynamic_transformations": len(dynamic_transforms),
            "low_confidence_datasets": len(low_confidence_datasets),
            "low_confidence_edges": len(low_confidence_edges),
        },
        "parse_failures": parse_failures,
        "grammar_missing": grammar_missing,
        "structurally_empty_files": structurally_empty,
        "dynamic_transformations": dynamic_transforms,
        "low_confidence_datasets": low_confidence_datasets,
        "low_confidence_edges": low_confidence_edges,
    }


def _render_blind_spots_md(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        "# Blind Spots & Unresolved References",
        "",
        "This report lists files, edges, and datasets where the Cartographer",
        "could not establish high-confidence intelligence.  Use it to prioritise",
        "manual review or refactoring.",
        "",
        "## Summary",
        "",
        f"| Category | Count |",
        f"|---|---|",
        f"| Parse failures | {s['parse_failures']} |",
        f"| Grammar not installed | {s['grammar_missing']} |",
        f"| Structurally empty files (no imports/refs extracted) | {s['structurally_empty_files']} |",
        f"| Dynamic / partially-unresolved SQL transformations | {s['dynamic_transformations']} |",
        f"| Low-confidence datasets (< 70 %) | {s['low_confidence_datasets']} |",
        f"| Low-confidence edges (< 70 %) | {s['low_confidence_edges']} |",
        "",
    ]

    _section(lines, "Parse Failures", payload["parse_failures"],
             lambda x: f"- `{x['file']}` — {x['error']}")

    _section(lines, "Grammar Not Installed", payload["grammar_missing"],
             lambda x: f"- `{x['file']}` — {x['error']}")

    _section(lines, "Structurally Empty Files", payload["structurally_empty_files"],
             lambda x: f"- `{x['file']}` ({x['language']}, {x['lines']} lines) — no imports or refs extracted")

    _section(lines, "Dynamic SQL Transformations", payload["dynamic_transformations"],
             lambda x: f"- `{x['source_file']}` (id={x['id']}, confidence={x['confidence']:.2f}) — {x['note']}")

    _section(lines, "Low-Confidence Datasets", payload["low_confidence_datasets"],
             lambda x: f"- `{x['name']}` (type={x['type']}, confidence={x['confidence']:.2f})")

    _section(lines, "Low-Confidence Edges", payload["low_confidence_edges"],
             lambda x: (
                 f"- `{x['from']}` → `{x['to']}` "
                 f"[{x['edge_type']}] confidence={x['confidence']:.2f}"
             ))

    return "\n".join(lines) + "\n"


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
    Write high_risk_areas.md.

    Returns md_path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / "high_risk_areas.md"
    md_path.write_text(
        _render_high_risk_md(graph, surveyor_stats, hydrologist_stats),
        encoding="utf-8",
    )
    logger.info("Wrote high-risk areas report → %s", md_path)
    return md_path


def _render_high_risk_md(
    graph: "KnowledgeGraph",
    surveyor_stats: dict[str, Any],
    hydrologist_stats: dict[str, Any],
) -> str:
    lines = [
        "# High-Risk Areas",
        "",
        "Files and datasets that carry the highest structural, velocity, or",
        "dependency risk in this repository.  Prioritise code review and",
        "test coverage for these items.",
        "",
    ]

    # --- 1. High git velocity files ---
    velocity_files: list[tuple[str, int]] = surveyor_stats.get("high_velocity_files", [])
    if velocity_files:
        lines += [
            "## 1. High Change-Velocity Files (last 30 days)",
            "",
            "Files with the most commits pose the highest regression risk.",
            "",
            "| File | Commits (30d) |",
            "|---|---|",
        ]
        for f, count in velocity_files[:10]:
            lines.append(f"| `{f}` | {count} |")
        lines.append("")
    else:
        lines += ["## 1. High Change-Velocity Files", "", "_No git velocity data available._", ""]

    # --- 2. Top graph hubs ---
    hubs: list[tuple[str, float]] = surveyor_stats.get("top_hubs", [])
    if hubs:
        lines += [
            "## 2. Top Architectural Hubs (PageRank)",
            "",
            "Nodes with the highest PageRank are depended on by many others.",
            "Breaking changes here will cascade widely.",
            "",
            "| Module / Dataset | PageRank Score |",
            "|---|---|",
        ]
        for node, score in hubs[:10]:
            lines.append(f"| `{node}` | {score:.4f} |")
        lines.append("")
    else:
        lines += ["## 2. Top Architectural Hubs", "", "_No hub data available._", ""]

    # --- 3. Circular dependencies ---
    cycles = graph.strongly_connected_components()
    if cycles:
        lines += [
            "## 3. Circular Dependencies",
            "",
            f"**{len(cycles)} cycle(s) detected.**  Circular imports make the codebase",
            "harder to test, refactor, and understand.",
            "",
        ]
        for i, cycle in enumerate(cycles[:5], 1):
            lines.append(f"### Cycle {i}")
            for node in cycle:
                lines.append(f"- `{node}`")
            lines.append("")
        if len(cycles) > 5:
            lines.append(f"_…and {len(cycles) - 5} more cycles (see module_graph.json)._\n")
    else:
        lines += ["## 3. Circular Dependencies", "", "_No circular dependencies detected._", ""]

    # --- 4. Parse-warning files ---
    parse_errors = [m for m in graph.all_modules() if m.parse_error]
    if parse_errors:
        lines += [
            "## 4. Files with Parse Warnings",
            "",
            "These files could not be fully parsed.  Coverage gaps exist here.",
            "",
            "| File | Error |",
            "|---|---|",
        ]
        for m in parse_errors[:20]:
            err = (m.parse_error or "").replace("|", "\\|")[:80]
            lines.append(f"| `{m.path}` | {err} |")
        lines.append("")
    else:
        lines += ["## 4. Files with Parse Warnings", "", "_No parse warnings._", ""]

    # --- 5. High-fan-out transformations ---
    high_fanout = []
    for xform in graph.all_transformations():
        downstream = len(xform.target_datasets)
        if downstream >= 2:
            high_fanout.append((xform.source_file, downstream))
    high_fanout.sort(key=lambda x: -x[1])

    if high_fanout:
        lines += [
            "## 5. Transformations with Many Downstream Dependencies",
            "",
            "A change to any of these SQL models will affect multiple downstream datasets.",
            "",
            "| SQL File | Downstream Datasets |",
            "|---|---|",
        ]
        for f, count in high_fanout[:10]:
            lines.append(f"| `{f}` | {count} |")
        lines.append("")
    else:
        lines += ["## 5. High-Fan-Out Transformations", "", "_None detected._", ""]

    # --- 6. Unresolved hotspots ---
    dynamic_xforms = [t for t in graph.all_transformations() if t.is_dynamic]
    if dynamic_xforms:
        lines += [
            "## 6. Unresolved / Dynamic Hotspots",
            "",
            "These files contain dynamic SQL or Jinja that could not be fully resolved.",
            "Lineage from these files may be incomplete.",
            "",
            "| File | Confidence |",
            "|---|---|",
        ]
        for t in dynamic_xforms:
            lines.append(f"| `{t.source_file}` | {t.confidence:.2f} |")
        lines.append("")
    else:
        lines += ["## 6. Unresolved / Dynamic Hotspots", "", "_No dynamic transformations._", ""]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section(
    lines: list[str],
    title: str,
    items: list,
    formatter: "Callable[[Any], str]",
) -> None:
    from typing import Callable
    lines.append(f"## {title}")
    lines.append("")
    if items:
        for item in items:
            lines.append(formatter(item))
    else:
        lines.append("_None detected._")
    lines.append("")
