# Phase 2 — Hydrologist: Data Lineage Analysis

## Overview

The **Hydrologist** is the second agent in the Brownfield Cartographer pipeline. It runs
automatically after Phase 1 and builds a **data lineage graph** that maps every dataset
(table, view, seed, source, file) and every transformation (SQL model, Python script, dbt
macro) along with the `PRODUCES` and `CONSUMES` edges that connect them.

The result is a queryable lineage graph and an interactive HTML visualisation that shows
exactly how data flows through the codebase — from raw seeds to final mart models.

---

## What the Hydrologist does

| Step | Action |
|------|--------|
| 1 | Parse dbt YAML config files to discover declared sources, seeds, and model metadata |
| 2 | Parse every SQL file with `sqlglot` (static) and fall back to regex for Jinja-heavy files |
| 3 | Extract `{{ ref() }}` and `{{ source() }}` dbt expressions as first-class lineage edges |
| 4 | Detect Python dataflow: `pandas.read_csv`, `spark.read`, `df.to_sql`, etc. |
| 5 | Build `PRODUCES` (transform → dataset) and `CONSUMES` (dataset → transform) edges |
| 6 | Attach confidence scores and evidence dicts to every edge |
| 7 | **Enrich**: classify each dataset with source/sink/final/intermediate flags |
| 8 | Write all output artifacts to `.cartography/data_lineage/` |
| 9 | Generate blind-spots and high-risk summary reports to `.cartography/` |

---

## Output Artifacts

| File | Location | Description |
|------|----------|-------------|
| `lineage_graph.json` | `.cartography/data_lineage/` | Custom JSON: datasets, transformations, PRODUCES/CONSUMES edges with confidence + evidence |
| `lineage_graph.html` | `.cartography/data_lineage/` | Interactive PyVis graph (dark theme, hover tooltips, physics layout) |
| `hydrologist_stats.json` | `.cartography/data_lineage/` | Summary: dataset counts by type, transformation counts, edge stats |
| `blind_spots.md` | `.cartography/` | Human-readable report of unresolved references and low-confidence signals |
| `unresolved_references.json` | `.cartography/` | Machine-readable blind-spots data (parse failures, dynamic transforms, low-confidence edges) |
| `high_risk_areas.md` | `.cartography/` | Aggregated risk summary: hubs, cycles, high-velocity files, parse warnings, dynamic hotspots |

---

## Dataset Classification

After building the lineage graph, the Hydrologist enriches every `DatasetNode` with
structural role flags derived from graph topology and naming conventions.

### Structural flags

| Field | `True` when… |
|-------|-------------|
| `is_source_dataset` | Dataset has **no PRODUCES edges pointing into it** — it is a root input (seed, external source, raw table) |
| `is_sink_dataset` | Dataset has **no CONSUMES edges coming out of it** — it is a terminal output (final mart model, export target) |
| `is_final_model` | Dataset name starts with `fct_` or `dim_`, or lives inside a `marts/` path |
| `is_intermediate_model` | Dataset name starts with `stg_` or `int_` |

> A dataset can be both `is_source_dataset=True` and `is_sink_dataset=True` if it is the
> only node in an isolated sub-graph (no transformation connects to it).
> Seeds in jaffle-shop have this property because they are raw inputs with no upstream
> transform — the dbt seed loader is external to the graph.

These fields are serialised to `lineage_graph.json` and are queryable via the NetworkX
graph object in Python.

---

## Edge Evidence

Every `PRODUCES` and `CONSUMES` edge carries an `evidence` dictionary:

```json
{
  "source_file": "models/staging/stg_orders.sql",
  "extraction_method": "sqlglot",
  "transformation_type": "dbt_model",
  "sql_preview": "with source as ( select * from {{ source('jaffle_shop', 'orders') }}"
}
```

For dbt `{{ ref() }}` expressions the evidence also includes `ref_name`:

```json
{
  "source_file": "models/marts/core/fct_orders.sql",
  "expression": "{{ ref('stg_orders') }}",
  "extraction_method": "dbt_jinja_regex",
  "ref_name": "stg_orders"
}
```

For edges extracted from Python dataflow:

```json
{
  "source_file": "pipelines/load_customers.py",
  "extraction_method": "regex",
  "transformation_type": "python_read",
  "sql_preview": "pd.read_csv('data/customers.csv')"
}
```

### Confidence scoring by extraction method

| Extraction method | Confidence | Notes |
|-------------------|-----------|-------|
| `tree_sitter_ast` | 1.00 | Full AST parse |
| `dbt_jinja_regex` | 1.00 | `{{ ref() }}` — deterministic in dbt projects |
| `config_parsing` | 0.95 | YAML source/seed declarations |
| `sqlglot` | 0.90 | Static SQL parse — reliable for standard SQL dialects |
| `regex` | 0.65 | Simple regex — misses dynamic patterns |
| `sqlglot_dynamic` | 0.55 | SQL with unresolved Jinja — partial parse |
| `inferred` | 0.40 | Shape-based — least reliable |

---

## Lineage Graph JSON Schema

`lineage_graph.json` top-level structure:

```json
{
  "datasets": {
    "stg_customers": {
      "name": "stg_customers",
      "dataset_type": "dbt_model",
      "source_file": "models/staging/stg_customers.sql",
      "confidence": 1.0,
      "is_source_dataset": false,
      "is_sink_dataset": false,
      "is_final_model": false,
      "is_intermediate_model": true
    }
  },
  "transformations": {
    "transform::stg_customers::models/staging/stg_customers.sql": {
      "name": "stg_customers",
      "transformation_type": "dbt_model",
      "source_file": "models/staging/stg_customers.sql",
      "confidence": 1.0
    }
  },
  "edges": [
    {
      "source": "transform::stg_customers::...",
      "target": "stg_customers",
      "edge_type": "PRODUCES",
      "confidence": 0.9,
      "evidence": { "extraction_method": "sqlglot", "..." : "..." }
    }
  ]
}
```

---

## Blind Spots Report (`blind_spots.json`)

A fully metric-based JSON replacing the previous `.md` files. Every section has a count in
`summary` plus an itemised `detail` array.

### Schema

```json
{
  "generated_at": "2026-03-12T08:58:48.032116Z",
  "summary": {
    "parse_failures": 0,
    "grammar_missing": 0,
    "structurally_empty_files": 2,
    "dynamic_transformations": 2,
    "low_confidence_datasets": 2,
    "low_confidence_edges": 2,
    "total_blind_spots": 8
  },
  "parse_failures": [],
  "grammar_missing": [],
  "structurally_empty_files": [
    { "file": "macros/cents_to_dollars.sql", "language": "sql", "lines": 16 }
  ],
  "dynamic_transformations": [
    {
      "id": "sql:macros/cents_to_dollars.sql",
      "source_file": "macros/cents_to_dollars.sql",
      "transformation_type": "dbt_model",
      "confidence": 0.45,
      "note": "Contains dynamic Jinja/SQL that could not be fully resolved"
    }
  ],
  "low_confidence_datasets": [
    { "name": "model.cents_to_dollars", "dataset_type": "dbt_model", "confidence": 0.45,
      "is_source": false, "is_sink": true }
  ],
  "low_confidence_edges": [
    {
      "from": "sql:macros/cents_to_dollars.sql",
      "to": "model.cents_to_dollars",
      "edge_type": "PRODUCES",
      "confidence": 0.45,
      "evidence": { "extraction_method": "sqlglot_dynamic", "sql_preview": "{# macro..." }
    }
  ]
}
```

### jaffle-shop actual output

| Metric | Value | Meaning |
|--------|-------|---------|
| `parse_failures` | 0 | All 33 files parsed successfully |
| `grammar_missing` | 0 | tree-sitter-sql installed, zero fallbacks |
| `structurally_empty_files` | 2 | `macros/cents_to_dollars.sql`, `macros/generate_schema_name.sql` — pure Jinja, no SQL table refs |
| `dynamic_transformations` | 2 | Same 2 macro files — `sqlglot_dynamic` extraction, confidence 0.40–0.45 |
| `low_confidence_datasets` | 2 | `model.cents_to_dollars`, `model.generate_schema_name` — derived from macro files |
| `low_confidence_edges` | 2 | PRODUCES edges from the 2 macros |
| `total_blind_spots` | 8 | Sum of all non-zero categories |

> **Interpretation**: the only blind spots in jaffle-shop are the two utility macros. This is
> expected — macros are Jinja template functions, not SQL models, and contain no table
> references. The lineage graph for all 13 actual SQL models is complete and
> high-confidence (1.0).

---

## High-Risk Areas Report (`high_risk_areas.json`)

Metric-based JSON replacing the previous `.md`. All six risk dimensions are machine-queryable.

### Schema

```json
{
  "generated_at": "2026-03-12T08:58:48.033459Z",
  "velocity_window_days": 30,
  "summary": {
    "high_velocity_files": 0,
    "top_hubs": 5,
    "circular_dependency_clusters": 0,
    "files_with_parse_warnings": 0,
    "high_fanout_transformations": 0,
    "dynamic_hotspot_transformations": 2
  },
  "high_velocity_files": [],
  "top_hubs": [
    {
      "node": "models/staging/stg_products.sql",
      "pagerank_score": 0.056218,
      "role": "staging",
      "is_hub": true,
      "in_cycle": false,
      "in_degree": 2,
      "out_degree": 0
    }
  ],
  "circular_dependencies": [],
  "parse_warnings": [],
  "high_fanout_transformations": [],
  "dynamic_hotspots": [
    {
      "id": "sql:macros/cents_to_dollars.sql",
      "source_file": "macros/cents_to_dollars.sql",
      "transformation_type": "dbt_model",
      "confidence": 0.45
    }
  ]
}
```

### jaffle-shop actual output

| Risk dimension | Count | Finding |
|----------------|-------|---------|
| `high_velocity_files` | 0 | Shallow clone — no git history available; velocity=0 for all files |
| `top_hubs` | 5 | stg_products, stg_supplies, stg_orders, stg_locations, order_items |
| `circular_dependency_clusters` | 0 | No circular imports — clean DAG structure |
| `files_with_parse_warnings` | 0 | Zero parse errors across all 33 files |
| `high_fanout_transformations` | 0 | No single SQL model outputs to ≥2 datasets |
| `dynamic_hotspot_transformations` | 2 | The 2 macro files (expected) |

---

## Reading `lineage_graph.html`

Open the HTML file in any modern browser — no server needed, fully self-contained.

### Node colours

| Colour | Node type |
|--------|-----------|
| Blue `#4ecdc4` | Transformation (SQL model, Python script) |
| Green `#45b7d1` | Dataset (table, view, seed, source) |

### Edge colours

| Colour | Edge type |
|--------|-----------|
| Orange | PRODUCES (transform → dataset) |
| Purple | CONSUMES (dataset ← transform, shown as dataset → transform) |

### Interaction

- **Drag** nodes to rearrange
- **Hover** over a node to see full metadata (type, source_file, confidence, classification flags)
- **Hover** over an edge to see evidence (extraction method, confidence, sql_preview snippet)
- **Scroll** to zoom in/out
- Use the **physics panel** (bottom-left) to freeze or adjust the force layout

---

## jaffle-shop Results (reference)

| Metric | Value |
|--------|-------|
| Datasets discovered | 27 |
| — dbt_source | 3 |
| — dbt_model | 15 |
| — dbt_seed | 4 |
| — table_ref | 5 |
| Transformations | 15 |
| PRODUCES edges | 15 |
| CONSUMES edges | 17 |
| Source datasets (`is_source_dataset=True`) | 9 |
| Sink datasets (`is_sink_dataset=True`) | 5 |
| Final models (`is_final_model=True`) | 5 |
| Intermediate models (`is_intermediate_model=True`) | 6 |
| Structurally empty files (macros) | 2 |
| Dynamic transformations (macros) | 2 |
| Low-confidence edges | 2 |

---

## Hydrologist Stats JSON schema

`hydrologist_stats.json` surface-level structure:

```json
{
  "datasets": {
    "total": 27,
    "by_type": {
      "dbt_model": 15,
      "dbt_seed": 4,
      "dbt_source": 3,
      "table_ref": 5
    }
  },
  "transformations": {
    "total": 15,
    "by_type": { "dbt_model": 15 }
  },
  "edges": {
    "produces": 15,
    "consumes": 17,
    "total": 32
  },
  "elapsed_seconds": 1.8
}
```
