# Brownfield Cartographer

A multi-agent codebase intelligence system for rapid FDE onboarding in production environments.

Ingests any local repository or GitHub URL and produces a queryable knowledge graph of the system's architecture, data flows, and semantic structure.

---

## Phase Status

| Phase | Agent | Status |
|-------|-------|--------|
| 1 | Surveyor (Static Structure) | Complete |
| 2 | Hydrologist (Data Lineage) | Complete |
| 3 | Semanticist (LLM Purpose Analysis) | Complete |
| 4 | Archivist + Navigator | Complete |
| 5 | Dashboard (Streamlit Experience) | Complete |

---

## Supported Languages

| Language | Extensions | Analysis method | What is extracted |
|----------|-----------|-----------------|-------------------|
| Python | `.py`, `.pyi` | tree-sitter AST | imports, functions, classes, cyclomatic complexity, data I/O calls |
| Notebook | `.ipynb` | cell-aware notebook reconstruction + tree-sitter AST | Python imports, notebook dataflow, comment ratio |
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

> Regex-based languages need **no extra installation** and work immediately after `uv pip install -e .`

---

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (`pip install uv`)
- Git (required for git-velocity analysis and GitHub URL cloning)
- [Ollama](https://ollama.com/) (required for Phase 3 LLM analysis; optional, graceful degradation without it)

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
- `plotly` for dashboard charts and scorecards (Phase 5)
- `streamlit` for the interactive dashboard shell (Phase 5)
- `pyvis` for interactive lineage visualization (Phase 2)
- `requests` for Ollama REST API communication (Phase 3)

> **Java, Kotlin, Scala, Go, Rust, C#, Ruby, Shell** are supported out of the box via regex-based
> import extraction; no additional grammar packages are needed for these languages.

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

### Lineage summary view

```bash
uv run cartographer lineage-summary ./.cartography/jaffle-shop
uv run cartographer lineage-summary ./.cartography/jaffle-shop --node source.ecom.raw_orders
```

### Navigator query mode

```bash
uv run cartographer query ./.cartography/jaffle-shop "What does this repository do?"
uv run cartographer query ./.cartography/jaffle-shop "What are the main data pipelines?"
uv run cartographer query ./.cartography/jaffle-shop "Which modules contain the most business logic?"
uv run cartographer query ./.cartography/jaffle-shop "What breaks if source.ecom.raw_orders changes?"
```

Use `--json-output` to print the structured answer object directly. Query logs are written to
`.cartography/<repo-name>/queries/` and Phase 4 answers are served from saved artifacts rather
than rescanning the repository.

### Dashboard

```bash
streamlit run streamlit_app.py
```

The dashboard auto-discovers available `.cartography/<repo-name>/` artifact roots and lets you
explore all five phases without rescanning the repository. If your environment predates Phase 5,
rerun `uv pip install -e .` first so Streamlit and Plotly are installed.

---

## Ollama Setup (Phase 3 / Phase 4)

Phase 3 requires Ollama running locally with at least one of these models:

```bash
# Install Ollama from https://ollama.com/
# Pull the recommended models:
ollama pull qwen3-coder:480b-cloud    # code analysis tasks
ollama pull deepseek-v3.1:671b-cloud  # synthesis & clustering tasks
```

If Ollama is not running or no models are found, Phase 3 gracefully degrades to
heuristic-only mode: purpose statements are generated from metadata (role, dbt-refs,
function names), domain clustering uses lineage dataset subjects, and a documentation-
presence scan flags undocumented files. Day-One synthesis is skipped.

---

## Phase 3 Highlights

The upgraded Semanticist now adds:

- semantic provenance fields on module nodes:
  `semantic_model_used`, `semantic_prompt_version`, `semantic_generation_timestamp`,
  `semantic_fallback_used`
- structured semantic evidence objects with:
  `source_phase`, `file_path`, `line_start`, `line_end`, `extraction_method`,
  `description`
- Day-One answers with structured citations, line ranges, and evidence types
- `semantic_hotspots.json`, which ranks onboarding-critical modules using fused signals
- `semantic_review_queue.json`, which flags modules that still need human review

When exact evidence cannot be grounded honestly, the pipeline leaves line ranges as
`null` and surfaces the result in the review queue instead of fabricating citations.

---

## Phase 4 Highlights

Phase 4 adds the interactive repository question-answering layer:

- `Archivist` loads Phase 1-3 artifacts, generates `CODEBASE.md`, and writes `onboarding_brief.md`
- `Navigator` answers repository questions from the saved module graph, lineage graph, and semantic outputs
- answers include a grounded explanation, structured citations, and a confidence score
- query logs are stored under `.cartography/<repo-name>/queries/`
- query routing uses the existing local Ollama setup:
  - `qwen3-coder:480b-cloud` for code reasoning and dependency analysis
  - `deepseek-v3.1:671b-cloud` for final explanation synthesis

Navigator answers keep the citations grounded in retrieved evidence. The LLM can improve wording
and synthesis, but it does not invent file paths or line ranges.

---

## Phase 5 Highlights

Phase 5 adds a modern Streamlit dashboard on top of the saved artifacts:

- `Repository Overview` for executive metrics, drift summary, and hotspot charts
- `Phase 1 - Structure` for the module graph, dependency focus diagrams, hubs, and git velocity
- `Phase 2 - Data Flow` for the lineage map, dataset explorer, upstream/downstream traces, and SQL snippets
- `Phase 3 - Semantic Insights` for purpose statements, domains, hotspot ranking, review queue, and reading order
- `Phase 4 - Query Navigator` for interactive grounded questions using the existing Navigator
- `Reports` for `phase1.md` through `phase5.md`, plus `CODEBASE.md` and `onboarding_brief.md`

The dashboard is artifact-driven: it loads `.cartography/` outputs, highlights provenance, and
shows evidence snippets with file paths and line ranges instead of rescanning the repository.

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

### Cross-phase reports — `<repo-name>/`

| File | Description |
|------|-------------|
| `cartography_trace.jsonl` | Audit log: one JSON line per agent action |
| `blind_spots.json` | Metric-based JSON: parse failures, dynamic transforms, low-confidence datasets + edges |
| `high_risk_areas.json` | Metric-based JSON: hubs, cycles, high-velocity files, fan-out transforms, dynamic hotspots |

### Phase 3 - `semantics/`

| File | Description |
|------|-------------|
| `semantic_enrichment.json` | Full semantic output for every module, including purpose, drift, structured evidence, and hotspot rankings |
| `semantic_index.json` | Compact lookup: module-to-purpose summary, domain membership, top hotspots, and top reading-order entries |
| `day_one_answers.json` | Five FDE Day-One Q&A with legacy `cited_files`, structured `citations`, line ranges, and evidence types |
| `reading_order.json` | Ranked onboarding guide: every module ordered by domain importance, business logic score, and hotspot context |
| `semanticist_stats.json` | Run stats: LLM calls, token usage, elapsed time, drift count, documentation-missing count, hotspot count, and review queue count |
| `semantic_review_queue.json` | Canonical semantic review queue with modules, reasons, scores, and evidence |

### Additional Phase 3 outputs

| File | Location | Description |
|------|----------|-------------|
| `semantic_hotspots.json` | `.cartography/<repo-name>/` | Ranked hotspot fusion output combining PageRank, git velocity, lineage fan-out, and business logic score |

### Phase 4 - `<repo-name>/`

| File | Location | Description |
|------|----------|-------------|
| `CODEBASE.md` | repo root | Living context summary for AI-agent injection and human onboarding |
| `onboarding_brief.md` | repo root | Markdown version of the Day-One answers with citations |
| `queries/` | repo root | Structured query logs for every Navigator answer |

### Expected output for jaffle-shop

Since jaffle-shop is primarily SQL + YAML (a dbt project), Phase 1 + Phase 2 produce:
- **33 files parsed via AST** — Python, YAML, SQL (`tree-sitter-sql`), and JS/TS have dedicated grammars; **0 grammar-missing files**
- **Module classification**: 33/33 modules assigned a named role: 13 mart, 12 staging, 6 config, 2 macro
- **SQL table references**: extracted via `tree-sitter-sql` AST
- **YAML files**: top-level keys and dbt source/seed/model declarations extracted
- **Import graph**: **11 DBT_REF edges** connecting mart models → staging models
- **PageRank hubs**: staging models (stg_products, stg_supplies, stg_orders) are top-3 hubs
- **Dataset classification**: 27 datasets — 12 source, 13 sink, 5 final models, 6 intermediate
- **Project type**: `dbt` (auto-detected from `dbt_project.yml`)
- **Risk reports**: `blind_spots.json` (8 total blind spots — 2 macros flagged dynamic), `high_risk_areas.json`
- **Output location**: `.cartography/jaffle-shop/` (auto-derived subfolder)
- **Purpose statements**: 31/31 modules enriched with purpose statements and semantic provenance fields
- **Structured evidence**: semantic outputs now carry file paths, line ranges, extraction methods, and source phases
- **Domain clusters**: semantic clustering remains part of the Phase 3 output
- **Doc drift**: drift and documentation-missing signals are emitted per module
- **Reading order**: 33-item onboarding guide written to `reading_order.json`
- **Day-One answers**: 5 FDE Day-One Q&A generated with structured citations and legacy `cited_files`
- **Hotspot fusion**: `.cartography/jaffle-shop/semantic_hotspots.json` ranks onboarding-critical modules
- **Semantic review queue**: `.cartography/jaffle-shop/semantics/semantic_review_queue.json` lists modules that need human follow-up

---

### Phase 2 reference update (March 13, 2026)

- The current jaffle-shop run produces **25 datasets**, **13 SQL transformations**, and **30 lineage edges**.
- `blind_spots.json` is now **0 across all categories** because dbt macros are excluded from executable lineage instead of being represented as low-confidence pseudo-models.
- `hydrologist_stats.json` records `macro_sql_files_skipped = 2`, which keeps macro utility files visible in Phase 1 without polluting Phase 2 lineage.
- `cartographer lineage-summary ./.cartography/jaffle-shop --node source.ecom.raw_orders` prints the saved source/sink sets plus the downstream blast radius for a concrete dataset.

## Project Structure

Phase 4 adds `src/agents/archivist.py`, `src/agents/navigator.py`, and
`reports/phase4.md` on top of the existing Surveyor, Hydrologist, and Semanticist pipeline.

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
│   ├── python_dataflow.py     # [Phase 2] pandas/spark read/write + SQL execution detection
│   ├── semantic_extractor.py  # [Phase 3] LLM purpose extraction + business logic scoring
│   ├── domain_clusterer.py    # [Phase 3] Heuristic + LLM domain clustering
│   └── doc_drift_detector.py  # [Phase 3] Documentation drift detection
├── agents/
│   ├── surveyor.py            # Surveyor: file scan → graph → PageRank/SCC
│   ├── hydrologist.py         # [Phase 2] Hydrologist: data lineage → datasets + transforms
│   └── semanticist.py         # [Phase 3] Semanticist: LLM purpose → domains → drift → Day-One
├── graph/
│   ├── knowledge_graph.py     # NetworkX wrapper + analytics + PNG/HTML visualization
│   ├── graph_viz.py           # Module graph PNG (role rings, confidence-scaled edges)
│   ├── graph_analytics.py     # PageRank, SCC, degree stats
│   ├── enrichment.py          # Module + dataset classification; confidence scoring
│   └── reporting.py           # Blind-spots + high-risk markdown/JSON report writers
├── llm/                       # [Phase 3] LLM integration layer
│   ├── ollama_client.py       # Ollama REST client, ContextWindowBudget
│   ├── model_router.py        # Task-aware model routing (qwen3-coder vs deepseek-v3.1)
│   └── prompt_builder.py      # Structured prompt templates for all semantic tasks
└── utils/
    ├── repo_loader.py          # Local path or GitHub URL → local Path (--full-history support)
    ├── file_inventory.py       # Walk repo, filter by language
    └── git_tools.py            # git log velocity per file

reports/
├── phase1.md                  # Phase 1 feature reference (classification, evidence, visualization)
├── phase2.md                  # Phase 2 feature reference (lineage, blind spots, high-risk)
└── phase3.md                  # Phase 3 feature reference (LLM semantics, domains, Day-One)
```

---

Phase 5 additions to the structure:

```text
streamlit_app.py                # Phase 5 dashboard entrypoint

src/dashboard/
|-- __init__.py                 # Dashboard package marker
`-- data_layer.py               # Artifact loading, evidence lookup, graph prep, query handoff

src/agents/archivist.py         # Phase 4 artifact retrieval
src/agents/navigator.py         # Phase 4 grounded query answering

reports/phase4.md               # Phase 4 feature reference
reports/phase5.md               # Phase 5 dashboard reference
```

## Output Directory Structure

By default artifacts are written to `.cartography/<repo-name>/` so multiple repos can coexist:

```
.cartography/
└── jaffle-shop/                    # derived from the target path or URL
    ├── cartography_trace.jsonl     # shared audit log (all agents)
    ├── blind_spots.json            # metric-based blind-spot signals
    ├── high_risk_areas.json        # metric-based risk signals
    ├── semantic_hotspots.json      # Phase 3 hotspot fusion artifact
    ├── module_graph/               # Phase 1 (Surveyor) artifacts
    │   ├── module_graph.json
    │   ├── module_graph_modules.json
    │   ├── module_graph.png
    │   └── surveyor_stats.json
    ├── data_lineage/               # Phase 2 (Hydrologist) artifacts
    │   ├── lineage_graph.json
    │   ├── lineage_graph.html
    │   └── hydrologist_stats.json
    └── semantics/                  # Phase 3 (Semanticist) artifacts
        ├── semantic_enrichment.json
        ├── semantic_index.json
        ├── day_one_answers.json
        ├── semantic_review_queue.json
        ├── reading_order.json
        └── semanticist_stats.json
```

To write to an exact directory (bypass auto-subfolder): `--output-dir ./my-output`

Phase 4 also writes:
- `.cartography/<repo-name>/CODEBASE.md`
- `.cartography/<repo-name>/onboarding_brief.md`
- `.cartography/<repo-name>/queries/*.json`

To analyse multiple repos side-by-side:
```bash
uv run cartographer analyze /path/to/repo-a    # → .cartography/repo-a/
uv run cartographer analyze /path/to/repo-b    # → .cartography/repo-b/
uv run cartographer analyze .                  # → .cartography/brownfield-cartographer/
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

### Blind Spots (`blind_spots.json`)

Surfaces everything the pipeline could not fully resolve, as a metric-based JSON:
- **`summary`** — counts for every category
- **`parse_failures`** — files where the AST parser errored
- **`structurally_empty_files`** — parsed OK but produced zero symbols
- **`dynamic_transformations`** — Jinja/SQL not fully resolvable
- **`low_confidence_datasets`** — datasets with confidence < 0.70
- **`low_confidence_edges`** — PRODUCES/CONSUMES edges with confidence < 0.70

### High-Risk Areas (`high_risk_areas.json`)

Aggregated risk signals for onboarding engineers:
- **High-velocity files** — most git commits in the velocity window (churn risk)
- **Top hubs** — highest-PageRank modules (single-point-of-failure risk)
- **Circular dependencies** — SCCs with size > 1 (refactoring debt)
- **High fan-out transforms** — produce many output datasets
- **Dynamic hotspots** — incomplete lineage, needs manual tracing

> See [reports/phase1.md](reports/phase1.md), [reports/phase2.md](reports/phase2.md),
> [reports/phase3.md](reports/phase3.md), [reports/phase4.md](reports/phase4.md), and
> [reports/phase5.md](reports/phase5.md)
> for full field references, interpretation guides, and query examples from the jaffle-shop run.

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

## Inspecting The New Semantic And Query Outputs

After a run, these commands are the quickest way to inspect the new Phase 3 outputs:

```bash
cat .cartography/<repo-name>/semantic_hotspots.json
cat .cartography/<repo-name>/semantics/day_one_answers.json
cat .cartography/<repo-name>/module_graph/module_graph_modules.json
cat .cartography/<repo-name>/semantics/semantic_review_queue.json
cat .cartography/<repo-name>/CODEBASE.md
cat .cartography/<repo-name>/onboarding_brief.md
ls .cartography/<repo-name>/queries
```

What to look for:

- provenance fields on module nodes:
  `semantic_model_used`, `semantic_prompt_version`,
  `semantic_generation_timestamp`, `semantic_fallback_used`
- structured `semantic_evidence` entries with grounded file and line metadata
- Day-One `citations` objects with `file_path`, `line_start`, `line_end`, and `evidence_type`
- hotspot ranking breakdowns in `semantic_hotspots.json`
- review queue entries in `semantic_review_queue.json`
- living context in `CODEBASE.md`
- onboarding markdown in `onboarding_brief.md`
- per-question structured logs in `queries/`

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

# Run tests
python -m unittest tests.test_phase3_semantics -v
```

---

## Roadmap

| Phase | Agent | What it does |
|-------|-------|--------------|
| 1 | Surveyor | File scan, import graph, PageRank hubs, git velocity, project-type detection, module classification, and edge evidence |
| 2 | Hydrologist | Data lineage: datasets, transformations, PRODUCES/CONSUMES edges, dataset classification, blind-spots, and high-risk reports |
| 3 | Semanticist | Purpose annotation, structured evidence, Day-One synthesis, hotspot fusion, and semantic review queue |
| 4 | Archivist + Navigator | Semantic search and Q&A over the knowledge graph |
