"""
Streamlit dashboard for the Brownfield Cartographer.

Run with:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

from html import escape
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
    coerce_day_one_citation,
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
        html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
            background:
                radial-gradient(circle at top left, #fff8ef 0%, #f3efe6 38%, #e6eef3 100%) !important;
            color: var(--ink) !important;
        }
        [data-testid="stHeader"] {
            background: rgba(248, 243, 234, 0.55) !important;
            backdrop-filter: blur(10px);
        }
        [data-testid="stAppViewBlockContainer"] {
            padding-top: 2rem;
        }
        [data-testid="stAppViewContainer"] *,
        [data-testid="stAppViewBlockContainer"] *,
        [data-testid="stMarkdownContainer"] *,
        [data-testid="stMetricValue"],
        [data-testid="stMetricLabel"],
        [data-testid="stMetricDelta"],
        [data-testid="stExpander"] summary,
        [data-testid="stCaptionContainer"],
        [data-testid="stTable"] *,
        [data-testid="stDataFrame"] *,
        .stTabs [role="tab"],
        .stSelectbox label,
        .stTextArea label,
        .stTextInput label,
        .stRadio label,
        .stButton button,
        h1, h2, h3, h4, h5, h6, p, span, label, code {
            color: var(--ink) !important;
        }
        [data-testid="stSidebar"] * {
            color: #f7fbfc !important;
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #173042 0%, #244b5e 100%);
        }
        [data-testid="stSidebar"] [data-baseweb="select"] > div,
        [data-testid="stSidebar"] .stRadio > div,
        [data-testid="stSidebar"] .stMarkdown {
            background: rgba(255, 255, 255, 0.06) !important;
            border-radius: 14px;
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
            margin-top: -0.25rem;
            margin-bottom: 1rem;
            font-size: 1rem;
            line-height: 1.55;
        }
        [data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 18px;
            padding: 0.8rem 1rem;
            box-shadow: 0 8px 24px rgba(18, 32, 43, 0.08);
        }
        [data-testid="stMetric"] label,
        [data-testid="stMetric"] div {
            color: var(--ink) !important;
        }
        .stTextArea textarea,
        .stTextInput input,
        [data-baseweb="select"] > div,
        [data-testid="stExpander"],
        [data-testid="stDataFrame"],
        [data-testid="stTable"] {
            background: rgba(255, 255, 255, 0.82) !important;
            border: 1px solid rgba(22, 32, 42, 0.10) !important;
            border-radius: 16px !important;
        }
        .stTabs [role="tab"] {
            background: transparent !important;
            border: 1px solid transparent !important;
            border-radius: 16px !important;
            box-shadow: none !important;
        }
        .stTabs [role="tab"][aria-selected="true"] {
            background: rgba(15, 118, 110, 0.14) !important;
            color: #0f766e !important;
            border-color: rgba(15, 118, 110, 0.28) !important;
        }
        .stButton button {
            border-radius: 999px !important;
            border: 1px solid rgba(22, 32, 42, 0.10) !important;
            background: rgba(255, 255, 255, 0.88) !important;
        }
        .stButton button[kind="primary"] {
            background: linear-gradient(135deg, #0f766e, #1d4ed8) !important;
            color: #f7fbfc !important;
            border: none !important;
        }
        .stCodeBlock, [data-testid="stCodeBlock"] {
            border-radius: 18px !important;
            overflow: hidden;
        }
        .story-card {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 22px;
            padding: 1.2rem 1.25rem;
            box-shadow: 0 12px 32px rgba(18, 32, 43, 0.08);
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .story-title {
            font-size: 1.02rem;
            font-weight: 700;
            margin-bottom: 0.4rem;
        }
        .story-copy {
            color: var(--muted);
            line-height: 1.55;
            margin: 0;
        }
        .review-card {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(192, 125, 31, 0.18);
            border-radius: 22px;
            padding: 1.1rem 1.15rem;
            box-shadow: 0 12px 32px rgba(18, 32, 43, 0.08);
            min-height: 180px;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .review-title {
            font-size: 1rem;
            font-weight: 700;
            margin: 0 0 0.45rem 0;
        }
        .review-copy {
            color: var(--muted);
            line-height: 1.55;
            margin: 0 0 0.8rem 0;
        }
        .review-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
        }
        .review-badge {
            display: inline-flex;
            align-items: center;
            padding: 0.3rem 0.62rem;
            border-radius: 999px;
            font-size: 0.83rem;
            line-height: 1.1;
            background: rgba(192, 125, 31, 0.12);
            border: 1px solid rgba(192, 125, 31, 0.18);
            color: var(--ink) !important;
        }
        .plain-note {
            background: rgba(255, 255, 255, 0.76);
            border-left: 4px solid #0f766e;
            border-radius: 18px;
            padding: 0.95rem 1rem;
            margin: 0.4rem 0 1.1rem 0;
            color: var(--ink) !important;
            line-height: 1.55;
        }
        .term-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.65rem;
            margin: 0.5rem 0 1rem 0;
        }
        .term-chip {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.42rem 0.78rem;
            border-radius: 999px;
            background: rgba(15, 118, 110, 0.10);
            border: 1px solid rgba(15, 118, 110, 0.20);
            color: var(--ink) !important;
            font-size: 0.92rem;
            line-height: 1;
            text-decoration: none;
            cursor: help;
        }
        .term-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 1.05rem;
            height: 1.05rem;
            border-radius: 999px;
            background: rgba(15, 118, 110, 0.14);
            color: #0f766e !important;
            font-size: 0.76rem;
            font-weight: 700;
        }
        .graph-note {
            color: var(--muted);
            margin-top: -0.2rem;
            margin-bottom: 0.8rem;
            font-size: 0.95rem;
        }
        .evidence-card {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.8rem;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .detail-card {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 20px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.8rem;
            box-shadow: 0 10px 26px rgba(18, 32, 43, 0.07);
            overflow-wrap: anywhere;
            word-break: break-word;
            height: 100%;
        }
        .qa-card {
            background: rgba(255, 255, 255, 0.84);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 22px;
            padding: 1.1rem 1.2rem;
            margin-bottom: 1rem;
            box-shadow: 0 12px 30px rgba(18, 32, 43, 0.08);
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .qa-header {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: flex-start;
            margin-bottom: 0.55rem;
        }
        .qa-question {
            font-size: 1rem;
            font-weight: 700;
            line-height: 1.4;
            margin: 0;
        }
        .qa-confidence {
            white-space: nowrap;
            padding: 0.34rem 0.7rem;
            border-radius: 999px;
            background: rgba(15, 118, 110, 0.10);
            border: 1px solid rgba(15, 118, 110, 0.18);
            font-size: 0.84rem;
            font-weight: 700;
        }
        .qa-answer {
            color: var(--muted);
            line-height: 1.6;
            margin: 0.15rem 0 0.9rem 0;
        }
        .qa-evidence-title {
            font-size: 0.88rem;
            font-weight: 700;
            margin-bottom: 0.35rem;
        }
        .qa-evidence-list {
            display: flex;
            flex-direction: column;
            gap: 0.45rem;
        }
        .qa-evidence-item {
            padding: 0.6rem 0.75rem;
            border-radius: 14px;
            background: rgba(15, 118, 110, 0.06);
            border: 1px solid rgba(15, 118, 110, 0.12);
            font-size: 0.9rem;
            color: var(--ink) !important;
        }
        .qa-evidence-meta {
            color: var(--muted) !important;
            font-size: 0.82rem;
            margin-top: 0.15rem;
        }
        .qa-muted {
            color: var(--muted);
            font-size: 0.92rem;
            margin: 0;
        }
        .pill-card {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 20px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.8rem;
            box-shadow: 0 10px 26px rgba(18, 32, 43, 0.07);
        }
        .pill-title {
            font-size: 0.98rem;
            font-weight: 700;
            margin: 0 0 0.65rem 0;
        }
        .pill-copy {
            color: var(--muted);
            line-height: 1.55;
            margin: 0;
        }
        .pill-list {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
        }
        .pill-item {
            display: inline-flex;
            align-items: center;
            padding: 0.34rem 0.62rem;
            border-radius: 999px;
            background: rgba(29, 78, 216, 0.08);
            border: 1px solid rgba(29, 78, 216, 0.14);
            font-size: 0.86rem;
            line-height: 1.2;
            color: var(--ink) !important;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .transform-card {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(22, 32, 42, 0.08);
            border-radius: 20px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.8rem;
            box-shadow: 0 10px 26px rgba(18, 32, 43, 0.07);
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .transform-path {
            font-size: 0.96rem;
            font-weight: 700;
            margin-bottom: 0.3rem;
        }
        .transform-meta {
            color: var(--muted);
            font-size: 0.84rem;
            margin-bottom: 0.7rem;
        }
        .detail-title {
            font-size: 0.98rem;
            font-weight: 700;
            margin: 0 0 0.55rem 0;
        }
        .detail-list {
            margin: 0;
            padding-left: 1rem;
            color: var(--muted);
            line-height: 1.55;
        }
        .detail-list li {
            margin-bottom: 0.35rem;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .summary-path {
            font-size: 1rem;
            font-weight: 700;
            margin-bottom: 0.45rem;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .summary-copy {
            color: var(--muted);
            line-height: 1.6;
            margin: 0;
        }
        .kv-grid {
            display: grid;
            grid-template-columns: minmax(130px, 180px) 1fr;
            gap: 0.5rem 0.8rem;
            align-items: start;
        }
        .kv-key {
            font-weight: 700;
        }
        .kv-value {
            color: var(--muted);
            overflow-wrap: anywhere;
            word-break: break-word;
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
    bundle = load_dashboard_bundle(Path(path_text))
    if not hasattr(bundle, "fde_day_one_answers"):
        setattr(bundle, "fde_day_one_answers", {})
    return bundle


TERM_GLOSSARY = {
    "module": "A single file in the repository, such as a SQL model, Python script, YAML file, or macro.",
    "dataset": "A named piece of data the system reads from or writes to, such as a source table, seed, or analytics model.",
    "lineage": "The path data takes as it moves through the system from raw inputs to downstream outputs.",
    "transformation": "A step that changes data, such as a SQL model or Python process that reads one dataset and writes another.",
    "hotspot": "A file that looks especially important because it combines architectural importance, business logic, and downstream impact.",
    "business logic": "The rules that turn raw technical data into meaningful business outcomes, metrics, or decisions.",
    "documentation drift": "A sign that the written docs no longer match what the code or SQL is actually doing.",
    "hub": "A highly connected file that many other parts of the repository depend on.",
    "git velocity": "How frequently a file has changed recently in version control.",
    "query navigator": "The question-answering layer that responds from saved artifacts instead of rescanning the repository.",
}


def _term_chip(term: str | tuple[str, str]) -> str:
    if isinstance(term, tuple):
        label, key = term
    else:
        label, key = term, term.lower()
    explanation = escape(TERM_GLOSSARY.get(key, "Plain-English explanation unavailable."))
    return (
        f"<span class='term-chip' title='{explanation}'>"
        f"{escape(label)} <span class='term-icon'>ℹ️</span></span>"
    )


def _render_term_row(terms: Iterable[str | tuple[str, str]]) -> None:
    chips = "".join(_term_chip(term) for term in terms)
    st.markdown(f"<div class='term-row'>{chips}</div>", unsafe_allow_html=True)


def _render_story_card(title: str, body: str) -> None:
    st.markdown(
        (
            f"<div class='story-card'><div class='story-title'>{escape(title)}</div>"
            f"<p class='story-copy'>{escape(body)}</p></div>"
        ),
        unsafe_allow_html=True,
    )


def _render_plain_note(text: str) -> None:
    st.markdown(f"<div class='plain-note'>{text}</div>", unsafe_allow_html=True)


def _render_detail_list_card(title: str, items: list[str], empty_text: str) -> None:
    if items:
        entries = "".join(f"<li>{escape(item)}</li>" for item in items)
        body = f"<ul class='detail-list'>{entries}</ul>"
    else:
        body = f"<p class='story-copy'>{escape(empty_text)}</p>"
    st.markdown(
        f"<div class='detail-card'><div class='detail-title'>{escape(title)}</div>{body}</div>",
        unsafe_allow_html=True,
    )


def _render_kv_card(title: str, rows: list[tuple[str, str]]) -> None:
    body = "".join(
        (
            f"<div class='kv-key'>{escape(label)}</div>"
            f"<div class='kv-value'>{escape(value)}</div>"
        )
        for label, value in rows
    )
    st.markdown(
        (
            f"<div class='detail-card'><div class='detail-title'>{escape(title)}</div>"
            f"<div class='kv-grid'>{body}</div></div>"
        ),
        unsafe_allow_html=True,
    )


def _render_pill_card(title: str, items: list[str], empty_text: str, limit: int = 12) -> None:
    shown_items = items[:limit]
    if shown_items:
        pills = "".join(f"<span class='pill-item'>{escape(item)}</span>" for item in shown_items)
        more = (
            f"<p class='pill-copy' style='margin-top:0.7rem;'>+{len(items) - limit} more related nodes</p>"
            if len(items) > limit
            else ""
        )
        body = f"<div class='pill-list'>{pills}</div>{more}"
    else:
        body = f"<p class='pill-copy'>{escape(empty_text)}</p>"
    st.markdown(
        f"<div class='pill-card'><div class='pill-title'>{escape(title)}</div>{body}</div>",
        unsafe_allow_html=True,
    )


def _render_transformation_cards(title: str, transformations: list[Any], empty_text: str) -> None:
    st.markdown(f"### {title}")
    if not transformations:
        st.markdown(
            f"<div class='pill-card'><p class='pill-copy'>{escape(empty_text)}</p></div>",
            unsafe_allow_html=True,
        )
        return
    for transformation in transformations:
        reads = ", ".join(transformation.source_datasets) or "None"
        writes = ", ".join(transformation.target_datasets) or "None"
        meta = f"Confidence {transformation.confidence:.2f}"
        if transformation.line_range:
            meta += f" • {_line_range_text(transformation.line_range)}"
        st.markdown(
            (
                "<div class='transform-card'>"
                f"<div class='transform-path'>{escape(transformation.source_file or transformation.id)}</div>"
                f"<div class='transform-meta'>{escape(meta)}</div>"
                f"<div class='kv-grid'>"
                f"<div class='kv-key'>Reads</div><div class='kv-value'>{escape(reads)}</div>"
                f"<div class='kv-key'>Writes</div><div class='kv-value'>{escape(writes)}</div>"
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )


def _line_range_text(line_range: Any) -> str:
    if not line_range or len(line_range) != 2:
        return "Unknown"
    start, end = line_range
    if start is None and end is None:
        return "Unknown"
    if start == end:
        return f"Line {start}"
    if start is None:
        return f"Ends at line {end}"
    if end is None:
        return f"Starts at line {start}"
    return f"Lines {start}-{end}"


def _friendly_review_reason(reason: str) -> str:
    normalized = reason.strip().lower()
    if normalized == "missing documentation":
        return "Documentation needs to be added."
    if normalized.startswith("documentation drift"):
        return "Existing documentation likely no longer matches the file."
    if normalized == "low-confidence semantic output":
        return "The file meaning still needs a quick human check."
    if normalized == "high hotspot score but weak evidence":
        return "This file looks important, but the current evidence is still thin."
    if normalized == "unresolved lineage case":
        return "Part of the data flow around this file is still unresolved."
    return reason[:1].upper() + reason[1:] if reason else "Needs review."


def _render_review_preview(items: list[dict[str, Any]]) -> None:
    preview = items[:3]
    columns = st.columns(len(preview)) if preview else []
    for column, item in zip(columns, preview, strict=False):
        reasons = item.get("reasons", [])
        summary = _friendly_review_reason(reasons[0]) if reasons else "Needs review."
        badges = "".join(
            f"<span class='review-badge'>{escape(_friendly_review_reason(reason).rstrip('.'))}</span>"
            for reason in reasons[:3]
        )
        with column:
            st.markdown(
                (
                    "<div class='review-card'>"
                    f"<div class='review-title'>{escape(item.get('file_path', 'unknown'))}</div>"
                    f"<p class='review-copy'>{escape(summary)}</p>"
                    f"<div class='review-badges'>{badges}</div>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )


def _render_lineage_health(bundle) -> None:
    blind_spots = bundle.blind_spots or {}
    blind_summary = blind_spots.get("summary", {})
    risk_areas = bundle.high_risk_areas or {}
    risk_summary = risk_areas.get("summary", {})

    st.markdown("### Lineage health")
    summary_cols = st.columns(3)
    summary_cols[0].metric("Total blind spots", blind_summary.get("total_blind_spots", 0))
    summary_cols[1].metric("Top hubs", risk_summary.get("top_hubs", 0))
    summary_cols[2].metric("High-velocity files", risk_summary.get("high_velocity_files", 0))

    blind_cols = st.columns(2)
    with blind_cols[0]:
        _render_kv_card(
            "Blind spot summary",
            [
                ("Parse failures", str(blind_summary.get("parse_failures", 0))),
                ("Missing grammars", str(blind_summary.get("grammar_missing", 0))),
                ("Structurally empty files", str(blind_summary.get("structurally_empty_files", 0))),
                ("Dynamic transformations", str(blind_summary.get("dynamic_transformations", 0))),
            ],
        )
    with blind_cols[1]:
        _render_kv_card(
            "Confidence summary",
            [
                ("Low-confidence datasets", str(blind_summary.get("low_confidence_datasets", 0))),
                ("Low-confidence edges", str(blind_summary.get("low_confidence_edges", 0))),
                ("Velocity window", f"{risk_areas.get('velocity_window_days', 0)} days"),
                ("Parse warnings", str(risk_summary.get("files_with_parse_warnings", 0))),
            ],
        )

    if blind_summary.get("total_blind_spots", 0) == 0:
        st.markdown(
            "<div class='pill-card'><p class='pill-copy'>No blind spots were detected in this saved lineage run.</p></div>",
            unsafe_allow_html=True,
        )
    else:
        issue_sections = [
            ("Parse failures", blind_spots.get("parse_failures", [])),
            ("Missing grammars", blind_spots.get("grammar_missing", [])),
            ("Structurally empty files", blind_spots.get("structurally_empty_files", [])),
            ("Dynamic transformations", blind_spots.get("dynamic_transformations", [])),
            ("Low-confidence datasets", blind_spots.get("low_confidence_datasets", [])),
            ("Low-confidence edges", blind_spots.get("low_confidence_edges", [])),
        ]
        for title, items in issue_sections:
            if items:
                st.markdown(f"### {title}")
                st.dataframe(items, use_container_width=True, hide_index=True)

    st.markdown("### Structural risk signals")
    top_hubs = risk_areas.get("top_hubs", [])
    if top_hubs:
        st.dataframe(
            [
                {
                    "Module": item.get("node", ""),
                    "Role": item.get("role", "unknown"),
                    "PageRank": item.get("pagerank_score", 0.0),
                    "In degree": item.get("in_degree", 0),
                    "Out degree": item.get("out_degree", 0),
                    "In cycle": item.get("in_cycle", False),
                }
                for item in top_hubs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.markdown(
            "<div class='pill-card'><p class='pill-copy'>No structural risk hotspots were detected in this saved run.</p></div>",
            unsafe_allow_html=True,
        )

    secondary_sections = [
        ("High-velocity files", risk_areas.get("high_velocity_files", [])),
        ("Circular dependencies", risk_areas.get("circular_dependencies", [])),
        ("High-fanout transformations", risk_areas.get("high_fanout_transformations", [])),
        ("Dynamic hotspots", risk_areas.get("dynamic_hotspots", [])),
    ]
    for title, items in secondary_sections:
        if items:
            st.markdown(f"### {title}")
            st.dataframe(items, use_container_width=True, hide_index=True)


def _render_page_intro(
    title: str,
    description: str,
    terms: Iterable[str | tuple[str, str]],
    plain_english: Optional[str] = None,
) -> None:
    st.title(title)
    st.markdown(f"<p class='section-copy'>{description}</p>", unsafe_allow_html=True)
    _render_term_row(terms)
    if plain_english:
        _render_plain_note(plain_english)


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


def _render_citations(bundle, citations: list[Any], key_prefix: str) -> None:
    normalized = [coerce_day_one_citation(item) for item in citations]
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


def _render_day_one_question_set(bundle, payload: dict[str, Any], key_prefix: str) -> None:
    questions = payload.get("questions", []) if isinstance(payload, dict) else []
    if not questions:
        st.info("No saved answers were available for this view yet.")
        return
    for index, item in enumerate(questions):
        confidence = float(item.get("confidence", 0.0) or 0.0)
        normalized_citations = [coerce_day_one_citation(citation) for citation in item.get("citations", [])]
        evidence_preview = normalized_citations[:3]
        preview_html = "".join(
            (
                "<div class='qa-evidence-item'>"
                f"{escape(citation.file_path)}"
                f"<div class='qa-evidence-meta'>"
                f"{escape(f'lines {citation.line_start}-{citation.line_end}' if citation.line_start is not None and citation.line_end is not None and citation.line_start != citation.line_end else f'line {citation.line_start}' if citation.line_start is not None else 'line range unavailable')} • "
                f"{escape(citation.source_phase)}/{escape(citation.evidence_type)}"
                "</div></div>"
            )
            for citation in evidence_preview
        )
        evidence_block = (
            "<div class='qa-evidence-title'>Key evidence</div>"
            f"<div class='qa-evidence-list'>{preview_html}</div>"
            if evidence_preview
            else "<p class='qa-muted'>No grounded citations were available for this answer.</p>"
        )
        st.markdown(
            (
                "<div class='qa-card'>"
                "<div class='qa-header'>"
                f"<div class='qa-question'>{escape(item.get('question', 'Question'))}</div>"
                f"<div class='qa-confidence'>Confidence {confidence:.2f}</div>"
                "</div>"
                f"<p class='qa-answer'>{escape(str(item.get('answer', '')))}</p>"
                f"{evidence_block}"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        if normalized_citations:
            with st.expander("View full grounded evidence", expanded=False):
                _render_citations(bundle, normalized_citations, f"{key_prefix}-{index}")


def _render_module_viewer(bundle, module_path: str, title: str = "Evidence Viewer") -> None:
    detail = module_detail(bundle, module_path)
    if detail is None:
        st.warning("Select a module to inspect evidence.")
        return

    module = detail["module"]
    st.subheader(title)
    st.markdown(
        (
            "<div class='evidence-card'>"
            f"<div class='summary-path'>{escape(module.path)}</div>"
            f"<p class='summary-copy'>{escape(module.purpose_statement or 'No semantic purpose statement was available.')}</p>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    metric_cols = st.columns(5)
    metric_cols[0].metric("Role", module.role)
    metric_cols[1].metric("Business Logic", f"{module.business_logic_score:.2f}")
    metric_cols[2].metric("Hotspot", f"{module.hotspot_fusion_score:.2f}")
    metric_cols[3].metric("Drift", module.doc_drift_level or "no_drift")
    metric_cols[4].metric("Confidence", f"{module.semantic_confidence:.2f}")

    st.markdown("### How this file fits into the repository")
    connection_cols = st.columns(2)
    with connection_cols[0]:
        _render_detail_list_card(
            "Depends on these files",
            [item["file_path"] for item in detail["imports_out"][:10]],
            "No import dependencies were recorded for this file.",
        )
        _render_detail_list_card(
            "Used by these files",
            [item["file_path"] for item in detail["imports_in"][:10]],
            "No downstream file dependencies were recorded for this file.",
        )
    with connection_cols[1]:
        _render_detail_list_card(
            "Reads these datasets",
            detail["lineage"]["consumed_datasets"][:10],
            "No upstream datasets were recorded for this file.",
        )
        _render_detail_list_card(
            "Produces these datasets",
            detail["lineage"]["produced_datasets"][:10],
            "No output datasets were recorded for this file.",
        )

    transformations = detail["lineage"]["transformations"]
    if transformations:
        st.markdown("### Data flow steps")
        st.dataframe(
            [
                {
                    "Transformation": item.get("type", "unknown"),
                    "Reads": ", ".join(item.get("source_datasets", [])) or "None",
                    "Writes": ", ".join(item.get("target_datasets", [])) or "None",
                    "Confidence": f"{float(item.get('confidence', 0.0) or 0.0):.2f}",
                }
                for item in transformations
            ],
            use_container_width=True,
            hide_index=True,
        )

    detail_cols = st.columns([1.05, 0.95])
    with detail_cols[1]:
        if detail["review_queue_item"]:
            _render_kv_card(
                "Review status",
                [
                    ("Status", "Needs a quick human check"),
                    (
                        "Why",
                        "; ".join(
                            _friendly_review_reason(reason)
                            for reason in detail["review_queue_item"].get("reasons", [])
                        )
                        or "No review reason recorded.",
                    ),
                ],
            )

    st.markdown("**Grounded Evidence**")
    _render_citations(bundle, evidence_for_module(bundle, module_path), f"module-{module_path}")


def _module_options(bundle) -> list[str]:
    return [
        item.get("file_path", "")
        for item in bundle.semantic_hotspots
        if item.get("file_path")
    ] or [module.path for module in bundle.module_graph.all_modules()]


def _dataset_options(bundle) -> list[str]:
    return sorted(record["dataset"] for record in dataset_records(bundle))


def _hotspot_table_records(bundle, limit: int = 20) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in bundle.semantic_hotspots[:limit]:
        records.append(
            {
                "file_path": item.get("file_path", ""),
                "hotspot_fusion_score": float(item.get("hotspot_fusion_score", 0.0) or 0.0),
                "purpose": item.get("purpose", ""),
            }
        )
    return records


def _render_overview(bundle) -> None:
    metrics = build_overview_metrics(bundle)
    st.markdown(
        """
        <div class="hero-card">
          <div class="hero-title">Brownfield Cartographer Dashboard</div>
          <p class="hero-copy">
            A cleaner, evidence-first view of repository structure, data flow, semantic meaning,
            and grounded questions built from the saved <code>.cartography</code> artifacts.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _render_term_row(
        [
            "module",
            "dataset",
            "lineage",
            "transformation",
            "hotspot",
            "documentation drift",
        ]
    )

    top_metrics = st.columns(3)
    top_metrics[0].metric("Total Files", metrics["total_files"])
    top_metrics[1].metric("Datasets", metrics["datasets"])
    top_metrics[2].metric("Transformations", metrics["transformations"])

    bottom_metrics = st.columns(3)
    bottom_metrics[0].metric("Semantic Domains", metrics["semantic_domains"])
    bottom_metrics[1].metric("Hotspots", metrics["hotspots"])
    bottom_metrics[2].metric("Drift Flags", metrics["documentation_drift"])

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

    st.markdown("### What to look at first")
    start_cols = st.columns(3)
    for column, item in zip(start_cols, hotspot_rows[:3], strict=False):
        with column:
            _render_story_card(
                item.get("file_path", "unknown"),
                item.get("purpose", "No purpose summary available."),
            )

    if bundle.semantic_review_queue:
        st.markdown("### Needs a quick human check")
        st.markdown(
            "<p class='graph-note'>These files are worth a short review because the saved evidence shows missing docs, likely drift, or lower-confidence interpretation.</p>",
            unsafe_allow_html=True,
        )
        _render_review_preview(bundle.semantic_review_queue)

    st.markdown("### Hotspot leaderboard")
    st.markdown(
        "<p class='graph-note'>These files are the strongest onboarding starting points because they combine graph importance, business logic, and downstream impact.</p>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        _bar_figure(
            [item.get("file_path", "") for item in hotspot_rows],
            [float(item.get("hotspot_fusion_score", 0.0) or 0.0) for item in hotspot_rows],
            "Most important files for onboarding",
            color="#c07d1f",
            orientation="h",
            text_format=".2f",
        ),
        use_container_width=True,
        config={"displaylogo": False},
    )

    overview_tabs = st.tabs(["Onboarding brief", "FDE Day-One", "Evidence viewer"])
    with overview_tabs[0]:
        st.markdown(bundle.onboarding_brief_markdown or "_Onboarding brief is not available yet._")
    with overview_tabs[1]:
        _render_day_one_question_set(bundle, getattr(bundle, "fde_day_one_answers", {}), "fde-day-one")
    with overview_tabs[2]:
        module_options = _module_options(bundle)
        selected_module = (
            st.selectbox(
                "Choose a module to inspect",
                module_options,
                key="overview-module",
            )
            if module_options
            else ""
        )
        _render_module_viewer(bundle, selected_module, "Selected module")


def _render_phase1(bundle) -> None:
    stats = bundle.surveyor_stats
    _render_page_intro(
        "Structural",
        "Surveyor maps how files relate to one another, which ones act as central hubs, and where change activity is concentrated.",
        [("Module", "module"), ("Hub", "hub"), ("Git velocity", "git velocity")],
    )

    metric_cols = st.columns(5)
    metric_cols[0].metric("Project Type", stats.get("project_type", "unknown"))
    metric_cols[1].metric("Files Parsed", stats.get("files_parsed_ok", 0))
    metric_cols[2].metric("Import Edges", stats.get("import_edges", 0))
    metric_cols[3].metric("dbt Refs", stats.get("dbt_ref_edges", 0))
    metric_cols[4].metric("Cycle Clusters", stats.get("circular_dependency_clusters", 0))

    st.markdown("### Full repository structure map")
    st.markdown(
        "<p class='graph-note'>This is the main structure view. Zoom and pan directly in the graph, then use the tabs below for summaries and the selected-module deep dive.</p>",
        unsafe_allow_html=True,
    )
    if bundle.module_graph_html:
        components.html(bundle.module_graph_html, height=1320, scrolling=True)
    else:
        st.info("module_graph.html is not available in the selected artifact set.")

    module_options = _module_options(bundle)
    selected_module = (
        st.selectbox(
            "Choose a module to inspect",
            module_options,
            key="phase1-module",
        )
        if module_options
        else ""
    )

    phase_tabs = st.tabs(["Key structural insights", "Selected module", "Focused dependency view"])
    with phase_tabs[0]:
        chart_cols = st.columns(2)
        hubs = hub_records(bundle)
        with chart_cols[0]:
            st.plotly_chart(
                _bar_figure(
                    [item["file_path"] for item in hubs],
                    [item["pagerank"] for item in hubs],
                    "Most central modules",
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
                    "Most frequently changed files",
                    color="#c07d1f",
                    orientation="h",
                ),
                use_container_width=True,
                config={"displaylogo": False},
            )
    with phase_tabs[1]:
        _render_module_viewer(bundle, selected_module, "Selected module")
    with phase_tabs[2]:
        st.markdown(
            "<p class='graph-note'>This optional focused view shows only the selected module and its immediate neighbors, which keeps the main page from repeating the same picture twice.</p>",
            unsafe_allow_html=True,
        )
        st.graphviz_chart(build_module_focus_dot(bundle, selected_module), use_container_width=True)


def _render_phase2(bundle) -> None:
    stats = bundle.hydrologist_stats
    _render_page_intro(
        "Lineage",
        "Hydrologist maps how data moves from raw inputs to downstream outputs by linking datasets and the transformations that read or produce them.",
        [("Dataset", "dataset"), ("Lineage", "lineage"), ("Transformation", "transformation")],
    )

    metric_cols = st.columns(6)
    metric_cols[0].metric("Sources", stats.get("sources_registered", 0))
    metric_cols[1].metric("Seeds", stats.get("seeds_found", 0))
    metric_cols[2].metric("SQL Models", stats.get("sql_files_analyzed", 0))
    metric_cols[3].metric("Datasets", stats.get("datasets_total", 0))
    metric_cols[4].metric("Transformations", stats.get("transformations_total", 0))
    metric_cols[5].metric("Dynamic", stats.get("dynamic_transformations", 0))

    st.markdown("### Full lineage map")
    st.markdown(
        "<p class='graph-note'>This is the main lineage view and it now uses more vertical space so upstream and downstream relationships are easier to read.</p>",
        unsafe_allow_html=True,
    )
    if bundle.lineage_graph_html:
        components.html(bundle.lineage_graph_html, height=1380, scrolling=True)
    else:
        st.info("lineage_graph.html is not available in the selected artifact set.")

    datasets = _dataset_options(bundle)
    selected_dataset = (
        st.selectbox(
            "Choose a dataset to inspect",
            datasets,
            key="phase2-dataset",
        )
        if datasets
        else ""
    )

    detail = dataset_detail(bundle, selected_dataset)
    phase_tabs = st.tabs(["Selected dataset", "Focused lineage view", "Blind spots and risks"])
    with phase_tabs[0]:
        if detail is None:
            st.warning("Select a dataset from the sidebar to inspect its lineage.")
        else:
            dataset = detail["dataset"]
            producer_file = next(
                (
                    transformation.source_file
                    for transformation in detail["producers"]
                    if transformation.source_file
                ),
                dataset.source_file,
            )
            summary_sentence = (
                f"`{dataset.name}` is a `{dataset.dataset_type}` dataset"
                f" produced by `{producer_file}` and consumed by {len(detail['consumers'])} downstream transformation(s)."
                if producer_file
                else f"`{dataset.name}` is a `{dataset.dataset_type}` dataset with {len(detail['producers'])} producing transformation(s) and {len(detail['consumers'])} consuming transformation(s)."
            )
            st.markdown(
                (
                    "<div class='evidence-card'>"
                    f"<div class='summary-path'>{escape(dataset.name)}</div>"
                    f"<p class='summary-copy'>{escape(summary_sentence)}</p>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
            _render_kv_card(
                "Dataset snapshot",
                [
                    ("Dataset type", dataset.dataset_type),
                    ("Confidence", f"{dataset.confidence:.2f}"),
                    ("Producing transformations", str(len(detail["producers"]))),
                    ("Consuming transformations", str(len(detail["consumers"]))),
                ],
            )

            relation_cols = st.columns(2)
            with relation_cols[0]:
                _render_pill_card(
                    "Upstream inputs",
                    detail["upstream"],
                    "No upstream datasets or transformations were detected.",
                )
            with relation_cols[1]:
                _render_pill_card(
                    "Downstream impact",
                    detail["downstream"],
                    "No downstream nodes were detected for this dataset.",
                )

            transform_cols = st.columns(2)
            with transform_cols[0]:
                _render_transformation_cards(
                    "Producing transformations",
                    detail["producers"],
                    "No producing transformations were recorded for this dataset.",
                )
            with transform_cols[1]:
                _render_transformation_cards(
                    "Consuming transformations",
                    detail["consumers"],
                    "No consuming transformations were recorded for this dataset.",
                )
    with phase_tabs[1]:
        if detail is None:
            st.warning("Select a dataset from the sidebar to inspect its lineage.")
        else:
            st.markdown(
                "<p class='graph-note'>This simplified view focuses only on the selected dataset and the transformations directly around it.</p>",
                unsafe_allow_html=True,
            )
            st.graphviz_chart(build_lineage_focus_dot(bundle, selected_dataset), use_container_width=True)
    with phase_tabs[2]:
        _render_lineage_health(bundle)


def _render_phase3(bundle) -> None:
    _render_page_intro(
        "Semantic Insights",
        "Semanticist explains what important files are for, groups them by business domain, and highlights where documentation or confidence is weak.",
        [("Business logic", "business logic"), "hotspot", ("Documentation drift", "documentation drift")],
    )

    insight_cols = st.columns(3)
    insight_cols[0].metric("Likely drift", build_overview_metrics(bundle)["drift_counts"].get("likely_drift", 0))
    insight_cols[1].metric("Top hotspot score", f"{float(bundle.semantic_hotspots[0].get('hotspot_fusion_score', 0.0)):.2f}" if bundle.semantic_hotspots else "0.00")
    insight_cols[2].metric("Domains with business logic", len([item for item in domain_records(bundle) if item["business_logic_total"] > 0]))

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

    module_options = _module_options(bundle)
    selected_module = (
        st.selectbox(
            "Choose a module to inspect",
            module_options,
            key="phase3-module",
        )
        if module_options
        else ""
    )

    tabs = st.tabs(["Hotspots", "Review Queue", "Reading Order", "Module Evidence"])
    with tabs[0]:
        st.dataframe(_hotspot_table_records(bundle, limit=20), use_container_width=True, hide_index=True)
    with tabs[1]:
        st.dataframe(review_queue_records(bundle), use_container_width=True, hide_index=True)
    with tabs[2]:
        st.dataframe(reading_order_records(bundle, limit=25), use_container_width=True, hide_index=True)
    with tabs[3]:
        _render_module_viewer(bundle, selected_module, "Semantic Evidence Viewer")


def _render_phase4(bundle) -> None:
    _render_page_intro(
        "Query Navigator",
        "Navigator answers repository questions from the saved graph and semantic artifacts. It explains the answer, shows a confidence score, and cites the supporting evidence.",
        [("Query Navigator", "query navigator"), "dataset", "lineage", "hotspot"],
    )

    if "phase4_question" not in st.session_state:
        st.session_state["phase4_question"] = "What does this repository do?"
    pending_question = st.session_state.pop("phase4_pending_question", None)
    if pending_question is not None:
        st.session_state["phase4_question"] = pending_question

    top_cols = st.columns([1.4, 0.9])
    with top_cols[0]:
        st.text_area(
            "Ask a question about the codebase",
            key="phase4_question",
            height=120,
            placeholder="Examples: What are the main data pipelines? What breaks if source.ecom.raw_orders changes?",
        )

        sample_cols = st.columns(2)
        samples = [
            "What does this repository do?",
            "What are the main data pipelines?",
            "Which modules contain the most business logic?",
            "What breaks if source.ecom.raw_orders changes?",
        ]
        for index, sample in enumerate(samples):
            if sample_cols[index % 2].button(sample, use_container_width=True, key=f"sample-{index}"):
                st.session_state["phase4_pending_question"] = sample
                st.rerun()

        if st.button("Run Navigator", type="primary", use_container_width=True):
            with st.spinner("Grounding the answer from saved artifacts..."):
                result = run_navigator_query(bundle.artifact_root, st.session_state["phase4_question"])
            st.session_state["phase4_result"] = result
            st.cache_resource.clear()
            st.rerun()

    result = st.session_state.get("phase4_result")
    if result:
        answer_tabs = st.tabs(["Answer", "Evidence", "Recent queries"])
        with answer_tabs[0]:
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
                    _gauge_figure(result["confidence"], "Answer confidence"),
                    use_container_width=True,
                    config={"displaylogo": False},
                )
        with answer_tabs[1]:
            _render_citations(bundle, result["citations"], "phase4-result")
        with answer_tabs[2]:
            if bundle.query_logs:
                st.dataframe(bundle.query_logs[:12], use_container_width=True, hide_index=True)
            else:
                st.info("No query logs were found for this artifact set yet.")
    else:
        st.info("Run a query to see an answer, confidence score, and grounded evidence here.")


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

    page = st.sidebar.radio(
        "Navigate",
        [
            "Repository Overview",
            "Structural",
            "Lineage",
            "Semantic Insights",
            "Query Navigator",
        ],
    )

    if page == "Repository Overview":
        _render_overview(bundle)
    elif page == "Structural":
        _render_phase1(bundle)
    elif page == "Lineage":
        _render_phase2(bundle)
    elif page == "Semantic Insights":
        _render_phase3(bundle)
    elif page == "Query Navigator":
        _render_phase4(bundle)


if __name__ == "__main__":
    main()
