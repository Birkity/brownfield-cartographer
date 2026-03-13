# Phase 3 - Semanticist: Production Semantic Analysis

## Overview

The Semanticist is the third agent in the Brownfield Cartographer pipeline. It enriches
the Phase 1 module graph and Phase 2 lineage graph with semantic understanding:

- purpose statements for modules
- business logic scoring
- semantic domains
- documentation drift signals
- onboarding-oriented Day-One answers
- hotspot fusion rankings
- a human review queue for uncertain or weakly grounded results

Phase 3 keeps the existing pipeline shape intact. `run_phase3()` remains the single
integration point, and all new outputs are emitted as extensions of the current
artifact set rather than separate scripts.

---

## What Changed In The Production Upgrade

This upgrade adds five production-quality capabilities:

1. Semantic provenance tracking on module nodes
2. Structured semantic evidence objects instead of plain-text evidence
3. Day-One answers with line-range citations and evidence types
4. A hotspot fusion score across architecture, change activity, lineage fan-out, and business logic
5. A semantic review queue for modules that still need human judgment

The system remains honest about uncertainty. When line ranges cannot be proven, the
pipeline emits `null` ranges and surfaces the module in the review queue instead of
inventing evidence.

---

## Runtime Outputs

Phase 3 writes or enriches these artifacts under `.cartography/<repo-name>/`:

| File | Location | Description |
|------|----------|-------------|
| `semantic_enrichment.json` | `semantics/` | Full purpose extraction, domain clustering, drift results, hotspot rankings, and review queue |
| `semantic_index.json` | `semantics/` | Compact lookup index with purpose summaries, domain membership, top hotspots, and reading order |
| `day_one_answers.json` | `semantics/` | Five onboarding answers with cited files, structured citations, and confidence |
| `reading_order.json` | `semantics/` | Ranked onboarding path across all modules |
| `semanticist_stats.json` | `semantics/` | Run metrics for Phase 3 |
| `semantic_hotspots.json` | repo root | Ranked hotspot fusion output for onboarding prioritization |
| `semantic_review_queue.md` | `reports/` in this repo | Human review queue generated from the latest Phase 3 run |

For the default jaffle-shop run, the new artifacts are:

- `.cartography/jaffle-shop/semantic_hotspots.json`
- `reports/semantic_review_queue.md`

---

## Semantic Provenance

Phase 3 now persists semantic provenance directly on `ModuleNode` records:

| Field | Type | Description |
|------|------|-------------|
| `semantic_model_used` | `Optional[str]` | Model that generated the semantic result |
| `semantic_prompt_version` | `Optional[str]` | Prompt template version used for the result |
| `semantic_generation_timestamp` | `Optional[str]` | UTC timestamp when the semantic result was generated |
| `semantic_fallback_used` | `bool` | Whether the result came from heuristic fallback rather than an LLM response |

These fields make semantic outputs auditable and allow teams to distinguish between:

- old vs. new prompt generations
- LLM-backed vs. heuristic outputs
- mixed runs where some files succeeded and others fell back

### Example

From `.cartography/jaffle-shop/module_graph/module_graph_modules.json`:

```json
{
  "file_path": ".pre-commit-config.yaml",
  "semantic_model_used": "qwen3-coder:480b-cloud",
  "semantic_prompt_version": "phase3-purpose-v2",
  "semantic_generation_timestamp": "2026-03-13T08:30:57.116221Z",
  "semantic_fallback_used": false
}
```

---

## Structured Evidence

`semantic_evidence` is now stored as structured objects instead of free-form text.

### Evidence schema

```json
{
  "source_phase": "phase1|phase2|phase3",
  "file_path": "models/staging/stg_orders.sql",
  "line_start": 14,
  "line_end": 16,
  "extraction_method": "phase2_lineage",
  "description": "Field renaming: id -> order_id, store_id -> location_id, customer -> customer_id"
}
```

### Why this matters

- Evidence can be traced to a concrete file and line range
- Day-One answers can cite grounded evidence instead of vague file lists
- Weak evidence can be detected automatically for review-queue generation
- Existing graph consumers remain compatible because legacy string evidence is still accepted during deserialization

### Example

From `.cartography/jaffle-shop/semantic_hotspots.json`:

```json
{
  "source_phase": "phase3",
  "file_path": "models/staging/stg_products.sql",
  "line_start": 14,
  "line_end": 28,
  "extraction_method": "phase2_lineage",
  "description": "Column renaming and price conversion logic for downstream analytics"
}
```

---

## Evidence Resolution

Phase 3 now fuses evidence from multiple sources before writing semantic outputs:

- Phase 1 symbol and YAML-key locations
- Phase 2 transformation line ranges and lineage evidence
- LLM-returned evidence grounded against line-numbered source excerpts

This produces better citations for SQL, YAML, and config files while staying honest
about unresolved or dynamic cases.

---

## Day-One Answers With Citations

`day_one_answers.json` now preserves the original `cited_files` list and adds a
structured `citations` array for each answer.

### Citation schema

```json
{
  "source_phase": "phase2",
  "file_path": "models/marts/orders.sql",
  "line_start": 15,
  "line_end": 39,
  "extraction_method": "phase2_lineage",
  "description": "order_items_summary CTE computes supply costs, item counts and food/drink categorizations",
  "evidence_type": "semantic"
}
```

### Example

From `.cartography/jaffle-shop/semantics/day_one_answers.json`:

```json
{
  "question": "What does this codebase do at a high level?",
  "cited_files": [
    "models/marts/orders.yml",
    "models/staging/stg_orders.sql"
  ],
  "citations": [
    {
      "source_phase": "phase3",
      "file_path": "models/marts/orders.yml",
      "line_start": 1,
      "line_end": 3,
      "extraction_method": "phase1_symbol",
      "description": "Defines data mart for order analytics with financial totals and customer behavior metrics",
      "evidence_type": "semantic"
    },
    {
      "source_phase": "phase3",
      "file_path": "models/staging/stg_orders.sql",
      "line_start": 14,
      "line_end": 16,
      "extraction_method": "phase2_lineage",
      "description": "Transforms raw e-commerce order data for downstream analytics",
      "evidence_type": "semantic"
    }
  ]
}
```

Some synthesis-only answers may still contain unresolved summary references such as
`LINEAGE SUMMARY` or `BLIND SPOTS`. Those citations intentionally keep `line_start`
and `line_end` as `null` because there is no honest module-level span to attach.

---

## Hotspot Fusion Score

Phase 3 now computes `hotspot_fusion_score` for every module. The score is designed to
highlight the modules a new engineer should understand early.

### Signals

The score is the equal-weight average of four min-max normalized signals:

- Phase 1 PageRank
- git velocity
- Phase 2 lineage fan-out
- Phase 3 business logic score

### Output artifact

The ranked results are written to:

`<artifact_root>/semantic_hotspots.json`

### Example

From `.cartography/jaffle-shop/semantic_hotspots.json`:

```json
{
  "file_path": "models/staging/stg_products.sql",
  "hotspot_fusion_score": 0.694445,
  "signal_breakdown": {
    "pagerank": { "raw": 0.014667, "normalized": 1.0 },
    "git_velocity": { "raw": 0.0, "normalized": 0.0 },
    "lineage_fanout": { "raw": 3.0, "normalized": 1.0 },
    "business_logic_score": { "raw": 0.7, "normalized": 0.777778 }
  }
}
```

### Interpretation

- High PageRank means the module matters architecturally
- High git velocity means it changes often
- High lineage fan-out means downstream models depend on it
- High business logic score means it carries product or analytics meaning

In the jaffle-shop example run on March 13, 2026, git velocity remained `0.0` because
the temporary clone triggered a Git safe-directory warning. The hotspot artifact was
still generated successfully; that signal simply normalized to zero for the run.

---

## Semantic Review Queue

Phase 3 now generates a human review queue at:

`reports/semantic_review_queue.md`

Modules are added when one or more of these conditions hold:

- semantic confidence `< 0.60`
- drift level is `possible_drift` or `likely_drift`
- `documentation_missing = true`
- hotspot fusion score `>= 0.70` with weak evidence
- unresolved lineage cases tied to the module

### Example

Excerpt from `reports/semantic_review_queue.md`:

```md
## `models/staging/stg_orders.sql`

- Hotspot fusion score: `0.64`
- Semantic confidence: `0.95`
- Drift level: `likely_drift`
- Reasons: documentation drift (likely_drift)
```

This report gives teams a concrete handoff list instead of forcing them to inspect the
entire semantic corpus manually.

---

## Reading Order

The reading order still ranks modules for onboarding, but it now benefits from the
hotspot fusion work and richer evidence grounding. Each item includes:

- `step`
- `file_path`
- `domain`
- `purpose`
- `business_logic_score`
- `hotspot_fusion_score`
- `reason`

This keeps the onboarding path aligned with both semantic importance and graph reality.

---

## New Module Fields

Phase 3 now enriches `ModuleNode` with the following semantic fields:

| Field | Type | Description |
|------|------|-------------|
| `purpose_statement` | `Optional[str]` | Module purpose summary |
| `business_logic_score` | `float` | Business-logic intensity from `0.0` to `1.0` |
| `domain_cluster` | `Optional[str]` | Semantic domain |
| `doc_drift_detected` | `bool` | Whether documentation drift was detected |
| `doc_drift_level` | `Optional[str]` | `no_drift`, `possible_drift`, or `likely_drift` |
| `documentation_missing` | `bool` | Whether inline docs were missing |
| `semantic_confidence` | `float` | Confidence in the semantic result |
| `semantic_evidence` | `list[SemanticEvidence]` | Grounded evidence supporting semantic output |
| `semantic_model_used` | `Optional[str]` | Model used for semantic generation |
| `semantic_prompt_version` | `Optional[str]` | Prompt version used |
| `semantic_generation_timestamp` | `Optional[str]` | UTC timestamp of generation |
| `semantic_fallback_used` | `bool` | Whether heuristic fallback was used |
| `hotspot_fusion_score` | `float` | Fused onboarding-priority score |

---

## How To Run

Run the full pipeline against a target repository:

```bash
uv run cartographer analyze /path/to/repo
```

To inspect the new outputs:

```bash
cat .cartography/<repo-name>/semantic_hotspots.json
cat .cartography/<repo-name>/semantics/day_one_answers.json
cat reports/semantic_review_queue.md
```

To inspect provenance fields on module nodes:

```bash
cat .cartography/<repo-name>/module_graph/module_graph_modules.json
```

---

## Validation Notes

The March 13, 2026 jaffle-shop run produced:

- semantic provenance fields on module nodes
- structured semantic evidence objects
- Day-One answers with structured citations and line ranges where grounded
- `.cartography/jaffle-shop/semantic_hotspots.json`
- `reports/semantic_review_queue.md`

Targeted unit coverage was also added for:

- legacy evidence coercion
- hotspot scoring
- citation formatting
- citation backfilling
- review queue selection
