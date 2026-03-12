# Phase 1 — Surveyor: Static Structure Analysis

## Overview

The **Surveyor** is the first agent in the Brownfield Cartographer pipeline. It performs a
complete static analysis of any local or remote code repository and builds a **module-level
knowledge graph** that captures the architectural skeleton of the codebase.

It runs automatically as part of `cartographer analyze <repo>` and completes before Phase 2.

---

## What the Surveyor does

| Step | Action |
|------|--------|
| 1 | Walk the repository and collect all source files (filters binaries, `node_modules`, `.git`) |
| 2 | Route each file to its language-specific analyser (AST for Python/SQL/YAML/JS/TS, regex for everything else) |
| 3 | Extract imports, function/class definitions, cyclomatic complexity, and dbt `{{ ref() }}` expressions |
| 4 | Build `IMPORTS` and `DBT_REF` edges in the knowledge graph |
| 5 | Compute PageRank, in/out-degree, and SCC (strongly-connected components) to identify hubs and cycles |
| 6 | Compute git-velocity (commits-per-file over the last 30 days, or `--velocity-days` window) |
| 7 | Auto-detect project type from root config files (`dbt_project.yml`, `go.mod`, `pom.xml`, etc.) |
| 8 | **Enrich:** classify each module with a role, entry-point flag, hub flag, and cycle flag |
| 9 | Write all output artifacts to `.cartography/module_graph/` |

---

## Output Artifacts

All artifacts land in `.cartography/<repo-name>/module_graph/` (repo name is auto-derived from
the target path or URL). Use `--output-dir` to override the destination.

| File | Description |
|------|-------------|
| `module_graph.json` | NetworkX node-link JSON of the import graph |
| `module_graph_modules.json` | Full `ModuleNode` records for every file (imports, functions, classes, complexity, velocity, **classification fields**) |
| `surveyor_stats.json` | Summary: hub counts, import/dbt-ref edges, cycles, velocity stats, elapsed time, project type |
| `module_graph.png` | Dark-theme PNG graph (matplotlib, 200 DPI, degree-scaled nodes, neon colour palette, **role rings**) |

---

## Module Classification

After building the graph, the Surveyor enriches every `ModuleNode` with the following fields:

### `role` — architectural role string

| Value | Meaning | Detection heuristic |
|-------|---------|---------------------|
| `source` | Raw seed / external data layer | file is in `seeds/` directory |
| `staging` | Staging / light-transform layer | filename starts with `stg_` |
| `intermediate` | Intermediate model | filename starts with `int_` |
| `mart` | Mart / presentation layer | filename starts with `fct_` or `dim_`, or is inside `marts/` |
| `macro` | Jinja macro | file is in `macros/` directory |
| `config` | Project configuration | YAML file with `sources` in its stem |
| `test` | Test file | file is in `tests/` or `test/` directory |
| `utility` | Shared helper | Python file in `utils/` or `helpers/` |
| `unknown` | Not matched | default |

> For non-dbt repos the role is usually `unknown` unless the file path follows one of the
> directory-name heuristics above (e.g. a `tests/` directory always yields `test`).

### Boolean flags

| Field | True when… |
|-------|-----------|
| `is_entry_point` | Module has **in-degree 0** — nothing imports it; it is a top-level entry point |
| `is_hub` | Module is in the **top-10 PageRank** nodes — a high-connectivity architectural hub |
| `in_cycle` | Module participates in a **circular dependency** (member of an SCC with size > 1) |

### `classification_confidence`

A `float` in `[0, 1]` expressing how confident the classifier is in the assigned role.
Values close to `1.0` mean the heuristic matched a strong signal (e.g. `seeds/` directory
prefix). Values of `0.5` or lower indicate the role was inferred from a weaker signal.

---

## Edge Evidence

Every `IMPORTS` edge in the module graph carries an `evidence` dictionary:

```json
{
  "source_file": "models/staging/stg_customers.sql",
  "line": 3,
  "expression": "customers",
  "is_relative": false,
  "extraction_method": "dbt_jinja_regex"
}
```

`DBT_REF` edges include:

```json
{
  "source_file": "models/marts/core/fct_orders.sql",
  "expression": "{{ ref('stg_orders') }}",
  "extraction_method": "dbt_jinja_regex",
  "ref_name": "stg_orders"
}
```

### Confidence scoring by extraction method

| Extraction method | Confidence | Notes |
|-------------------|-----------|-------|
| `tree_sitter_ast` | 1.00 | Full AST parse — highest fidelity |
| `dbt_jinja_regex` | 1.00 | Jinja `{{ ref() }}` pattern — very reliable in dbt projects |
| `config_parsing` | 0.95 | YAML config parsing |
| `sqlglot` | 0.90 | Static SQL parse — occasional ambiguity on complex dialects |
| `regex` | 0.65 | Simple import regex — misses dynamic patterns |
| `sqlglot_dynamic` | 0.55 | SQL with Jinja/templating — partial parse only |
| `inferred` | 0.40 | Shape-based inference — least reliable |

Edge widths in `module_graph.png` scale proportionally by confidence (thicker = more certain).

---

## Reading `module_graph.png`

The PNG uses a dark background with a neon-coloured node palette. Each node is a file.

### Node colours (by language)

| Colour | Language |
|--------|---------|
| Cyan `#00d2ff` | Python |
| Yellow `#ffd700` | SQL |
| Green `#39d353` | YAML / JSON |
| Blue `#4ecdc4` | JavaScript / TypeScript |
| Steel blue | Other / unknown |

### Node size

Scales by **total degree** (imports-in + imports-out). Highly connected files appear larger.

### Role badges in labels

Each node label may carry a short role badge in square brackets:

| Badge | Role |
|-------|------|
| `[stg]` | staging |
| `[mart]` | mart / presentation |
| `[int]` | intermediate |
| `[src]` | source / seed |
| `[macro]` | Jinja macro |
| `[test]` | test file |
| `[cfg]` | configuration |

### Overlay rings

Three concentric overlay rings mark special structural properties:

| Ring colour | Meaning |
|-------------|---------|
| Gold `#FFD700` | **Hub** — top-10 PageRank (high-connectivity architectural hub) |
| Red `#FF4757` | **Cycle** — participates in a circular dependency chain |
| Green `#2ED573` | **Entry point** — in-degree 0; nothing imports this module |

---

## jaffle-shop Results (reference run)

| Metric | Value |
|--------|-------|
| Files parsed | 33 / 33 (0 grammar-missing) |
| Modules with a named role | 33 / 33 |
| Staging modules | 12 |
| Mart modules | 13 |
| Config modules | 6 |
| Macro modules | 2 |
| DBT_REF edges | 11 |
| Hub modules (top-10 PageRank) | 5 shown (stg_products 0.0562, stg_supplies 0.0562, stg_orders 0.0499, stg_locations 0.0475, order_items 0.0412) |
| Circular dependencies | 0 |
| Dead-code candidates | 7 |
| Project type | `dbt` |
| Output location | `.cartography/jaffle-shop/module_graph/` |

---

## Surveyor Stats JSON schema

`surveyor_stats.json` surface-level structure:

```json
{
  "project_type": "dbt",
  "total_files": 33,
  "parsed_ok": 33,
  "grammar_missing": 0,
  "parse_errors": 0,
  "import_edges": 11,
  "dbt_ref_edges": 11,
  "hub_count": 10,
  "cycle_count": 0,
  "velocity_p50": 0,
  "velocity_p95": 3,
  "elapsed_seconds": 4.1
}
```
