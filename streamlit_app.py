"""
Streamlit dashboard for the Brownfield Cartographer.

Run with:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.dashboard.data_layer import (
    PROJECT_ROOT,
    build_lineage_focus_dot,
    build_module_focus_dot,
    build_overview_metrics,
    dataset_detail,
    dataset_records,
    discover_artifact_roots,
    domain_records,
    evidence_for_module,
    hub_records,
    load_code_snippet,
    load_dashboard_bundle,
    module_detail,
    module_records,
    reading_order_records,
    review_queue_records,
    run_navigator_query,
    velocity_records,
)
from src.models.nodes import DayOneCitation


st.set_page_config(
    page_title="Brownfield Cartographer",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink: #16202a;
            --muted: #5f6b76;
            --paper: #f8f3ea;
            --teal: #0f766e;
            --teal-soft: #d7f1ef;
            --amber: #c07d1f;
            --slate: #dbe4ee;
            --rose: #f7d9d3;
        }
        .stApp {
            background:
                radial-gradient(circle at top left, #fff8ef 0%, #f3efe6 38%, #e6eef3 100%);
            color: var(--ink);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #173042 0%, #244b5e 100%);
        }
        [data-testid="stSidebar"] * {
            color: #f7fbfc;
        }
        .hero-card {
            background: linear-gradient(135deg, rgba(15, 118, 110, 0.95), rgba(30, 64, 175, 0.86));
            color: #f7fbfc;
            border-radius: 24px;
            padding: 1.6rem 1.8rem;
            margin-bottom: 1.2rem;
            box-shadow: 0 18px 40px rgba(18, 32, 43, 0.16);
        }
        .hero-title {
            font-family: "Trebuchet MS", "Gill Sans", "Segoe UI", sans-serif;
            font-size: 2rem;
            font-weight: 700;
            margin: 0 0 0.35rem 0;
            letter-spacing: 0.02em;
        }
        .hero-copy {
            font-size: 1rem;
            line-height: 1.5;
            margin: 0;
            opacity: 0.96;
        }
        .section-copy {
            color: var(--muted);
            margin-top: -0.4rem;
            margin-bottom: 1rem;
        }
        [data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 18px;
            padding: 0.8rem 1rem;
            box-shadow: 0 8px 24px rgba(18, 32, 43, 0.08);
        }
        .evidence-card {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.8rem;
        }
        .evidence-meta {
            color: var(--muted);
            font-size: 0.9rem;
            margin-bottom: 0.2rem;
        }
        .report-shell {
            background: rgba(255, 255, 255, 0.76);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            border: 1px solid rgba(22, 32, 42, 0.08);
        }
        .small-note {
            color: var(--muted);
            font-size: 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner=False)
def _load_bundle(path_text: str):
    return load_dashboard_bundle(Path(path_text))


def _artifact_options() -> list[Path]:
    artifact_base = PROJECT_ROOT / ".cartography"
    if not artifact_base.exists():
        return []
    return discover_artifact_roots(artifact_base)


def _language_for_file(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    return {
        ".py": "python",
        ".sql": "sql",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".json": "json",
        ".md": "markdown",
        ".js": "javascript",
        ".ts": "typescript",
        ".ipynb": "json",
    }.get(suffix, "text")


def _bar_figure(
    labels: Iterable[str],
    values: Iterable[float],
    title: str,
    color: str = "#0f766e",
    orientation: str = "v",
    text_format: Optional[str] = None,
) -> go.Figure:
    labels_list = list(labels)
    values_list = list(values)
    fig = go.Figure()
    fig.add_bar(
        x=labels_list if orientation == "v" else values_list,
        y=values_list if orientation == "v" else labels_list,
        orientation=orientation,
        marker=dict(color=color, line=dict(color="rgba(22, 32, 42, 0.12)", width=1)),
        text=[format(value, text_format) if text_format else value for value in values_list],
        textposition="auto",
        hovertemplate="%{y}: %{x}<extra></extra>" if orientation == "h" else "%{x}: %{y}<extra></extra>",
    )
    fig.update_layout(
        title=title,
        paper_bgcolor="rgba(255,255,255,0.0)",
        plot_bgcolor="rgba(255,255,255,0.0)",
        margin=dict(l=10, r=10, t=48, b=10),
        font=dict(color="#16202a", family="Trebuchet MS, Segoe UI, sans-serif"),
        showlegend=False,
    )
    return fig


def _donut_figure(labels: Iterable[str], values: Iterable[float], title: str) -> go.Figure:
    fig = go.Figure(
        data=[
            go.Pie(
                labels=list(labels),
                values=list(values),
                hole=0.56,
                marker=dict(
                    colors=["#0f766e", "#c07d1f", "#1d4ed8", "#ef4444", "#7c3aed", "#64748b"]
                ),
            )
        ]
    )
    fig.update_layout(
        title=title,
        paper_bgcolor="rgba(255,255,255,0.0)",
        margin=dict(l=10, r=10, t=48, b=10),
        font=dict(color="#16202a", family="Trebuchet MS, Segoe UI, sans-serif"),
    )
    return fig


def _gauge_figure(value: float, title: str) -> go.Figure:
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=max(0.0, min(1.0, value)) * 100,
            number={"suffix": "%"},
            title={"text": title},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#0f766e"},
                "steps": [
                    {"range": [0, 45], "color": "#f5d0c5"},
                    {"range": [45, 75], "color": "#f4e3b2"},
                    {"range": [75, 100], "color": "#d2f0dc"},
                ],
            },
        )
    )
    fig.update_layout(
        paper_bgcolor="rgba(255,255,255,0.0)",
        margin=dict(l=16, r=16, t=56, b=10),
        font=dict(color="#16202a", family="Trebuchet MS, Segoe UI, sans-serif"),
    )
    return fig


def _normalize_citation(item: Any) -> DayOneCitation:
    if isinstance(item, DayOneCitation):
        return item
    return DayOneCitation.model_validate(item)


def _render_citations(bundle, citations: list[Any], key_prefix: str) -> None:
    normalized = [_normalize_citation(item) for item in citations]
    if not normalized:
        st.info("No grounded citations were available for this view.")
        return
    for index, citation in enumerate(normalized):
        span = "unknown lines"
        if citation.line_start is not None and citation.line_end is not None:
            span = (
                f"lines {citation.line_start}"
                if citation.line_start == citation.line_end
                else f"lines {citation.line_start}-{citation.line_end}"
            )
        elif citation.line_start is not None:
            span = f"line {citation.line_start}"
        with st.expander(
            f"{citation.file_path} | {span} | {citation.source_phase}/{citation.evidence_type}",
            expanded=index == 0,
        ):
            st.markdown(
                (
                    f"<div class='evidence-card'><div class='evidence-meta'>"
                    f"{citation.extraction_method}</div><div>{citation.description}</div></div>"
                ),
                unsafe_allow_html=True,
            )
            snippet = load_code_snippet(
                bundle,
                citation.file_path,
                citation.line_start,
                citation.line_end,
            )
            st.code(snippet.text, language=_language_for_file(citation.file_path))
            if snippet.resolved_path:
                st.caption(str(snippet.resolved_path))


def _render_module_viewer(bundle, module_path: str, title: str = "Evidence Viewer") -> None:
    detail = module_detail(bundle, module_path)
    if detail is None:
        st.warning("Select a module to inspect evidence.")
        return

    module = detail["module"]
    st.subheader(title)
    st.markdown(
        (
            f"<div class='evidence-card'><strong>{module.path}</strong><br>"
            f"{module.purpose_statement or 'No semantic purpose statement was available.'}</div>"
        ),
        unsafe_allow_html=True,
    )

    metric_cols = st.columns(5)
    metric_cols[0].metric("Role", module.role)
    metric_cols[1].metric("Business Logic", f"{module.business_logic_score:.2f}")
    metric_cols[2].metric("Hotspot", f"{module.hotspot_fusion_score:.2f}")
    metric_cols[3].metric("Drift", module.doc_drift_level or "no_drift")
    metric_cols[4].metric("Confidence", f"{module.semantic_confidence:.2f}")

    left, right = st.columns([1.1, 0.9])
    with left:
        st.markdown("**Imports and Dependents**")
        st.write(
            {
                "imports": [item["file_path"] for item in detail["imports_out"][:10]],
                "dependents": [item["file_path"] for item in detail["imports_in"][:10]],
            }
        )
        st.markdown("**Lineage Touchpoints**")
        st.write(detail["lineage"])
    with right:
        st.markdown("**Semantic Provenance**")
        st.write(
            {
                "semantic_model_used": module.semantic_model_used,
                "semantic_prompt_version": module.semantic_prompt_version,
                "semantic_generation_timestamp": str(module.semantic_generation_timestamp)
                if module.semantic_generation_timestamp
                else "",
                "semantic_fallback_used": module.semantic_fallback_used,
            }
        )
        if detail["review_queue_item"]:
            st.markdown("**Review Queue Status**")
            st.write(detail["review_queue_item"])

    st.markdown("**Grounded Evidence**")
    _render_citations(bundle, evidence_for_module(bundle, module_path), f"module-{module_path}")


def _render_overview(bundle, selected_module: str) -> None:
    metrics = build_overview_metrics(bundle)
    st.markdown(
        """
        <div class="hero-card">
          <div class="hero-title">Brownfield Cartographer Dashboard</div>
          <p class="hero-copy">
            A visual, evidence-first map of repository structure, data flow, semantics, and live
            query answers built entirely from the saved <code>.cartography</code> artifacts.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    metric_cols = st.columns(6)
    metric_cols[0].metric("Total Files", metrics["total_files"])
    metric_cols[1].metric("Datasets", metrics["datasets"])
    metric_cols[2].metric("Transformations", metrics["transformations"])
    metric_cols[3].metric("Semantic Domains", metrics["semantic_domains"])
    metric_cols[4].metric("Hotspots", metrics["hotspots"])
    metric_cols[5].metric("Drift Flags", metrics["documentation_drift"])

    st.markdown(
        "<p class='section-copy'>The overview combines signals from all four completed phases so a non-technical reader can see what matters first.</p>",
        unsafe_allow_html=True,
    )

    module_rows = module_records(bundle)
    dataset_rows = dataset_records(bundle)
    domain_rows = domain_records(bundle)
    hotspot_rows = bundle.semantic_hotspots[:8]

    role_counts: dict[str, int] = {}
    for row in module_rows:
        role_counts[row["role"]] = role_counts.get(row["role"], 0) + 1

    dataset_counts: dict[str, int] = {}
    for row in dataset_rows:
        dataset_counts[row["dataset_type"]] = dataset_counts.get(row["dataset_type"], 0) + 1

    drift_counts = metrics["drift_counts"]

    top_row = st.columns(2)
    with top_row[0]:
        st.plotly_chart(
            _bar_figure(role_counts.keys(), role_counts.values(), "Repository Structure by Role"),
            use_container_width=True,
            config={"displaylogo": False},
        )
    with top_row[1]:
        st.plotly_chart(
            _donut_figure(dataset_counts.keys(), dataset_counts.values(), "Data Assets by Dataset Type"),
            use_container_width=True,
            config={"displaylogo": False},
        )

    bottom_row = st.columns(2)
    with bottom_row[0]:
        st.plotly_chart(
            _bar_figure(
                [row["domain"] for row in domain_rows],
                [row["business_logic_total"] for row in domain_rows],
                "Semantic Domains by Business Logic Weight",
                color="#1d4ed8",
            ),
            use_container_width=True,
            config={"displaylogo": False},
        )
    with bottom_row[1]:
        st.plotly_chart(
            _donut_figure(
                drift_counts.keys(),
                drift_counts.values(),
                "Documentation Drift Summary",
            ),
            use_container_width=True,
            config={"displaylogo": False},
        )

    st.plotly_chart(
        _bar_figure(
            [item.get("file_path", "") for item in hotspot_rows],
            [float(item.get("hotspot_fusion_score", 0.0) or 0.0) for item in hotspot_rows],
            "Hotspot Leaderboard",
            color="#c07d1f",
            orientation="h",
            text_format=".2f",
        ),
        use_container_width=True,
        config={"displaylogo": False},
    )

    overview_tabs = st.tabs(["CODEBASE", "Onboarding Brief", "Evidence Viewer"])
    with overview_tabs[0]:
        st.markdown(bundle.codebase_markdown or "_CODEBASE.md is not available yet._")
    with overview_tabs[1]:
        st.markdown(bundle.onboarding_brief_markdown or "_Onboarding brief is not available yet._")
    with overview_tabs[2]:
        _render_module_viewer(bundle, selected_module, "Module Evidence Viewer")


def _render_phase1(bundle, selected_module: str) -> None:
    stats = bundle.surveyor_stats
    st.title("Phase 1 - Structure")
    st.markdown(
        "<p class='section-copy'>Surveyor maps the repository structure, import graph, hub modules, and git-driven architectural churn.</p>",
        unsafe_allow_html=True,
    )

    metric_cols = st.columns(5)
    metric_cols[0].metric("Project Type", stats.get("project_type", "unknown"))
    metric_cols[1].metric("Files Parsed", stats.get("files_parsed_ok", 0))
    metric_cols[2].metric("Import Edges", stats.get("import_edges", 0))
    metric_cols[3].metric("dbt Refs", stats.get("dbt_ref_edges", 0))
    metric_cols[4].metric("Cycle Clusters", stats.get("circular_dependency_clusters", 0))

    st.markdown("**Interactive module graph**")
    st.caption(
        "Drag, zoom, hover, and click inside the network to inspect local topology. Use the module explorer below for grounded evidence and provenance."
    )
    if bundle.module_graph_html:
        components.html(bundle.module_graph_html, height=720, scrolling=True)
    else:
        st.info("module_graph.html is not available in the selected artifact set.")

    chart_cols = st.columns(2)
    hubs = hub_records(bundle)
    with chart_cols[0]:
        st.plotly_chart(
            _bar_figure(
                [item["file_path"] for item in hubs],
                [item["pagerank"] for item in hubs],
                "Hub Modules by PageRank",
                color="#0f766e",
                orientation="h",
                text_format=".4f",
            ),
            use_container_width=True,
            config={"displaylogo": False},
        )
    with chart_cols[1]:
        velocities = velocity_records(bundle)
        st.plotly_chart(
            _bar_figure(
                [item["file_path"] for item in velocities],
                [item["velocity"] for item in velocities],
                "Git Velocity",
                color="#c07d1f",
                orientation="h",
            ),
            use_container_width=True,
            config={"displaylogo": False},
        )

    st.subheader("Dependency Explorer")
    st.graphviz_chart(build_module_focus_dot(bundle, selected_module), use_container_width=True)
    _render_module_viewer(bundle, selected_module, "Selected Module")


def _render_phase2(bundle, selected_dataset: str) -> None:
    stats = bundle.hydrologist_stats
    st.title("Phase 2 - Data Flow")
    st.markdown(
        "<p class='section-copy'>Hydrologist turns SQL, YAML, Python, and dbt semantics into a lineage graph of datasets and transformations.</p>",
        unsafe_allow_html=True,
    )

    metric_cols = st.columns(6)
    metric_cols[0].metric("Sources", stats.get("sources_registered", 0))
    metric_cols[1].metric("Seeds", stats.get("seeds_found", 0))
    metric_cols[2].metric("SQL Models", stats.get("sql_files_analyzed", 0))
    metric_cols[3].metric("Datasets", stats.get("datasets_total", 0))
    metric_cols[4].metric("Transformations", stats.get("transformations_total", 0))
    metric_cols[5].metric("Dynamic", stats.get("dynamic_transformations", 0))

    st.markdown("**Interactive lineage map**")
    if bundle.lineage_graph_html:
        components.html(bundle.lineage_graph_html, height=720, scrolling=True)
    else:
        st.info("lineage_graph.html is not available in the selected artifact set.")

    detail = dataset_detail(bundle, selected_dataset)
    st.subheader("Dataset Explorer")
    st.graphviz_chart(build_lineage_focus_dot(bundle, selected_dataset), use_container_width=True)

    if detail is None:
        st.warning("Select a dataset from the sidebar to inspect its lineage.")
        return

    dataset = detail["dataset"]
    info_cols = st.columns(4)
    info_cols[0].metric("Dataset Type", dataset.dataset_type)
    info_cols[1].metric("Confidence", f"{dataset.confidence:.2f}")
    info_cols[2].metric("Producers", len(detail["producers"]))
    info_cols[3].metric("Consumers", len(detail["consumers"]))

    left, right = st.columns(2)
    with left:
        st.markdown("**Upstream**")
        st.write(detail["upstream"][:12] or ["No upstream nodes"])
        st.markdown("**Producers**")
        for transformation in detail["producers"]:
            st.write(
                {
                    "id": transformation.id,
                    "source_file": transformation.source_file,
                    "line_range": transformation.line_range,
                    "reads": transformation.source_datasets,
                    "writes": transformation.target_datasets,
                }
            )
    with right:
        st.markdown("**Downstream**")
        st.write(detail["downstream"][:12] or ["No downstream nodes"])
        st.markdown("**Consumers**")
        for transformation in detail["consumers"]:
            st.write(
                {
                    "id": transformation.id,
                    "source_file": transformation.source_file,
                    "line_range": transformation.line_range,
                    "reads": transformation.source_datasets,
                    "writes": transformation.target_datasets,
                }
            )

    producer_file = next(
        (transformation.source_file for transformation in detail["producers"] if transformation.source_file),
        dataset.source_file,
    )
    producer_range = next(
        (transformation.line_range for transformation in detail["producers"] if transformation.line_range),
        (None, None),
    )
    if producer_file:
        st.markdown("**Representative SQL or transformation source**")
        snippet = load_code_snippet(bundle, producer_file, producer_range[0], producer_range[1])
        st.code(snippet.text, language=_language_for_file(producer_file))

    with st.expander("Phase 2 blind spots and risk signals"):
        st.write(bundle.blind_spots)
        st.write(bundle.high_risk_areas)


def _render_phase3(bundle, selected_module: str) -> None:
    stats = bundle.semanticist_stats
    st.title("Phase 3 - Semantic Insights")
    st.markdown(
        "<p class='section-copy'>Semanticist adds purpose statements, domain clusters, business-logic concentration, hotspot scoring, and documentation-drift signals.</p>",
        unsafe_allow_html=True,
    )

    metric_cols = st.columns(5)
    metric_cols[0].metric("Purpose Statements", stats.get("purpose_statements_generated", 0))
    metric_cols[1].metric("Domains", stats.get("domains_found", 0))
    metric_cols[2].metric("Hotspots", stats.get("semantic_hotspots", 0))
    metric_cols[3].metric("Review Queue", stats.get("review_queue_items", 0))
    metric_cols[4].metric("Missing Docs", stats.get("documentation_missing_count", 0))

    chart_cols = st.columns(2)
    with chart_cols[0]:
        top_hotspots = bundle.semantic_hotspots[:10]
        st.plotly_chart(
            _bar_figure(
                [item.get("file_path", "") for item in top_hotspots],
                [float(item.get("hotspot_fusion_score", 0.0) or 0.0) for item in top_hotspots],
                "Hotspot Ranking",
                color="#0f766e",
                orientation="h",
                text_format=".2f",
            ),
            use_container_width=True,
            config={"displaylogo": False},
        )
    with chart_cols[1]:
        domains = domain_records(bundle)
        st.plotly_chart(
            _bar_figure(
                [item["domain"] for item in domains],
                [item["modules"] for item in domains],
                "Domain Cluster Size",
                color="#1d4ed8",
            ),
            use_container_width=True,
            config={"displaylogo": False},
        )

    drift_labels = ["no_drift", "possible_drift", "likely_drift"]
    drift_values = [
        build_overview_metrics(bundle)["drift_counts"].get(label, 0) for label in drift_labels
    ]
    st.plotly_chart(
        _donut_figure(drift_labels, drift_values, "Documentation Drift"),
        use_container_width=True,
        config={"displaylogo": False},
    )

    tabs = st.tabs(["Hotspots", "Review Queue", "Reading Order", "Module Evidence"])
    with tabs[0]:
        st.dataframe(bundle.semantic_hotspots[:20], use_container_width=True, hide_index=True)
    with tabs[1]:
        st.dataframe(review_queue_records(bundle), use_container_width=True, hide_index=True)
    with tabs[2]:
        st.dataframe(reading_order_records(bundle, limit=25), use_container_width=True, hide_index=True)
    with tabs[3]:
        _render_module_viewer(bundle, selected_module, "Semantic Evidence Viewer")


def _render_phase4(bundle) -> None:
    st.title("Phase 4 - Query Navigator")
    st.markdown(
        "<p class='section-copy'>Navigator answers codebase questions from saved graph and semantic artifacts. It does not rescan the repository when you ask a question.</p>",
        unsafe_allow_html=True,
    )

    if "phase4_question" not in st.session_state:
        st.session_state["phase4_question"] = "What does this repository do?"

    st.text_area(
        "Ask a question about the codebase",
        key="phase4_question",
        height=120,
        placeholder="Examples: What are the main data pipelines? What breaks if source.ecom.raw_orders changes?",
    )

    sample_cols = st.columns(4)
    samples = [
        "What does this repository do?",
        "What are the main data pipelines?",
        "Which modules contain the most business logic?",
        "What breaks if source.ecom.raw_orders changes?",
    ]
    for index, sample in enumerate(samples):
        if sample_cols[index].button(sample, use_container_width=True):
            st.session_state["phase4_question"] = sample
            st.rerun()

    if st.button("Run Navigator", type="primary", use_container_width=True):
        with st.spinner("Grounding the answer from saved artifacts..."):
            result = run_navigator_query(bundle.artifact_root, st.session_state["phase4_question"])
        st.session_state["phase4_result"] = result
        st.cache_resource.clear()
        st.rerun()

    result = st.session_state.get("phase4_result")
    if result:
        answer_col, gauge_col = st.columns([1.6, 0.8])
        with answer_col:
            st.markdown(
                (
                    f"<div class='evidence-card'><strong>{result['question']}</strong><br><br>"
                    f"{result['answer']}</div>"
                ),
                unsafe_allow_html=True,
            )
            st.caption(
                f"Query type: {result['query_type']} | Models used: {result['models_used']} | Log: {result['log_path'] or 'not written'}"
            )
        with gauge_col:
            st.plotly_chart(
                _gauge_figure(result["confidence"], "Answer Confidence"),
                use_container_width=True,
                config={"displaylogo": False},
            )

        st.markdown("**Supporting citations**")
        _render_citations(bundle, result["citations"], "phase4-result")

    st.subheader("Recent Query Logs")
    if bundle.query_logs:
        st.dataframe(bundle.query_logs[:12], use_container_width=True, hide_index=True)
    else:
        st.info("No query logs were found for this artifact set yet.")


def _render_reports(bundle) -> None:
    st.title("Reports")
    st.markdown(
        "<p class='section-copy'>This page brings together the written phase reports plus the generated living-context artifacts used by the dashboard and Navigator.</p>",
        unsafe_allow_html=True,
    )

    tab_labels = []
    tab_content = []
    for key in sorted(bundle.reports):
        tab_labels.append(key.upper())
        tab_content.append(bundle.reports[key])
    tab_labels.extend(["CODEBASE", "ONBOARDING"])
    tab_content.extend([bundle.codebase_markdown, bundle.onboarding_brief_markdown])

    tabs = st.tabs(tab_labels)
    for tab, content in zip(tabs, tab_content, strict=False):
        with tab:
            st.markdown("<div class='report-shell'>", unsafe_allow_html=True)
            st.markdown(content or "_This report is not available in the current workspace._")
            st.markdown("</div>", unsafe_allow_html=True)


def main() -> None:
    _apply_theme()

    artifact_roots = _artifact_options()
    if not artifact_roots:
        st.error("No .cartography artifact roots were found. Run `cartographer analyze` first.")
        return

    st.sidebar.title("Cartographer")
    st.sidebar.caption("Artifact-driven repository intelligence")
    selected_root = st.sidebar.selectbox(
        "Artifact set",
        artifact_roots,
        format_func=lambda path: path.name,
    )
    bundle = _load_bundle(str(selected_root))

    module_options = [
        item.get("file_path", "")
        for item in bundle.semantic_hotspots
        if item.get("file_path")
    ] or [module.path for module in bundle.module_graph.all_modules()]
    default_module = module_options[0] if module_options else ""
    selected_module = st.sidebar.selectbox(
        "Evidence Viewer Module",
        module_options,
        index=module_options.index(default_module) if default_module in module_options else 0,
    )

    datasets = sorted(record["dataset"] for record in dataset_records(bundle))
    selected_dataset = st.sidebar.selectbox(
        "Dataset Explorer",
        datasets,
        index=0 if datasets else None,
    ) if datasets else ""

    page = st.sidebar.radio(
        "Navigate",
        [
            "Repository Overview",
            "Phase 1 - Structure",
            "Phase 2 - Data Flow",
            "Phase 3 - Semantic Insights",
            "Phase 4 - Query Navigator",
            "Reports",
        ],
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        (
            f"<div class='small-note'>Artifact root<br><strong>{bundle.artifact_root}</strong></div>"
            f"<div class='small-note' style='margin-top:0.9rem;'>Saved queries<br><strong>{len(bundle.query_logs)}</strong></div>"
        ),
        unsafe_allow_html=True,
    )

    if page == "Repository Overview":
        _render_overview(bundle, selected_module)
    elif page == "Phase 1 - Structure":
        _render_phase1(bundle, selected_module)
    elif page == "Phase 2 - Data Flow":
        _render_phase2(bundle, selected_dataset)
    elif page == "Phase 3 - Semantic Insights":
        _render_phase3(bundle, selected_module)
    elif page == "Phase 4 - Query Navigator":
        _render_phase4(bundle)
    else:
        _render_reports(bundle)


if __name__ == "__main__":
    main()
