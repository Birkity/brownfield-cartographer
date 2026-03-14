# Phase 5 - Streamlit Dashboard

## Overview

Phase 5 adds a modern Streamlit dashboard that visualizes the saved Cartographer artifacts from
all previous phases. The dashboard is intentionally artifact-driven: it reads `.cartography/`
outputs, Phase 4 query logs, and the written reports instead of rescanning the repository.

This keeps the interface fast, grounded, and easy to use for both technical and non-technical
stakeholders.

---

## Architecture

Phase 5 is split into two layers:

1. `streamlit_app.py`
   - page layout
   - navigation
   - charts
   - embedded graph views
   - query UX

2. `src/dashboard/data_layer.py`
   - artifact discovery
   - graph loading
   - summary metrics
   - evidence lookup
   - focused Graphviz diagrams
   - source snippet loading
   - Phase 4 Navigator handoff

This separation keeps the UI thin and makes the dashboard behavior testable without requiring a
live Streamlit session.

---

## Dashboard Pages

### Repository Overview

Combines Phase 1 through Phase 4 signals into a non-technical landing page:

- total files
- datasets
- transformations
- semantic domains
- hotspot count
- documentation drift summary

Visuals:

- module-role distribution
- dataset-type donut chart
- semantic-domain business logic chart
- drift summary chart
- hotspot leaderboard

### Phase 1 - Structure

Uses the saved module graph and Surveyor stats to show:

- interactive module network from `module_graph.html`
- hub modules
- git velocity
- focused dependency diagram for a selected module
- evidence viewer with provenance and grounded citations

### Phase 2 - Data Flow

Uses the saved lineage graph to show:

- interactive lineage DAG from `lineage_graph.html`
- dataset explorer
- producers and consumers
- upstream and downstream impact
- representative SQL or transformation snippet
- blind-spot and risk context

### Phase 3 - Semantic Insights

Uses Semanticist outputs to show:

- purpose statements
- domain clusters
- business logic scores
- hotspot ranking
- documentation drift
- semantic review queue
- reading order for onboarding

### Phase 4 - Query Navigator

Provides a question box over the existing Navigator and Archivist:

- asks repository questions from saved artifacts
- returns answer, confidence, and citations
- displays evidence snippets with file paths and line ranges
- preserves query logging under `.cartography/<repo>/queries/`

### Reports

Brings the project reports and generated living-context artifacts together in one place:

- `phase1.md`
- `phase2.md`
- `phase3.md`
- `phase4.md`
- `phase5.md`
- `CODEBASE.md`
- `onboarding_brief.md`

---

## Evidence Model

The dashboard reuses the grounded evidence contract introduced in Phase 3 and Phase 4:

- `file_path`
- `line_start`
- `line_end`
- `source_phase`
- `description`

When a line range exists, the dashboard reads the corresponding source file and renders a
line-numbered code snippet. When the source file is not available from the saved run context, the
dashboard reports that honestly instead of fabricating code.

---

## Query Flow

1. User selects an artifact set from `.cartography/`
2. Dashboard loads the saved module graph, lineage graph, semantic outputs, reports, and query logs
3. User navigates through structural, lineage, semantic, or report views
4. If the user asks a question, the dashboard calls the existing `Navigator`
5. `Navigator` answers from saved artifacts and writes the normal Phase 4 query log
6. Dashboard renders the answer with grounded citations and evidence snippets

No phase reruns are triggered by the dashboard.

---

## Running

```bash
uv pip install -e .
streamlit run streamlit_app.py
```

The app auto-discovers available artifact roots under `.cartography/`.

---

## Verification

### Artifact-backed verification

The dashboard data layer was verified directly against the current
`.cartography/jaffle-shop` artifact set.

Observed counts from the saved run:

- files: `33`
- datasets: `25`
- transformations: `13`
- semantic domains: `5`
- hotspot count above 0.50: `4`
- documentation drift flags: `3`
- review queue items: `25`
- saved Phase 4 query logs: `4`

### Automated tests

Added:

- `tests/test_phase5_dashboard.py`

Covered behaviors:

- artifact loading
- overview metric derivation
- Graphviz focus diagram generation
- code snippet extraction with line numbers
- Phase 4 Navigator handoff via injectable query runner

### Environment caveat

The dashboard code is complete and the data layer is tested, but a live Streamlit launch could not
be executed inside this restricted environment because `streamlit` could not be fetched from the
package index during verification. `plotly` was installed successfully, and the app source was
syntax-checked with `py_compile`.

---

## Outcome

Phase 5 gives Brownfield Cartographer a polished, artifact-first interface for structure, lineage,
semantics, and grounded questions without changing the underlying analysis architecture.
