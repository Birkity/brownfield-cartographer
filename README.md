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
| SQL | `.sql` | regex (dbt Jinja) | `{{ ref() }}` / `{{ source() }}` model dependencies |
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
- `tree-sitter` + language grammars (Python, YAML, JavaScript, TypeScript)
- `networkx` for graph analytics
- `pydantic` for typed data models
- `click` + `rich` for the CLI
- `sqlglot` for SQL lineage parsing (Phase 2)
- `pyyaml` for YAML config analysis (Phase 2)
- `pyvis` for interactive lineage visualization (Phase 2)

> **Java, Kotlin, Scala, Go, Rust, C#, Ruby, Shell** are supported out of the box via regex-based
> import extraction — no additional grammar packages needed for these languages.

> **Note on `tree-sitter-sql`**: The SQL tree-sitter grammar is optional.
> If you want it: `uv pip install -e ".[sql-grammar]"`.
> Without it, SQL files are still inventoried and line-counted, but table
> references are not extracted via AST (Phase 2 uses sqlglot for this anyway).

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

| File | Phase | Description |
|------|-------|-------------|
| `module_graph.json` | 1 | NetworkX node-link JSON of the import graph (IMPORTS + DBT_REF edges) |
| `module_graph_modules.json` | 1 | Full ModuleNode records (imports, dbt_refs, functions, classes, complexity, velocity) |
| `cartography_trace.jsonl` | 1+2 | Audit log: one JSON line per agent action |
| `surveyor_stats.json` | 1 | Summary: hub counts, import edges, dbt_ref_edges, cycles, velocity, elapsed, **project_type** |
| `module_graph.png` | 1 | Dark-theme graph PNG (matplotlib, 160 DPI, degree-scaled nodes, neon palette) |
| `lineage_graph.json` | 2 | Datasets, transformations, and PRODUCES/CONSUMES edges |
| `lineage_graph.html` | 2 | Interactive PyVis lineage map (dark theme, hover tooltips, physics layout) |
| `hydrologist_stats.json` | 2 | Phase 2 summary: dataset counts by type, transformation counts, edge stats |

### Expected output for jaffle-shop

Since jaffle-shop is primarily SQL + YAML (a dbt project), Phase 1 now finds:
- **SQL files**: inventoried, line-counted; **`{{ ref('model') }}` edges extracted via regex** (no SQL grammar needed)
- **YAML files**: inventoried, top-level keys extracted
- **Python files**: few or none (dbt projects are mostly SQL)
- **Import graph**: **11 DBT_REF edges** (was 0 before) connecting marts → staging models
- **PageRank**: staging models correctly identified as architectural hubs
- **Complexity scores**: populated for all Python files (0.0 for SQL/YAML)
- **Project type**: `dbt` (detected from `dbt_project.yml`)

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
│   └── knowledge_graph.py     # NetworkX wrapper + analytics + PNG/HTML visualization
└── utils/
    ├── repo_loader.py          # Local path or GitHub URL → local Path (--full-history support)
    ├── file_inventory.py       # Walk repo, filter by language
    └── git_tools.py            # git log velocity per file
```

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
| 1 ✅ | Surveyor | File scan, import graph, PageRank hubs, git velocity, project-type detection |
| 2 ✅ | Hydrologist | Data lineage — datasets, transformations, PRODUCES/CONSUMES edges, interactive HTML |
| 3 🔜 | Semanticist | LLM-powered purpose annotation for modules and datasets |
| 4 🔜 | Archivist + Navigator | Semantic search, Q&A chat over the knowledge graph |
- `src/models/nodes.py`: `DatasetNode`, `TransformationNode` schemas ready
- `src/analyzers/` — add `sql_lineage.py` (sqlglot-based) and `dag_config_parser.py` (Airflow/dbt YAML)

See the `# TODO Phase 2` comments throughout the codebase for precise hook locations.
