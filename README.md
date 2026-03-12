# Brownfield Cartographer

A multi-agent codebase intelligence system for rapid FDE onboarding in production environments.

Ingests any local repository or GitHub URL and produces a queryable knowledge graph of the system's architecture, data flows, and semantic structure.

---

## Phase Status

| Phase | Agent | Status |
|-------|-------|--------|
| 1 | Surveyor (Static Structure) | ✅ Complete |
| 2 | Hydrologist (Data Lineage) | ✅ Complete |
| 3 | Semanticist (LLM Purpose Analysis) | 🔜 Planned |
| 4 | Archivist + Navigator | 🔜 Planned |

---

## Supported Languages

| Language | Extensions | Analysis method | What is extracted |
|----------|-----------|-----------------|-------------------|
| Python | `.py`, `.pyi` | tree-sitter AST | imports, functions, classes, cyclomatic complexity |
| SQL | `.sql` | tree-sitter AST + sqlglot | table references, `{{ ref() }}` / `{{ source() }}` dbt dependencies |
| YAML | `.yml`, `.yaml` | tree-sitter AST | top-level keys |
| JavaScript | `.js`, `.mjs`, `.cjs` | tree-sitter AST | imports, functions |
| TypeScript | `.ts`, `.tsx` | tree-sitter AST | imports, functions |
| Java | `.java` | regex | `import` statements |
| Kotlin | `.kt`, `.kts` | regex | `import` statements |
| Scala | `.scala`, `.sc` | regex | `import` statements |
| Go | `.go` | regex | `import` paths |
| Rust | `.rs` | regex | `use` declarations |
| C# | `.cs` | regex | `using` directives |
| Ruby | `.rb` | regex | `require` / `require_relative` |
| Shell | `.sh`, `.bash`, `.zsh` | LOC only | file inventoried |

> Regex-based languages need **no extra installation** — they work immediately after `uv pip install -e .`

---

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (`pip install uv`)
- Git (required for git-velocity analysis and GitHub URL cloning)

---

## Installation

```bash
# From the project root
uv pip install -e .
```

This installs all required dependencies including:
- `tree-sitter` + language grammars (Python, YAML, JavaScript, TypeScript, **SQL**)
- `networkx` for graph analytics
- `pydantic` for typed data models
- `click` + `rich` for the CLI
- `sqlglot` for SQL lineage parsing (Phase 2)
- `pyyaml` for YAML config analysis (Phase 2)
- `pyvis` for interactive lineage visualization (Phase 2)

> **Java, Kotlin, Scala, Go, Rust, C#, Ruby, Shell** are supported out of the box via regex-based
> import extraction — no additional grammar packages needed for these languages.

> `tree-sitter-sql` is included as a standard dependency. All 33 files in
> jaffle-shop (Python, YAML, SQL, JS/TS) are fully parsed via AST with **zero
> grammar fallbacks**.

---

## Running Phase 1

### Analyse a local repository

```bash
uv run cartographer analyze /path/to/your/repo
```

### Analyse the primary target (dbt jaffle-shop)

```bash
# Option A: clone manually, then point at the local dir
git clone --depth 50 https://github.com/dbt-labs/jaffle-shop /tmp/jaffle-shop
uv run cartographer analyze /tmp/jaffle-shop

# Option B: pass the GitHub URL directly (cartographer clones for you)
uv run cartographer analyze https://github.com/dbt-labs/jaffle-shop
```

### Full git history (accurate velocity for remote repos)

```bash
# --full-history clones without --depth, gives accurate change-velocity data
# Slower for large repos but required if shallow clone shows velocity=0
uv run cartographer analyze https://github.com/dbt-labs/jaffle-shop --full-history
```

### Custom output directory

```bash
uv run cartographer analyze /tmp/jaffle-shop --output-dir ./jaffle-cartography
```

### Extended git-velocity window (90 days)

```bash
uv run cartographer analyze /tmp/jaffle-shop --velocity-days 90
```

### Verbose logging

```bash
uv run cartographer --verbose analyze /tmp/jaffle-shop
```

---

## Output Artifacts

All artifacts are written to `.cartography/` (or the directory you specify with `--output-dir`).

### Phase 1 — `module_graph/`

| File | Description |
|------|-------------|
| `module_graph.json` | NetworkX node-link JSON of the import graph (IMPORTS + DBT_REF edges, with confidence + evidence on every edge) |
| `module_graph_modules.json` | Full `ModuleNode` records (imports, dbt_refs, functions, classes, complexity, velocity, **role, is_entry_point, is_hub, in_cycle**) |
| `surveyor_stats.json` | Summary: hub counts, import edges, dbt_ref_edges, cycles, velocity, elapsed, project_type |
| `module_graph.png` | Dark-theme graph PNG (matplotlib, 200 DPI, degree-scaled nodes, neon palette, **role badges, hub/cycle/entry-point overlay rings**) |

### Phase 2 — `data_lineage/`

| File | Description |
|------|-------------|
| `lineage_graph.json` | Datasets, transformations, and PRODUCES/CONSUMES edges (with confidence + evidence; **dataset classification flags**) |
| `lineage_graph.html` | Interactive PyVis lineage map (dark theme, hover tooltips, physics layout) |
| `hydrologist_stats.json` | Phase 2 summary: dataset counts by type, transformation counts, edge stats |

### Cross-phase reports — `.cartography/`

| File | Description |
|------|-------------|
| `cartography_trace.jsonl` | Audit log: one JSON line per agent action |
| `blind_spots.md` | Unresolved references: parse failures, dynamic transforms, low-confidence datasets and edges |
| `unresolved_references.json` | Machine-readable version of `blind_spots.md` |
| `high_risk_areas.md` | Risk summary: high-velocity hubs, cycles, parse warnings, dynamic hotspots |

### Expected output for jaffle-shop

Since jaffle-shop is primarily SQL + YAML (a dbt project), Phase 1 + Phase 2 produce:
- **33 files parsed via AST** — Python, YAML, SQL (`tree-sitter-sql`), and JS/TS have dedicated grammars; **0 grammar-missing files**
- **Module classification**: 33/33 modules assigned a named role (`staging`, `mart`, `config`, `macro`, `unknown`)
- **SQL table references**: extracted via `tree-sitter-sql` AST
- **YAML files**: top-level keys and dbt source/seed/model declarations extracted
- **Import graph**: **11 DBT_REF edges** connecting mart models → staging models
- **PageRank**: staging models correctly identified as architectural hubs (gold ring in PNG)
- **Dataset classification**: 25/27 datasets classified with source/sink/final/intermediate flags
- **Complexity scores**: populated for all Python files (0.0 for SQL/YAML)
- **Project type**: `dbt` (auto-detected from `dbt_project.yml`)
- **3 new risk reports**: `blind_spots.md`, `high_risk_areas.md`, `unresolved_references.json`

---

## Project Structure

```
src/
├── cli.py                     # Click CLI (analyze + query commands)
├── orchestrator.py            # Pipeline wiring: Phase 1 + Phase 2 entry points
├── models/
│   └── nodes.py               # Pydantic schemas: ModuleNode, DatasetNode, TransformationNode…
├── analyzers/
│   ├── language_router.py     # Extension → Language routing (28 extensions, 14 languages)
│   ├── tree_sitter_analyzer.py# AST parsing (Python/YAML/JS/TS) + regex extraction
│   ├── dbt_helpers.py         # Regex extraction of {{ ref() }} and {{ source() }} from SQL
│   ├── sql_lineage.py         # [Phase 2] sqlglot-based SQL lineage & dataset extraction
│   ├── config_analyzer.py     # [Phase 2] YAML config parsing (dbt sources/seeds/models)
│   └── python_dataflow.py     # [Phase 2] pandas/spark read/write + SQL execution detection
├── agents/
│   ├── surveyor.py            # Surveyor: file scan → graph → PageRank/SCC
│   └── hydrologist.py         # [Phase 2] Hydrologist: data lineage → datasets + transforms
├── graph/
│   ├── knowledge_graph.py     # NetworkX wrapper + analytics + PNG/HTML visualization
│   ├── graph_viz.py           # Module graph PNG (role rings, confidence-scaled edges)
│   ├── graph_analytics.py     # PageRank, SCC, degree stats
│   ├── enrichment.py          # Module + dataset classification; confidence scoring
│   └── reporting.py           # Blind-spots + high-risk markdown/JSON report writers
└── utils/
    ├── repo_loader.py          # Local path or GitHub URL → local Path (--full-history support)
    ├── file_inventory.py       # Walk repo, filter by language
    └── git_tools.py            # git log velocity per file

reports/
├── phase1.md                  # Phase 1 feature reference (classification, evidence, visualization)
└── phase2.md                  # Phase 2 feature reference (lineage, blind spots, high-risk)
```

---

## Enrichment & Reporting (Polish Layer)

Both phases emit rich metadata beyond the raw graph topology.

### Node Classification

Every `ModuleNode` (Phase 1) carries:

| Field | Type | Description |
|-------|------|-------------|
| `role` | `str` | `staging`, `mart`, `intermediate`, `source`, `macro`, `config`, `test`, `utility`, `unknown` |
| `is_entry_point` | `bool` | In-degree 0 — nothing imports this module |
| `is_hub` | `bool` | Top-10 PageRank — high-connectivity architectural hub |
| `in_cycle` | `bool` | Participates in a circular dependency |
| `classification_confidence` | `float` | Heuristic confidence in the assigned role (`0.0`–`1.0`) |

Every `DatasetNode` (Phase 2) carries:

| Field | Type | Description |
|-------|------|-------------|
| `is_source_dataset` | `bool` | No PRODUCES edges into it — raw input (seed, external source) |
| `is_sink_dataset` | `bool` | No CONSUMES edges out — terminal output (final model, export) |
| `is_final_model` | `bool` | Name matches `fct_*`, `dim_*`, or lives in `marts/` |
| `is_intermediate_model` | `bool` | Name matches `stg_*` or `int_*` |

### Confidence Scoring

Every edge carries a `confidence` float and an `evidence` dict. Confidence is derived from the
extraction method:

| Method | Score | Notes |
|--------|-------|-------|
| `tree_sitter_ast` | 1.00 | Full AST parse |
| `dbt_jinja_regex` | 1.00 | `{{ ref() }}` — deterministic |
| `config_parsing` | 0.95 | YAML config declarations |
| `sqlglot` | 0.90 | Static SQL parse |
| `regex` | 0.65 | Simple import regex |
| `sqlglot_dynamic` | 0.55 | SQL with unresolved Jinja |
| `inferred` | 0.40 | Shape-based — least reliable |

Edge widths in the module PNG and lineage HTML scale proportionally by confidence.

### Reading the Module Graph PNG

Overlay rings on `module_graph.png`:

| Ring | Colour | Meaning |
|------|--------|---------|
| Gold | `#FFD700` | **Hub** — top-10 PageRank |
| Red | `#FF4757` | **Cycle** — circular dependency |
| Green | `#2ED573` | **Entry point** — in-degree 0 |

Node labels include short role badges: `[stg]` staging · `[mart]` mart · `[int]` intermediate ·
`[src]` source · `[macro]` macro · `[test]` test · `[cfg]` config.

### Blind Spots (`blind_spots.md`)

Surfaces everything the pipeline could not fully resolve:
- **Parse failures** and **grammar-missing** files
- **Structurally empty** files (parsed OK but no symbols extracted)
- **Dynamic transformations** with unresolved Jinja (incomplete lineage)
- **Low-confidence datasets and edges** (confidence < 0.7)

Machine-readable version: `unresolved_references.json`.

### High-Risk Areas (`high_risk_areas.md`)

Aggregated risk signals for onboarding engineers:
- **High-velocity files** — most git commits in the velocity window (churn risk)
- **Top hubs** — highest-PageRank modules (single-point-of-failure risk)
- **Circular dependencies** — SCCs with size > 1 (refactoring debt)
- **High fan-out transforms** — produce many output datasets
- **Dynamic hotspots** — incomplete lineage, needs manual tracing

> See [reports/phase1.md](reports/phase1.md) and [reports/phase2.md](reports/phase2.md)
> for full field references and interpretation guides.

---

## Running on Your Week 1 Repo

```bash
uv run cartographer analyze /path/to/your/week1-code --output-dir .cartography/self-audit
```

Compare the generated `module_graph_modules.json` against your own `ARCHITECTURE_NOTES.md`
to see what the automated analysis found vs. what you documented manually.

---

## Running Phase 2 (Hydrologist — Data Lineage)

Phase 2 runs automatically after Phase 1 as part of the same `analyze` command:

```bash
# Run both Phase 1 + Phase 2 on jaffle-shop
uv run cartographer analyze https://github.com/dbt-labs/jaffle-shop
```

### What Phase 2 produces

- **Datasets** — every table, view, seed, source, or file treated as data (classified by type)
- **Transformations** — every SQL file, Python script, or dbt model that reads/writes data
- **PRODUCES edges** — transformation → output dataset
- **CONSUMES edges** — transformation ← input dataset
- **`lineage_graph.html`** — interactive dark-theme lineage graph with hover tooltips, physics layout, and a colour-coded legend

### Example output on jaffle-shop

```
Phase 2 — Hydrologist: data lineage
  Datasets found     : 27
    dbt_source       : 3
    dbt_model        : 15
    dbt_seed         : 4
    table_ref        : 5
  Transformations    : 15
  Lineage edges      : 32  (PRODUCES: 15, CONSUMES: 17)
  Saved → .cartography/lineage_graph.html  (open in any browser)
  Saved → .cartography/lineage_graph.json
```

The HTML visualization opens in any browser — no server needed, fully self-contained.

---

## Running on Any Repo Type

Phase 1 v3 automatically detects the project type and supports 14 languages. Run it on anything:

```bash
# Go microservice
uv run cartographer analyze /path/to/go-service

# Java Spring Boot app
uv run cartographer analyze /path/to/java-app

# React front-end
uv run cartographer analyze /path/to/react-app

# Rust CLI tool
uv run cartographer analyze /path/to/rust-project
```

The CLI overview table will show a **Project type** row (e.g. `go`, `java-maven`, `react`, `rust`, `django`, `nextjs`) detected from root config files (`go.mod`, `pom.xml`, `package.json`, etc.).

---

## Development

```bash
# Install with dev extras
uv pip install -e ".[dev]"

# Run tests (few exist at this stage)
uv run pytest tests/ -v
```

---

## Roadmap

| Phase | Agent | What it does |
|-------|-------|--------------|
| 1 ✅ | Surveyor | File scan, import graph, PageRank hubs, git velocity, project-type detection, module classification, edge evidence |
| 2 ✅ | Hydrologist | Data lineage — datasets, transformations, PRODUCES/CONSUMES edges, dataset classification, blind-spots + high-risk reports |
| 3 🔜 | Semanticist | LLM-powered purpose annotation for modules and datasets |
| 4 🔜 | Archivist + Navigator | Semantic search, Q&A chat over the knowledge graph |
