# Brownfield Cartographer

A multi-agent codebase intelligence system for rapid FDE onboarding in production environments.

Ingests any local repository or GitHub URL and produces a queryable knowledge graph of the system's architecture, data flows, and semantic structure.

---

## Phase Status

| Phase | Agent | Status |
|-------|-------|--------|
| 1 | Surveyor (Static Structure) | ✅ Complete |
| 2 | Hydrologist (Data Lineage) | 🔜 Planned |
| 3 | Semanticist (LLM Purpose Analysis) | 🔜 Planned |
| 4 | Archivist + Navigator | 🔜 Planned |

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
- `sqlglot` (ready for Phase 2 SQL lineage analysis)

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

| File | Description |
|------|-------------|
| `module_graph.json` | NetworkX node-link JSON of the import graph |
| `module_graph_modules.json` | Full ModuleNode records (imports, functions, classes, velocity) |
| `cartography_trace.jsonl` | Audit log: one JSON line per agent action |
| `surveyor_stats.json` | Summary statistics: hub counts, cycles, velocity, elapsed time |

### Expected output for jaffle-shop

Since jaffle-shop is primarily SQL + YAML (a dbt project), Phase 1 will find:
- **SQL files**: inventoried, line-counted; AST extraction limited without `tree-sitter-sql`
- **YAML files**: inventoried, top-level keys extracted
- **Python files**: few or none (dbt projects are mostly SQL)
- **Import graph**: sparse for SQL-only repos — this is expected; Phase 2 fills the lineage

---

## Project Structure

```
src/
├── cli.py                     # Click CLI (analyze + query commands)
├── orchestrator.py            # Pipeline wiring: Phase 1 entry point
├── models/
│   └── nodes.py               # Pydantic schemas: ModuleNode, FunctionNode, TraceEntry…
├── analyzers/
│   ├── language_router.py     # Extension → Language routing + skip logic
│   └── tree_sitter_analyzer.py# AST parsing for Python/SQL/YAML/JS/TS
├── agents/
│   └── surveyor.py            # Surveyor: file scan → graph → PageRank/SCC
├── graph/
│   └── knowledge_graph.py     # NetworkX wrapper + serialization
└── utils/
    ├── repo_loader.py          # Local path or GitHub URL → local Path
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

## Development

```bash
# Install with dev extras
uv pip install -e ".[dev]"

# Run tests (few exist at this stage)
uv run pytest tests/ -v
```

---

## Phase 2 TODOs (Hydrologist)

The following integration points are already stubbed in the code:

- `src/orchestrator.py`: `run_phase2()` chain after `run_phase1()`
- `src/graph/knowledge_graph.py`: `add_dataset_node()`, `add_produces_edge()`, `add_consumes_edge()`
- `src/models/nodes.py`: `DatasetNode`, `TransformationNode` schemas ready
- `src/analyzers/` — add `sql_lineage.py` (sqlglot-based) and `dag_config_parser.py` (Airflow/dbt YAML)

See the `# TODO Phase 2` comments throughout the codebase for precise hook locations.
