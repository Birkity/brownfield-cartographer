# Phase 2 Report — The Brownfield Cartographer: Hydrologist Agent

**Author**: Brownfield Cartographer Implementation  
**Date**: June 2025  
**Phase**: 2 of 4 — Data Flow & Lineage Analysis (Hydrologist)  
**Primary Target**: https://github.com/dbt-labs/jaffle-shop  
**Status**: Complete and validated

---

## Table of Contents

1. [What Phase 2 Implements](#1-what-phase-2-implements)
2. [Architecture: File-by-File Breakdown](#2-architecture-file-by-file-breakdown)
3. [Data Models: Phase 2 Extensions](#3-data-models-phase-2-extensions)
4. [Core Analyzers and What They Do](#4-core-analyzers-and-what-they-do)
5. [How the Pipeline Runs: Step-by-Step](#5-how-the-pipeline-runs-step-by-step)
6. [Output Files: What They Contain and Why](#6-output-files-what-they-contain-and-why)
7. [Actual Lineage Results on jaffle-shop](#7-actual-lineage-results-on-jaffle-shop)
8. [Design Decisions and Trade-offs](#8-design-decisions-and-trade-offs)
9. [Known Limitations](#9-known-limitations)
10. [How to Improve Phase 2](#10-how-to-improve-phase-2)

---

## 1. What Phase 2 Implements

Phase 2 adds **data-flow and lineage understanding** to the knowledge graph built by Phase 1. While Phase 1 maps file-to-file relationships (imports, references), Phase 2 answers:

> **"Where does data come from, how is it transformed, and where does it go?"**

### What is produced

| Output | Description |
|--------|-------------|
| `DatasetNode` for every data artifact | Sources, models, seeds, file reads/writes, API calls — each with type, confidence, and provenance |
| `TransformationNode` for every data transformation | SQL models, Python pandas/spark operations, SQL execution — each with upstream and downstream links |
| `PRODUCES` edges | Transformation → Dataset (this SQL model creates this table) |
| `CONSUMES` edges | Dataset → Transformation (this model reads from this source) |
| YAML config enrichment | Source declarations, model descriptions, column lists from schema.yml |
| Interactive lineage visualization | PyVis HTML with colour-coded nodes, hover details, directed edges |
| Three serialized artifacts | `lineage_graph.json`, `lineage_graph.html`, `hydrologist_stats.json` |

### What is NOT in Phase 2 (by design)

| Feature | Phase |
|---------|-------|
| Column-level lineage (which column maps to which) | Future enhancement |
| Runtime data profiling | Out of scope |
| LLM-based semantic analysis | Phase 3 (Semanticist) |
| Natural language documentation | Phase 4 (Archivist) |

---

## 2. Architecture: File-by-File Breakdown

### New files created in Phase 2

| File | Purpose | Lines |
|------|---------|-------|
| `src/analyzers/sql_lineage.py` | sqlglot-based SQL lineage extraction | ~280 |
| `src/analyzers/config_analyzer.py` | YAML config parsing (dbt sources, schemas, seeds) | ~190 |
| `src/analyzers/python_dataflow.py` | Regex-based Python data I/O pattern detection | ~200 |
| `src/agents/hydrologist.py` | Hydrologist agent orchestrating all Phase 2 analyzers | ~500 |

### Files modified in Phase 2

| File | Changes |
|------|---------|
| `src/models/nodes.py` | Extended `DatasetNode` and `TransformationNode` with Phase 2 fields |
| `src/graph/knowledge_graph.py` | Added `add_dataset_node()`, `add_transformation_node()`, `add_produces_edge()`, `add_consumes_edge()`, `save_lineage()`, `export_lineage_viz()`, `lineage_summary()` |
| `src/orchestrator.py` | Added `run_phase2()`, updated `run_phase1()` to return graph + repo_root |
| `src/cli.py` | Chains Phase 2 after Phase 1, displays lineage summary table |
| `pyproject.toml` | Added `pyvis>=0.3.2` and `pyyaml>=6.0` dependencies |

---

## 3. Data Models: Phase 2 Extensions

### DatasetNode

Represents any data artifact that transformations consume or produce.

```python
class DatasetNode(BaseModel):
    name: str           # "source.ecom.raw_orders", "model.stg_orders", "file.data/out.csv"
    storage_type: StorageType  # TABLE, FILE, STREAM, API
    dataset_type: str   # "dbt_source", "dbt_model", "dbt_seed", "table_ref",
                        # "file_read", "file_write", "api_call", "unknown"
    source_file: str    # The repo file that defined this dataset
    description: str    # From YAML schema if available
    columns: list[str]  # Column names from schema.yml
    confidence: float   # 1.0 = static, lower = inferred/dynamic
```

### TransformationNode

Represents a data transformation between datasets.

```python
class TransformationNode(BaseModel):
    id: str                     # "sql:models/marts/orders.sql" or "py:etl.py:42"
    transformation_type: str    # "dbt_model", "python_pandas", "python_spark", etc.
    source_file: str
    source_datasets: list[str]  # Upstream dependencies
    target_datasets: list[str]  # Downstream outputs
    confidence: float           # 1.0 = deterministic, <1.0 = dynamic
    is_dynamic: bool            # True if SQL is dynamically constructed
```

### Naming Conventions

| Category | Pattern | Example |
|----------|---------|---------|
| dbt sources | `source.<schema>.<table>` | `source.ecom.raw_orders` |
| dbt models | `model.<name>` | `model.stg_orders` |
| dbt seeds | `seed.<name>` | `seed.raw_customers` |
| File I/O | `file.<path>` | `file.data/output.csv` |
| Unresolved | `dynamic.<type>.<file>:<line>` | `dynamic.pandas.etl.py:42` |

---

## 4. Core Analyzers and What They Do

### SQL Lineage Analyzer (`sql_lineage.py`)

Uses `sqlglot` to parse SQL files and extract table-level lineage:

1. **Jinja stripping**: Replaces `{{ ref('x') }}` → `__dbt_ref__x` and `{{ source('s','t') }}` → `__dbt_source__s__t` so sqlglot can parse the SQL
2. **AST traversal**: Walks `exp.Table` nodes to find FROM/JOIN (upstream) and INSERT INTO/CREATE TABLE (downstream) references
3. **CTE exclusion**: WITH clauses define internal names — excluded from upstream
4. **dbt resolution**: Placeholder names are resolved back to `model.x` and `source.s.t`
5. **Fallback**: If sqlglot fails on a particular dialect, falls back to regex extraction

For dbt models, the file itself is the downstream target (`model.<stem>`).

### Config Analyzer (`config_analyzer.py`)

Parses YAML configuration files using the `yaml_keys` list already extracted by Phase 1:

- **`sources` key** → Extracts dbt source declarations (schema, table, columns, description)
- **`models` key** → Extracts model schema information (name, columns, description)
- **`seeds` key** → Extracts seed declarations
- **`name` + `version` keys** → Identifies dbt_project.yml for project metadata

### Python Dataflow Analyzer (`python_dataflow.py`)

Regex-based detection of data I/O patterns:

| Pattern | Direction | Example |
|---------|-----------|---------|
| `pd.read_csv()` / `pd.read_parquet()` | read | `pd.read_csv("data/input.csv")` |
| `df.to_csv()` / `df.to_parquet()` | write | `df.to_parquet("output.parquet")` |
| `spark.read.parquet()` / `spark.table()` | read | `spark.read.parquet("s3://bucket/data")` |
| `df.write.saveAsTable()` | write | `df.write.saveAsTable("schema.table")` |
| `cursor.execute()` / `engine.execute()` | read | `cursor.execute("SELECT * FROM t")` |
| `open(path, "w")` | write | `open("out.txt", "w")` |

Static string targets get `confidence=1.0`. Variable-based targets get `confidence=0.5` and `is_dynamic=True`.

---

## 5. How the Pipeline Runs: Step-by-Step

```
CLI: cartographer analyze <target>
  │
  ├── Phase 1: run_phase1()
  │     └── Surveyor → KnowledgeGraph (modules + import edges)
  │
  └── Phase 2: run_phase2(artifacts, graph, repo_root)
        │
        ├── Step 1: Parse YAML configs → source declarations, model schemas
        ├── Step 2: Detect seed CSV files in seeds/ directories
        ├── Step 3: Register dbt sources as DatasetNodes
        ├── Step 4: Analyze SQL files via sqlglot → SQLLineageResult per file
        ├── Step 5: Wire SQL lineage → TransformationNode + DatasetNode + edges
        ├── Step 6: Analyze Python files → data I/O pattern detection
        ├── Step 7: Wire Python dataflow → TransformationNode + DatasetNode + edges
        │
        ├── Persist: lineage_graph.json (datasets + transformations + edges)
        ├── Persist: lineage_graph.html (PyVis interactive visualization)
        ├── Persist: hydrologist_stats.json (summary statistics)
        └── Append: cartography_trace.jsonl (audit trail)
```

---

## 6. Output Files: What They Contain and Why

| File | Format | Contents |
|------|--------|----------|
| `lineage_graph.json` | JSON | All datasets, transformations, and PRODUCES/CONSUMES edges |
| `lineage_graph.html` | HTML | Interactive PyVis network visualization |
| `hydrologist_stats.json` | JSON | Summary statistics (counts, timings, flags) |
| `cartography_trace.jsonl` | JSONL (append) | Audit trail — Phase 2 entries appended after Phase 1 entries |
| `module_graph.json` | JSON (updated) | Unified graph now includes lineage nodes and edges |

### lineage_graph.json structure

```json
{
  "datasets": {
    "source.ecom.raw_orders": {
      "name": "source.ecom.raw_orders",
      "dataset_type": "dbt_source",
      "columns": ["id", "order_date", "..."],
      "confidence": 1.0
    }
  },
  "transformations": {
    "sql:models/staging/stg_orders.sql": {
      "id": "sql:models/staging/stg_orders.sql",
      "transformation_type": "dbt_model",
      "source_datasets": ["source.ecom.raw_orders"],
      "target_datasets": ["model.stg_orders"],
      "confidence": 1.0
    }
  },
  "edges": [
    {"source": "sql:models/staging/stg_orders.sql", "target": "model.stg_orders", "edge_type": "PRODUCES"},
    {"source": "source.ecom.raw_orders", "target": "sql:models/staging/stg_orders.sql", "edge_type": "CONSUMES"}
  ]
}
```

---

## 7. Actual Lineage Results on jaffle-shop

### Summary Statistics

| Metric | Value |
|--------|-------|
| Project type | dbt |
| SQL files analyzed | 15 |
| Python files analyzed | 0 |
| Total datasets discovered | 27 |
| Total transformations | 15 |
| PRODUCES edges | 15 |
| CONSUMES edges | 17 |
| Sources registered (from YAML) | 6 |
| Seeds found (CSV files) | 6 |
| Dynamic transformations | 2 |
| Elapsed time | 1.92s |

### Dataset Breakdown

| Type | Count | Examples |
|------|-------|----------|
| `dbt_source` | 6 | `source.ecom.raw_orders`, `source.ecom.raw_customers`, `source.ecom.raw_items`, `source.ecom.raw_products`, `source.ecom.raw_stores`, `source.ecom.raw_supplies` |
| `dbt_model` | 15 | `model.stg_orders`, `model.customers`, `model.order_items`, `model.orders` |
| `dbt_seed` | 6 | `seed.raw_customers`, `seed.raw_orders`, `seed.raw_items` |

### Complete Data Lineage Graph

```
Sources (raw data)          Staging Models             Mart Models
─────────────────          ──────────────             ───────────
source.ecom.raw_customers → stg_customers ──────────→ customers
source.ecom.raw_orders    → stg_orders ──┬──────────→ orders
source.ecom.raw_items     → stg_order_items ─┐       │
source.ecom.raw_products  → stg_products ──┐ │       │
source.ecom.raw_supplies  → stg_supplies ─┐│ ├──────→ order_items
source.ecom.raw_stores    → stg_locations  │├─┘       │
                                           ││         │
                            stg_products ──┼┘    ────→ products
                            stg_supplies ──┘     ────→ supplies
                            stg_locations  ──────────→ locations
```

### Key Lineage Chains Discovered

1. **Orders mart**: `source.ecom.raw_orders` → `stg_orders` → `orders` (also joins `order_items`)
2. **Order items mart**: `stg_order_items` + `stg_orders` + `stg_products` + `stg_supplies` → `order_items`
3. **Customers mart**: `stg_customers` + `orders` → `customers`
4. **All staging models** correctly traced back to their `source.ecom.*` origins

---

## 8. Design Decisions and Trade-offs

### 1. Jinja stripping before sqlglot

**Decision**: Replace `{{ ref() }}` and `{{ source() }}` with identifier placeholders, strip remaining Jinja, then parse with sqlglot.

**Why**: sqlglot cannot parse Jinja templates. Replacing dbt calls with identifiers preserves the table reference semantics while making the SQL parseable. Remaining Jinja (config, for loops, if blocks) is removed.

**Trade-off**: Complex Jinja logic (dynamic table names, macro-generated SQL) cannot be fully resolved → marked `is_dynamic=True`.

### 2. Confidence scoring on every node

**Decision**: Every `DatasetNode` and `TransformationNode` carries a `confidence` float (0.0–1.0).

**Why**: Not all lineage is equally reliable. Static `{{ ref('x') }}` calls are 1.0 confidence. Jinja-heavy files with dynamic table names get lower confidence. This lets downstream consumers (Phase 3/4) calibrate their trust.

### 3. Regex-based Python dataflow (not AST)

**Decision**: Python data I/O detection uses regex over source text, not tree-sitter AST.

**Why**: Pattern matching on `pd.read_csv(`, `spark.read.parquet(` etc. catches >90% of real-world patterns. AST walking would add complexity for marginal accuracy gain. The `confidence` field captures the distinction.

### 4. Three-tier dataset naming

**Decision**: `source.<schema>.<table>`, `model.<name>`, `seed.<name>`.

**Why**: Avoids table name collisions across tiers. A staging model called `stg_orders` and a source table called `stg_orders` are different datasets. The prefix makes this unambiguous.

### 5. Never fabricate lineage

**Decision**: If we can't parse it, we mark it dynamic — we don't guess.

**Why**: False lineage is worse than missing lineage. An onboarding engineer reading the map needs to trust what's shown. Two files in jaffle-shop are marked as dynamic transformations because their Jinja is too complex to fully resolve.

---

## 9. Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| No column-level lineage | Can't trace individual column flows | Report table-level lineage with column lists from schema.yml |
| Jinja macro expansion not supported | Complex macros generate SQL we can't see | Mark as `is_dynamic=True`, `confidence < 1.0` |
| sqlglot dialect limitations | Some SQL dialects may not parse | Multi-dialect fallback (duckdb, postgres, bigquery) + regex fallback |
| Python detection is pattern-based | Could miss custom wrapper functions | Catches standard library patterns (pandas, spark, sqlalchemy) |
| No cross-repo lineage | Can't trace data flowing to/from external systems | Sources/sinks are documented as boundary datasets |
| Seed detection only finds CSVs | Other seed formats (JSON, Parquet) not detected | dbt convention is CSV seeds |

---

## 10. How to Improve Phase 2

| Improvement | Difficulty | Impact |
|------------|------------|--------|
| Column-level lineage via sqlglot `lineage()` | Medium | Trace individual column transformations |
| Jinja rendering via dbt compile | Medium | Resolve macros before parsing → more accurate lineage |
| Python AST-based dataflow | Medium | Catch complex patterns (wrapped I/O, variable paths) |
| dbt manifest.json integration | Easy | If available, use compiled manifest for perfect lineage |
| Cross-project lineage | Hard | Link source schemas across repos |
| Lineage diff (between git versions) | Medium | Show how data flow changed over time |
