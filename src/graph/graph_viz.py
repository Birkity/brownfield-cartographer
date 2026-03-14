"""
Graph visualization helpers for the Brownfield Cartographer.

Exposes three public functions:
  export_module_viz_html(g, output_path) — interactive HTML (PyVis)  ← primary
  export_module_viz(g, output_path)      — dark-theme PNG (matplotlib, legacy)
  export_lineage_viz(g, datasets, transformations, output_path) — interactive HTML (PyVis)

Both accept the raw networkx DiGraph and data dicts, making them
independent of KnowledgeGraph and easy to unit-test.
"""

from __future__ import annotations

import logging
from pathlib import Path

import networkx as nx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module graph — PNG (matplotlib)
# ---------------------------------------------------------------------------

_BG = "#F3F7F6"
_TEXT = "#10212B"
_EDGE_IMPORT = "#2563EB"
_EDGE_DBT = "#15803D"

_LANG_COLOURS: dict[str, str] = {
    "python":     "#BAE6FD",
    "sql":        "#FDE68A",
    "yaml":       "#BBF7D0",
    "javascript": "#FED7AA",
    "typescript": "#BFDBFE",
    "java":       "#FECACA",
    "kotlin":     "#E9D5FF",
    "scala":      "#FECDD3",
    "go":         "#A5F3FC",
    "rust":       "#FDBA74",
    "csharp":     "#DCFCE7",
    "ruby":       "#FBCFE8",
    "shell":      "#D9F99D",
    "notebook":   "#E9D5FF",
    "external":   "#E2E8F0",
}


def export_module_viz(g: nx.DiGraph, output_path: Path) -> bool:
    """
    Export the module import graph as a dark-theme PNG.

    Tries pydot first (needs graphviz binary), then falls back to matplotlib.
    Returns True on success, False on failure.
    """
    if g.number_of_nodes() == 0:
        logger.warning("Graph is empty — skipping visualization")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # -- Attempt 1: pydot ----------------------------------------
    try:
        from networkx.drawing.nx_pydot import to_pydot  # type: ignore[import]
        dot = to_pydot(g)
        dot.write_png(str(output_path))
        logger.info("Saved graph visualization (pydot) → %s", output_path)
        return True
    except Exception as exc:
        logger.debug("pydot failed (%s), falling back to matplotlib", exc)

    # -- Attempt 2: matplotlib ------------------------------------
    try:
        import matplotlib  # type: ignore[import]
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore[import]
        import matplotlib.patches as mpatches  # type: ignore[import]

        n_nodes = g.number_of_nodes()
        fig_w = max(28, min(n_nodes * 1.2, 72))
        fig_h = max(20, min(n_nodes * 0.85, 54))
        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        fig.patch.set_facecolor(_BG)
        ax.set_facecolor(_BG)

        if n_nodes <= 15:
            pos = nx.shell_layout(g)
        elif n_nodes <= 60:
            pos = nx.spring_layout(g, k=3.5 / (n_nodes ** 0.5 + 1), seed=42, iterations=120)
        else:
            pos = nx.kamada_kawai_layout(g)

        degrees = dict(g.in_degree())
        max_deg = max(degrees.values()) if degrees else 1
        base_size = max(1200, 4000 - n_nodes * 25)
        node_sizes = [
            base_size + (degrees.get(n, 0) / max(max_deg, 1)) * base_size * 2.0
            for n in g.nodes()
        ]
        node_colors = [
            _LANG_COLOURS.get(g.nodes[n].get("language", "external"), "#78909C")
            for n in g.nodes()
        ]
        edge_colors = [
            _EDGE_DBT if d.get("edge_type") == "DBT_REF" else _EDGE_IMPORT
            for _, _, d in g.edges(data=True)
        ]
        # Edge widths: scale by confidence where available
        edge_widths = [
            max(0.8, 3.0 * d.get("confidence", 1.0))
            for _, _, d in g.edges(data=True)
        ]

        nx.draw_networkx_edges(
            g, pos=pos, ax=ax, edge_color=edge_colors,
            width=edge_widths, alpha=0.75, arrows=True, arrowsize=18,
            arrowstyle="-|>", node_size=node_sizes, connectionstyle="arc3,rad=0.08",
        )

        # --- Base nodes coloured by language ---
        node_list = list(g.nodes())
        nx.draw_networkx_nodes(
            g, pos=pos, ax=ax, nodelist=node_list,
            node_color=node_colors, node_size=node_sizes,
            alpha=0.95, linewidths=1.5, edgecolors="#FFFFFF22",
        )

        # --- Role-based visual overlays ---
        hub_nodes   = [n for n in node_list if g.nodes[n].get("is_hub", False)]
        cycle_nodes = [n for n in node_list if g.nodes[n].get("in_cycle", False)]
        entry_nodes = [n for n in node_list if g.nodes[n].get("is_entry_point", False)]

        if hub_nodes:
            hub_sizes = [node_sizes[node_list.index(n)] * 1.45 for n in hub_nodes]
            nx.draw_networkx_nodes(
                g, pos=pos, ax=ax, nodelist=hub_nodes,
                node_color="none", node_size=hub_sizes,
                linewidths=3.5, edgecolors="#FFD700",    # gold ring = hub
            )
        if cycle_nodes:
            cyc_sizes = [node_sizes[node_list.index(n)] * 1.45 for n in cycle_nodes]
            nx.draw_networkx_nodes(
                g, pos=pos, ax=ax, nodelist=cycle_nodes,
                node_color="none", node_size=cyc_sizes,
                linewidths=3.5, edgecolors="#FF4757",    # red ring = cycle
            )
        if entry_nodes:
            ent_sizes = [node_sizes[node_list.index(n)] * 1.35 for n in entry_nodes]
            nx.draw_networkx_nodes(
                g, pos=pos, ax=ax, nodelist=entry_nodes,
                node_color="none", node_size=ent_sizes,
                linewidths=2.5, edgecolors="#2ED573",    # green ring = entry-point
            )

        font_size = max(9, min(15, 200 // max(n_nodes, 1)))
        # Label includes role badge for nodes that have a known role
        labels = {}
        for n in g.nodes():
            stem = n.split("/")[-1].replace(".py","").replace(".sql","") \
                     .replace(".yaml","").replace(".yml","")
            role = g.nodes[n].get("role", "unknown")
            role_badge = {
                "staging":      " [stg]",
                "mart":         " [mart]",
                "intermediate": " [int]",
                "source":       " [src]",
                "macro":        " [macro]",
                "test":         " [test]",
                "config":       " [cfg]",
            }.get(role, "")
            labels[n] = stem + role_badge
        nx.draw_networkx_labels(g, pos=pos, labels=labels, ax=ax,
                                font_size=font_size, font_color=_TEXT, font_weight="bold")

        import_edges = sum(1 for _, _, d in g.edges(data=True)
                           if d.get("edge_type", "IMPORTS") == "IMPORTS")
        dbt_edges = sum(1 for _, _, d in g.edges(data=True)
                        if d.get("edge_type") == "DBT_REF")
        ax.set_title(
            f"Module Import Graph  ·  {n_nodes} nodes  ·  "
            f"{import_edges} imports  ·  {dbt_edges} dbt refs",
            fontsize=20, color=_TEXT, pad=22, fontweight="bold",
        )
        ax.axis("off")

        seen_langs = {g.nodes[n].get("language", "external") for n in g.nodes()}
        legend_handles = [
            mpatches.Patch(color=_LANG_COLOURS.get(lang, "#78909C"), label=lang.capitalize())
            for lang in sorted(seen_langs) if lang in _LANG_COLOURS
        ]
        if import_edges:
            legend_handles.append(mpatches.Patch(color=_EDGE_IMPORT, label="IMPORTS edge"))
        if dbt_edges:
            legend_handles.append(mpatches.Patch(color=_EDGE_DBT, label="DBT_REF edge"))
        # Role-ring legend entries
        if hub_nodes:
            legend_handles.append(mpatches.Patch(
                facecolor="none", edgecolor="#FFD700", linewidth=2.5, label="Hub (PageRank top-10)"))
        if cycle_nodes:
            legend_handles.append(mpatches.Patch(
                facecolor="none", edgecolor="#FF4757", linewidth=2.5, label="In circular dependency"))
        if entry_nodes:
            legend_handles.append(mpatches.Patch(
                facecolor="none", edgecolor="#2ED573", linewidth=2.5, label="Entry point (no in-edges)"))
        if legend_handles:
            leg = ax.legend(
                handles=legend_handles, loc="lower left",
                framealpha=0.35, facecolor="#161B22", edgecolor="#30363D",
                labelcolor=_TEXT, fontsize=13, title="Legend", title_fontsize=13,
            )
            leg.get_title().set_color(_TEXT)

        plt.tight_layout(pad=1.5)
        plt.savefig(str(output_path), dpi=200, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        logger.info("Saved graph visualization (matplotlib) → %s", output_path)
        return True
    except Exception as exc:
        logger.warning("matplotlib visualization also failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Module graph — interactive HTML (PyVis)  ← primary visualization
# ---------------------------------------------------------------------------

_ROLE_BADGE: dict[str, str] = {
    "staging":      "[stg]",
    "mart":         "[mart]",
    "intermediate": "[int]",
    "source":       "[src]",
    "macro":        "[macro]",
    "test":         "[test]",
    "config":       "[cfg]",
}

_MODULE_PYVIS_OPTIONS = """
var options = {
  "nodes": {
    "borderWidth": 3,
    "borderWidthSelected": 5,
    "shadow": {"enabled": true, "color": "rgba(15,23,42,0.18)", "size": 16, "x": 2, "y": 4},
    "font": {"color": "#10212B", "size": 18,
             "face": "Trebuchet MS, Segoe UI, sans-serif",
             "strokeWidth": 6, "strokeColor": "#F8FBFA",
             "bold": {"color": "#10212B", "size": 19}}
  },
  "edges": {
    "smooth": {"type": "dynamic", "roundness": 0.18},
    "shadow": {"enabled": true, "color": "rgba(15,23,42,0.12)", "size": 7},
    "width": 3,
    "selectionWidth": 5,
    "arrows": {"to": {"enabled": true, "scaleFactor": 1.2, "type": "arrow"}},
    "font": {"color": "#334155", "size": 13, "align": "middle",
             "strokeWidth": 4, "strokeColor": "#F8FBFA"}
  },
  "physics": {
    "barnesHut": {
      "gravitationalConstant": -11000, "centralGravity": 0.18,
      "springLength": 250, "springConstant": 0.03,
      "damping": 0.18, "avoidOverlap": 1.0
    },
    "minVelocity": 0.2,
    "stabilization": {"iterations": 350}
  },
  "interaction": {
    "hover": true, "tooltipDelay": 80, "navigationButtons": true,
    "keyboard": {"enabled": true, "bindToWindow": false},
    "multiselect": true, "zoomView": true, "dragView": true
  }
}
"""


def _module_node_tooltip(node: str, attrs: dict) -> str:
    lang = attrs.get("language", "external")
    role = attrs.get("role") or "unknown"
    loc = attrs.get("lines_of_code", 0)
    fn = attrs.get("function_count", 0)
    cl = attrs.get("class_count", 0)
    imp = attrs.get("import_count", 0)
    dbt = attrs.get("dbt_ref_count", 0)
    vel = attrs.get("change_velocity_30d", 0)
    cx = attrs.get("complexity_score", 0.0)
    conf = attrs.get("classification_confidence", 1.0)
    conf_color = "#15803D" if conf >= 0.9 else "#C2410C"

    flags = []
    if attrs.get("is_hub"):
        flags.append("<span style='color:#FFD700'>★ Hub</span>")
    if attrs.get("in_cycle"):
        flags.append("<span style='color:#FF4757'>⟳ In cycle</span>")
    if attrs.get("is_entry_point"):
        flags.append("<span style='color:#2ED573'>↳ Entry point</span>")
    if attrs.get("is_dead_code_candidate"):
        flags.append("<span style='color:#8B949E'>☠ Dead-code candidate</span>")
    perr = attrs.get("parse_error")

    lines = [
        "<div style='font-family:Trebuchet MS, Segoe UI, sans-serif;font-size:13px;padding:10px 13px;"
        "background:#FFFFFF;border:1px solid #D7E3E0;border-radius:12px;max-width:400px;"
        "box-shadow:0 18px 36px rgba(15,23,42,0.14);color:#10212B'>",
        f"<b style='color:#1D4ED8;font-size:15px'>{node}</b><br>",
        f"<span style='color:#64748B'>Language:</span> <b style='color:#10212B'>{lang}</b>"
        f" &nbsp; <span style='color:#64748B'>Role:</span> <b style='color:#10212B'>{role}</b><br>",
        f"<span style='color:#64748B'>LoC:</span> {loc}"
        f" &nbsp; <span style='color:#64748B'>Fn:</span> {fn}"
        f" &nbsp; <span style='color:#64748B'>Class:</span> {cl}"
        f" &nbsp; <span style='color:#64748B'>Imports:</span> {imp}<br>",
    ]
    if dbt:
        lines.append(f"<span style='color:#64748B'>dbt refs:</span> {dbt}<br>")
    lines += [
        f"<span style='color:#64748B'>Velocity 30d:</span> {vel:.1f}"
        f" &nbsp; <span style='color:#64748B'>Complexity:</span> {cx:.2f}<br>",
        f"<span style='color:#64748B'>Confidence:</span> "
        f"<b style='color:{conf_color}'>{conf:.0%}</b>",
    ]
    if flags:
        lines.append("<br>" + " &nbsp; ".join(flags))
    if perr:
        lines.append(f"<br><span style='color:#FF4757'>⚠ parse error: {perr}</span>")
    lines.append("</div>")
    return "".join(lines)


def _build_module_legend(g: nx.DiGraph) -> str:
    lang_counts: dict[str, int] = {}
    for _, attrs in g.nodes(data=True):
        lang = attrs.get("language", "external")
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

    lang_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
        f"<span style='width:13px;height:13px;background:{_LANG_COLOURS.get(lang, '#546E7A')};"
        f"border-radius:50%;display:inline-block'></span>"
        f"<span>{lang} <span style='color:#64748B'>({cnt})</span></span></div>"
        for lang, cnt in sorted(lang_counts.items(), key=lambda x: -x[1])
    )
    border_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
        f"<span style='width:13px;height:13px;border:2px solid {c};"
        f"border-radius:50%;display:inline-block'></span>"
        f"<span>{label}</span></div>"
        for c, label in [
            ("#FFD700", "★ Hub (high degree)"),
            ("#FF4757", "⟳ Circular dep"),
            ("#2ED573", "↳ Entry point"),
        ]
    )
    edge_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
        f"<span style='width:22px;height:3px;background:{c};display:inline-block'></span>"
        f"<span>{label}</span></div>"
        for c, label in [("#58A6FF", "IMPORTS"), ("#3FB950", "DBT_REF")]
    )
    return (
        "<div style='"
        "position:fixed;bottom:20px;left:20px;"
        "background:rgba(255,255,255,0.96);border:1px solid #D7E3E0;border-radius:14px;"
        "padding:14px 18px;color:#10212B;font-family:Trebuchet MS, Segoe UI, sans-serif;font-size:13px;"
        "box-shadow:0 18px 36px rgba(15,23,42,0.12);z-index:9999;min-width:200px'>"
        "<div style='font-weight:bold;margin-bottom:10px;color:#1D4ED8;font-size:14px'>Legend</div>"
        "<div style='font-size:11px;color:#64748B;margin-bottom:6px;text-transform:uppercase;"
        "letter-spacing:1px'>Language</div>"
        + lang_rows
        + "<div style='font-size:11px;color:#64748B;margin:10px 0 6px;text-transform:uppercase;"
        "letter-spacing:1px'>Node border</div>"
        + border_rows
        + "<div style='font-size:11px;color:#64748B;margin:10px 0 6px;text-transform:uppercase;"
        "letter-spacing:1px'>Edge type</div>"
        + edge_rows
        + "</div>"
    )


def export_module_viz_html(g: nx.DiGraph, output_path: Path) -> bool:
    """
    Export the module import graph as an interactive dark-theme HTML (PyVis).

    Node colours reflect file language.  Border colour indicates role:
      gold=hub, red=cycle, green=entry-point.
    Hover a node to see full metrics; edge width scales with confidence.

    Returns True on success, False when pyvis is not installed or the graph is empty.
    """
    if g.number_of_nodes() == 0:
        logger.warning("Graph is empty — skipping module visualization")
        return False

    try:
        from pyvis.network import Network  # type: ignore[import]
    except ImportError:
        logger.warning("pyvis not installed — skipping module visualization")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    net = Network(
        height="100vh", width="100%", directed=True,
        bgcolor="#F3F7F6", font_color="#10212B", notebook=False,
    )
    net.set_options(_MODULE_PYVIS_OPTIONS)

    degrees = dict(g.degree())
    max_deg = max(degrees.values(), default=1) or 1

    for node, attrs in g.nodes(data=True):
        lang = attrs.get("language", "external")
        role = attrs.get("role") or "unknown"
        bg_color = _LANG_COLOURS.get(lang, "#546E7A")

        if attrs.get("is_hub"):
            border_color = "#FFD700"
        elif attrs.get("in_cycle"):
            border_color = "#FF4757"
        elif attrs.get("is_entry_point"):
            border_color = "#2ED573"
        else:
            border_color = "#30363D"

        parts = node.replace("\\", "/").split("/")
        short = parts[-1].rsplit(".", 1)[0]
        badge = _ROLE_BADGE.get(role, "")
        display_label = f"{short}\n{badge}" if badge else short

        deg = degrees.get(node, 0)
        size = 28 + int(28 * (deg / max_deg))

        net.add_node(
            node,
            label=display_label,
            title=_module_node_tooltip(node, attrs),
            color={
                "background": bg_color,
                "border": border_color,
                "highlight": {"background": bg_color, "border": "#FFFFFF"},
            },
            font={
                "color": "#10212B",
                "size": 18,
                "face": "Trebuchet MS, Segoe UI, sans-serif",
                "strokeWidth": 6,
                "strokeColor": "#F8FBFA",
                "bold": {"color": "#10212B", "size": 19},
            },
            shape="ellipse",
            size=size,
        )

    for u, v, data in g.edges(data=True):
        edge_type = data.get("edge_type", "IMPORTS")
        conf = data.get("confidence", 1.0)
        width = max(1.0, conf * 3.0)
        if edge_type == "DBT_REF":
            color = {"color": "#15803D", "highlight": "#22C55E", "opacity": 0.82}
            edge_title = "<b style='color:#15803D'>DBT_REF</b>"
        else:
            color = {"color": "#2563EB", "highlight": "#60A5FA", "opacity": 0.76}
            edge_title = "<b style='color:#2563EB'>IMPORTS</b>"
        net.add_edge(u, v, color=color, title=edge_title, width=width)

    try:
        html_content = net.generate_html()
        legend_html = _build_module_legend(g)
        html_content = html_content.replace("</body>", legend_html + "\n</body>")
        output_path.write_text(html_content, encoding="utf-8")
        logger.info("Saved module graph visualization (PyVis) → %s", output_path)
        return True
    except Exception as exc:
        logger.warning("PyVis module viz save failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Lineage graph — interactive HTML (PyVis)
# ---------------------------------------------------------------------------

_DS_COLORS: dict[str, dict] = {
    "dbt_source": {"background": "#FECACA", "border": "#DC2626",
                   "highlight": {"background": "#FCA5A5", "border": "#991B1B"}},
    "dbt_model":  {"background": "#BFDBFE", "border": "#2563EB",
                   "highlight": {"background": "#93C5FD", "border": "#1D4ED8"}},
    "dbt_seed":   {"background": "#DCFCE7", "border": "#16A34A",
                   "highlight": {"background": "#BBF7D0", "border": "#15803D"}},
    "table_ref":  {"background": "#FDE68A", "border": "#D97706",
                   "highlight": {"background": "#FCD34D", "border": "#B45309"}},
    "file_read":  {"background": "#DDD6FE", "border": "#7C3AED",
                   "highlight": {"background": "#C4B5FD", "border": "#6D28D9"}},
    "file_write": {"background": "#FBCFE8", "border": "#DB2777",
                   "highlight": {"background": "#F9A8D4", "border": "#BE185D"}},
    "api_call":   {"background": "#FED7AA", "border": "#EA580C",
                   "highlight": {"background": "#FDBA74", "border": "#C2410C"}},
    "unknown":    {"background": "#E2E8F0", "border": "#64748B",
                   "highlight": {"background": "#CBD5E1", "border": "#475569"}},
}
_XFORM_COLOR = {
    "background": "#FEF3C7", "border": "#D97706",
    "highlight": {"background": "#FDE68A", "border": "#B45309"},
}
_DS_ICONS = {
    "dbt_source": "⬡", "dbt_model": "◆", "dbt_seed": "⊞",
    "table_ref": "◇", "file_read": "↓", "file_write": "↑", "unknown": "○",
}
_XFORM_ICONS = {
    "dbt_model": "⚙", "dbt_macro": "Λ",
    "python_pandas": "🐼", "python_spark": "⚡",
    "python_sql_exec": "⬢", "sql_query": "⬡",
}

_PYVIS_OPTIONS = """
var options = {
  "nodes": {
    "borderWidth": 3,
    "borderWidthSelected": 5,
    "shadow": {"enabled": true, "color": "rgba(15,23,42,0.16)", "size": 16, "x": 2, "y": 4},
    "font": {"color": "#10212B", "size": 18,
             "face": "Trebuchet MS, Segoe UI, sans-serif",
             "strokeWidth": 6, "strokeColor": "#F8FBFA",
             "bold": {"color": "#10212B", "size": 19}}
  },
  "edges": {
    "smooth": {"type": "dynamic", "roundness": 0.18},
    "shadow": {"enabled": true, "color": "rgba(15,23,42,0.12)", "size": 8},
    "width": 3, "selectionWidth": 5,
    "arrows": {"to": {"enabled": true, "scaleFactor": 1.35, "type": "arrow"}},
    "font": {"color": "#334155", "size": 14, "align": "middle",
             "strokeWidth": 4, "strokeColor": "#F8FBFA"}
  },
  "physics": {
    "barnesHut": {
      "gravitationalConstant": -10000, "centralGravity": 0.18,
      "springLength": 310, "springConstant": 0.025,
      "damping": 0.18, "avoidOverlap": 1.0
    },
    "minVelocity": 0.2,
    "stabilization": {"iterations": 350}
  },
  "interaction": {
    "hover": true, "tooltipDelay": 100, "navigationButtons": true,
    "keyboard": {"enabled": true, "bindToWindow": false},
    "multiselect": true, "zoomView": true, "dragView": true
  }
}
"""


def _ds_tooltip(ds) -> str:
    parts = [
        f"<div style='font-family:Trebuchet MS, Segoe UI, sans-serif;font-size:13px;padding:10px 13px;"
        f"background:#FFFFFF;border:1px solid #D7E3E0;border-radius:12px;max-width:340px;"
        f"box-shadow:0 18px 36px rgba(15,23,42,0.14);color:#10212B'>",
        f"<b style='color:#1D4ED8;font-size:15px'>{ds.name}</b><br>",
        f"<span style='color:#64748B'>Type:</span> "
        f"<b style='color:#10212B'>{ds.dataset_type}</b><br>",
    ]
    if ds.source_file:
        parts.append(
            f"<span style='color:#64748B'>Defined in:</span> "
            f"<code style='color:#2563EB'>{ds.source_file}</code><br>"
        )
    if ds.description:
        parts.append(f"<span style='color:#64748B'>Description:</span> {ds.description}<br>")
    if ds.columns:
        col_str = ", ".join(ds.columns[:8])
        if len(ds.columns) > 8:
            col_str += f" +{len(ds.columns) - 8} more"
        parts.append(
            f"<span style='color:#64748B'>Columns:</span> "
            f"<code style='color:#15803D'>{col_str}</code><br>"
        )
    conf_color = "#15803D" if ds.confidence >= 0.9 else "#C2410C"
    parts.append(
        f"<span style='color:#64748B'>Confidence:</span> "
        f"<b style='color:{conf_color}'>{ds.confidence:.0%}</b></div>"
    )
    return "".join(parts)


def _xform_tooltip(xform) -> str:
    fname = xform.source_file.split("/")[-1]
    label = fname.replace(".sql", "").replace(".py", "")
    parts = [
        f"<div style='font-family:Trebuchet MS, Segoe UI, sans-serif;font-size:13px;padding:10px 13px;"
        f"background:#FFFFFF;border:1px solid #D7E3E0;border-radius:12px;max-width:380px;"
        f"box-shadow:0 18px 36px rgba(15,23,42,0.14);color:#10212B'>",
        f"<b style='color:#C2410C;font-size:15px'>{label}</b><br>",
        f"<span style='color:#64748B'>Type:</span> "
        f"<b style='color:#10212B'>{xform.transformation_type}</b><br>",
        f"<span style='color:#64748B'>File:</span> "
        f"<code style='color:#2563EB'>{xform.source_file}</code><br>",
    ]
    if xform.source_datasets:
        parts.append(
            f"<span style='color:#64748B'>Reads:</span> "
            f"<code style='color:#BE185D'>{', '.join(xform.source_datasets)}</code><br>"
        )
    if xform.target_datasets:
        parts.append(
            f"<span style='color:#64748B'>Writes:</span> "
            f"<code style='color:#15803D'>{', '.join(xform.target_datasets)}</code><br>"
        )
    if xform.is_dynamic:
        parts.append(
            "<br><span style='color:#FFA502'>⚠ Dynamic SQL — lineage may be incomplete</span>"
        )
    conf_color = "#15803D" if xform.confidence >= 0.9 else "#C2410C"
    parts.append(
        f"<br><span style='color:#64748B'>Confidence:</span> "
        f"<b style='color:{conf_color}'>{xform.confidence:.0%}</b></div>"
    )
    return "".join(parts)


def export_lineage_viz(
    g: nx.DiGraph,
    datasets: dict,
    transformations: dict,
    output_path: Path,
) -> bool:
    """
    Export the lineage subgraph as an interactive dark-theme HTML (PyVis).

    Returns True on success, False when pyvis is not installed or saving fails.
    """
    if not datasets and not transformations:
        logger.warning("No lineage data — skipping PyVis visualization")
        return False

    try:
        from pyvis.network import Network  # type: ignore[import]
    except ImportError:
        logger.warning("pyvis not installed — skipping lineage visualization")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    net = Network(
        height="100vh", width="100%", directed=True,
        bgcolor="#F3F7F6", font_color="#10212B", notebook=False,
    )
    net.set_options(_PYVIS_OPTIONS)

    for ds in datasets.values():
        parts = ds.name.split(".")
        label = parts[-1] if len(parts) > 1 else ds.name
        icon = _DS_ICONS.get(ds.dataset_type, "○")
        net.add_node(
            ds.name, label=f"{icon} {label}", title=_ds_tooltip(ds),
            color=_DS_COLORS.get(ds.dataset_type, _DS_COLORS["unknown"]),
            shape="ellipse", size=42, mass=2,
            font={
                "color": "#10212B",
                "size": 18,
                "face": "Trebuchet MS, Segoe UI, sans-serif",
                "strokeWidth": 6,
                "strokeColor": "#F8FBFA",
                "bold": {"color": "#10212B", "size": 19},
            },
        )

    for xform in transformations.values():
        fname = xform.source_file.split("/")[-1]
        label = fname.replace(".sql", "").replace(".py", "")
        icon = _XFORM_ICONS.get(xform.transformation_type, "⚙")
        net.add_node(
            xform.id, label=f"{icon} {label}", title=_xform_tooltip(xform),
            color=_XFORM_COLOR, shape="box", size=30, mass=1,
            font={
                "color": "#10212B",
                "size": 17,
                "face": "Trebuchet MS, Segoe UI, sans-serif",
                "strokeWidth": 5,
                "strokeColor": "#F8FBFA",
                "bold": {"color": "#10212B", "size": 18},
            },
        )

    for u, v, data in g.edges(data=True):
        edge_type = data.get("edge_type", "")
        if edge_type == "PRODUCES":
            net.add_edge(
                u, v,
                color={"color": "#2ED573", "highlight": "#7BED9F", "opacity": 0.85},
                title="<b style='color:#2ED573'>PRODUCES</b>", label="→",
            )
        elif edge_type == "CONSUMES":
            net.add_edge(
                u, v,
                color={"color": "#FF4757", "highlight": "#FF6B81", "opacity": 0.75},
                title="<b style='color:#FF4757'>CONSUMES</b>", label="→",
                dashes=True,
            )

    try:
        html_content = net.generate_html()
        legend_html = _build_lineage_legend(datasets, transformations)
        html_content = html_content.replace("</body>", legend_html + "\n</body>")
        output_path.write_text(html_content, encoding="utf-8")
        logger.info("Saved lineage visualization (PyVis) → %s", output_path)
        return True
    except Exception as exc:
        logger.warning("PyVis save failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# HTML legend panel
# ---------------------------------------------------------------------------

_LEGEND_TYPE_META: dict[str, tuple[str, str, str]] = {
    "dbt_source": ("#FF4757", "⬡", "Source"),
    "dbt_model":  ("#00D2FF", "◆", "Model"),
    "dbt_seed":   ("#2ED573", "⊞", "Seed"),
    "table_ref":  ("#FFA502", "◇", "Table ref"),
    "file_read":  ("#A29BFE", "↓", "File read"),
    "file_write": ("#FD79A8", "↑", "File write"),
    "unknown":    ("#636E72", "○", "Unknown"),
}


def _build_lineage_legend(datasets: dict, transformations: dict) -> str:
    """Build an HTML legend panel injected into the PyVis output."""
    ds_types: dict[str, int] = {}
    for ds in datasets.values():
        ds_types[ds.dataset_type] = ds_types.get(ds.dataset_type, 0) + 1

    ds_rows = ""
    for dtype, count in sorted(ds_types.items(), key=lambda x: -x[1]):
        color, icon, label = _LEGEND_TYPE_META.get(dtype, ("#636E72", "○", dtype))
        ds_rows += (
            f"<div style='display:flex;align-items:center;gap:8px;margin:4px 0'>"
            f"<span style='width:14px;height:14px;border-radius:50%;"
            f"background:{color};display:inline-block;flex-shrink:0'></span>"
            f"<span style='color:#E6EDF3'>{icon} {label}</span>"
            f"<span style='color:#8B949E;margin-left:auto;font-size:11px'>{count}</span>"
            f"</div>"
        )

    ndynamic = sum(1 for t in transformations.values() if t.is_dynamic)
    xform_note = ""
    if ndynamic:
        xform_note = (
            f"<div style='margin-top:6px;padding:5px 8px;background:#161B22;"
            f"border-left:3px solid #FFA502;border-radius:3px;font-size:11px;color:#8B949E'>"
            f"⚠ {ndynamic} transformation(s) marked <b style='color:#FFA502'>dynamic</b> — "
            f"Jinja/variable SQL not fully resolved</div>"
        )

    stats_html = (
        f"<div style='margin-bottom:10px;padding:8px;background:#161B22;"
        f"border-radius:6px;font-size:12px'>"
        f"<span style='color:#8B949E'>Datasets</span> "
        f"<b style='color:#58A6FF'>{len(datasets)}</b> &nbsp;·&nbsp; "
        f"<span style='color:#8B949E'>Transforms</span> "
        f"<b style='color:#FDCB6E'>{len(transformations)}</b>"
        f"</div>"
    )

    return f"""
<style>
  body {{ margin: 0; overflow: hidden; }}
  #legend-panel {{
    position: fixed; top: 16px; right: 16px; z-index: 9999;
    background: #161B22ee; border: 1px solid #30363D; border-radius: 12px;
    padding: 16px 18px; min-width: 220px; max-width: 280px;
    font-family: JetBrains Mono, monospace, sans-serif; font-size: 13px;
    box-shadow: 0 8px 32px #00000066; backdrop-filter: blur(8px);
  }}
  #legend-panel h3 {{
    margin: 0 0 12px 0; color: #58A6FF; font-size: 14px; font-weight: 700;
    border-bottom: 1px solid #30363D; padding-bottom: 8px; letter-spacing: 0.5px;
  }}
  #legend-panel .section-title {{
    color: #8B949E; font-size: 11px; text-transform: uppercase;
    letter-spacing: 1px; margin: 10px 0 5px 0;
  }}
  #edge-legend {{ margin-top: 12px; border-top: 1px solid #30363D; padding-top: 10px; }}
  .edge-row {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
  .edge-line {{ height: 3px; width: 28px; border-radius: 2px; flex-shrink: 0; }}
  #toggle-btn {{
    position: fixed; top: 16px; right: 16px; z-index: 10000; display: none;
    background: #161B22; border: 1px solid #30363D; border-radius: 8px;
    color: #58A6FF; padding: 6px 12px; cursor: pointer; font-size: 13px;
  }}
</style>
<button id="toggle-btn"
  onclick="document.getElementById('legend-panel').style.display='block';this.style.display='none'">
  ⊞ Legend
</button>
<div id="legend-panel">
  <h3>🗺 Lineage Map</h3>
  {stats_html}
  <div class="section-title">Datasets</div>
  {ds_rows}
  <div class="section-title" style="margin-top:10px">Transformations</div>
  <div style="display:flex;align-items:center;gap:8px;margin:4px 0">
    <span style="width:14px;height:14px;border-radius:3px;background:#FDCB6E;
                 display:inline-block;flex-shrink:0"></span>
    <span style="color:#E6EDF3">⚙ SQL / Python transform</span>
    <span style="color:#8B949E;margin-left:auto;font-size:11px">{len(transformations)}</span>
  </div>
  {xform_note}
  <div id="edge-legend">
    <div class="section-title">Edges</div>
    <div class="edge-row">
      <div class="edge-line" style="background:#2ED573"></div>
      <span style="color:#E6EDF3">PRODUCES</span>
    </div>
    <div class="edge-row">
      <div class="edge-line" style="background:#FF4757;
           border-bottom:2px dashed #FF4757;height:2px"></div>
      <span style="color:#E6EDF3">CONSUMES</span>
    </div>
  </div>
  <div style="margin-top:12px;font-size:10px;color:#484F58;
              border-top:1px solid #21262D;padding-top:8px">
    Click node to select · Scroll to zoom · Drag to pan<br>
    <a href="#"
       onclick="document.getElementById('legend-panel').style.display='none';
                document.getElementById('toggle-btn').style.display='block';
                return false"
       style="color:#58A6FF">Hide legend</a>
  </div>
</div>"""
