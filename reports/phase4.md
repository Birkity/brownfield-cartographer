# Phase 4 - Navigator and Archivist

## Overview

Phase 4 turns the Brownfield Cartographer into a queryable system instead of a write-once report generator.
It adds two runtime components on top of the existing `.cartography/<repo-name>/` artifacts:

- `Archivist`: loads saved Phase 1-3 artifacts, retrieves grounded evidence, and generates living context files
- `Navigator`: interprets repository questions, pulls evidence from the Archivist, and returns structured answers

The key design constraint is that Phase 4 does **not** rescan the repository. It answers from saved
module graph, lineage, and semantic artifacts.

---

## Navigator Architecture

The Navigator follows a retrieval-first flow:

1. classify the question into a supported intent
2. retrieve grounded evidence from saved artifacts
3. optionally use `qwen3-coder:480b-cloud` for technical reasoning over the retrieved evidence
4. use `deepseek-v3.1:671b-cloud` to synthesize the final explanation
5. return a structured answer with citations and confidence
6. log the query to `.cartography/<repo-name>/queries/`

Supported query patterns include:

- repository overview
- main pipelines / lineage questions
- business-logic hotspot questions
- blast radius questions
- implementation lookup
- module explanation

### Structured answer format

```json
{
  "question": "What breaks if source.ecom.raw_orders changes?",
  "answer": "Changes to source.ecom.raw_orders will directly break the staging model...",
  "confidence": 0.95,
  "citations": [
    {
      "file_path": "models/staging/stg_orders.sql",
      "line_start": 1,
      "line_end": 33,
      "evidence_type": "lineage",
      "source_phase": "phase2",
      "description": "dbt_model reads source.ecom.raw_orders and writes model.stg_orders"
    }
  ]
}
```

---

## Archivist Responsibilities

The Archivist is the artifact access layer for Phase 4.

It loads:

- `module_graph/module_graph.json`
- `module_graph/module_graph_modules.json`
- `data_lineage/lineage_graph.json`
- `semantics/semantic_index.json`
- `semantics/day_one_answers.json`
- `semantics/reading_order.json`
- `semantics/semantic_review_queue.json`
- `semantic_hotspots.json`
- `blind_spots.json`
- `high_risk_areas.json`

It also generates:

- `CODEBASE.md`
- `onboarding_brief.md`
- query logs under `queries/`

`CODEBASE.md` is the living context artifact for AI-agent injection.
`onboarding_brief.md` is the Markdown rendering of the Day-One answers with citations.

---

## Query Flow

### Example CLI usage

```bash
uv run cartographer query ./.cartography/jaffle-shop "What does this repository do?"
uv run cartographer query ./.cartography/jaffle-shop "What are the main data pipelines?"
uv run cartographer query ./.cartography/jaffle-shop "Which modules contain the most business logic?"
uv run cartographer query ./.cartography/jaffle-shop "What breaks if source.ecom.raw_orders changes?"
```

### Grounding rules

- citations are always sourced from saved graph or semantic evidence
- file paths and line ranges come from existing artifacts, not fresh repository scans
- if line ranges are unknown, they remain `null` rather than being invented
- LLM synthesis is allowed to improve explanation quality, but not to invent evidence

---

## Generated Outputs

Current default jaffle-shop outputs after Phase 4:

- `.cartography/jaffle-shop/CODEBASE.md`
- `.cartography/jaffle-shop/onboarding_brief.md`
- `.cartography/jaffle-shop/queries/`

### Example: `CODEBASE.md`

The generated file includes:

- architecture overview
- critical path
- data sources and sinks
- known debt
- high-velocity files

### Example: query log

From `.cartography/jaffle-shop/queries/20260313T134516798028Z-what-breaks-if-source.ecom.raw_orders-changes.json`:

```json
{
  "query_type": "blast_radius",
  "models_used": {
    "reasoning": "qwen3-coder:480b-cloud",
    "synthesis": "deepseek-v3.1:671b-cloud"
  }
}
```

---

## Example Answers

### What does this repository do?

Answer summary:

> This repository is a dbt-based analytics project for an e-commerce jaffle shop...

Grounding:

- `Taskfile.yml:9-40`
- `models/staging/__sources.yml:3-20`
- `models/marts/orders.yml:3`

### What are the main data pipelines?

Answer summary:

> Raw data originates from source tables and seed files, flows through staging models,
> and then into marts like `orders`, `order_items`, `customers`, and `locations`.

Grounding:

- `models/staging/__sources.yml:3-20`
- `models/staging/stg_orders.yml:3`
- `models/marts/orders.sql:15-39`

### Which modules contain the most business logic?

Answer summary:

> The strongest business logic is concentrated in `models/staging/stg_products.sql`,
> `models/staging/stg_orders.sql`, and `models/staging/stg_supplies.sql`.

Grounding:

- `models/staging/stg_products.sql:14-28`
- `models/staging/stg_orders.sql:14-27`
- `models/staging/stg_supplies.sql:14-16`

### What breaks if `source.ecom.raw_orders` changes?

Answer summary:

> The change breaks `model.stg_orders` first, then propagates to downstream marts
> including `model.customers`, `model.order_items`, and `model.orders`.

Grounding:

- `models/staging/stg_orders.sql:1-33`
- `models/marts/customers.sql:1-58`
- `models/marts/order_items.sql:1-66`
- `models/marts/orders.sql:1-77`

---

## Testing

### Automated tests

Phase 4 adds `tests/test_phase4_navigation.py` covering:

- Archivist generation of `CODEBASE.md` and `onboarding_brief.md`
- Navigator repository-overview answers with grounded citations
- blast-radius answers from saved artifacts only

Combined suite run on March 13, 2026:

```bash
.venv\Scripts\python.exe -m unittest tests.test_phase1_phase2 tests.test_phase1_phase2_rubric tests.test_phase3_semantics tests.test_phase4_navigation tests.test_git_tools -v
```

Result:

- `19 tests passed`

### End-to-end verification

Verified against `.cartography/jaffle-shop`:

- `CODEBASE.md` exists
- `onboarding_brief.md` exists
- query logs are written to `queries/`
- example Navigator queries return structured answers with grounded citations
- query execution loads saved artifacts and does not require source-file rescanning

Note:

- a full `cartographer analyze` run still spends most of its wall time in Phase 3 LLM inference
  for the 31 semantic-module passes on jaffle-shop
- Phase 4 itself was validated end to end against the saved Phase 1-3 artifacts, which is the
  intended runtime behavior for interactive querying
