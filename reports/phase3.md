# Phase 3 — Semanticist: LLM-Powered Semantic Analysis

## Overview

The **Semanticist** is the third agent in the Brownfield Cartographer pipeline. It uses
local LLM inference via [Ollama](https://ollama.com/) to extract **purpose statements**,
**business logic scores**, **domain clusters**, and **documentation drift** signals from
every module in the knowledge graph. It then synthesises a set of **Day-One onboarding
answers** — the five questions a new FDE would ask on their first day.

The result is a semantically enriched graph where every module carries a human-readable
purpose statement, a domain classification, and a drift flag — plus a compact index
designed for the Phase 4 Navigator agent.

---

## What the Semanticist does

| Step | Action |
|------|--------|
| 1 | **Init LLM**: connect to Ollama, discover available models, build a `ModelRouter` with task-specific routing |
| 2 | **Purpose extraction**: for each eligible module, read source code (smart-truncated) + graph context, prompt the LLM via batched or individual calls; heuristic fallback if no LLM |
| 3 | **Domain clustering**: group all modules into semantic domains using lineage dataset subjects — heuristic baseline (always works) + optional LLM refinement for semantic grouping |
| 4 | **Documentation drift / missing-doc scan**: when LLM is available, compare each module's inline docs against its purpose statement and flag stale/misleading docs; when no LLM, perform a documentation-presence scan and mark undocumented files |
| 5 | **Day-One synthesis**: build a comprehensive prompt with all Phase 1–3 evidence, generate answers to the five FDE Day-One questions |
| 6 | **Enrich graph**: write purpose, business_logic_score, domain_cluster, doc_drift back to every `ModuleNode` |
| 7 | **Reading order**: rank all modules by domain importance + business logic score to produce a step-by-step onboarding guide for new engineers |

---

## Model Routing

The Semanticist uses a **task-aware model router** that selects the best LLM for each job:

| Task type | Preferred model | Fallback | Rationale |
|-----------|----------------|----------|-----------|
| Purpose extraction | `qwen3-coder` | `deepseek-v3.1` | Code-focused reasoning |
| SQL explanation | `qwen3-coder` | `deepseek-v3.1` | Code-focused reasoning |
| Business logic scoring | `qwen3-coder` | `deepseek-v3.1` | Code-focused reasoning |
| Domain clustering | `deepseek-v3.1` | `qwen3-coder` | High-level synthesis |
| Doc drift detection | `deepseek-v3.1` | `qwen3-coder` | Comparative reasoning |
| Day-One synthesis | `deepseek-v3.1` | `qwen3-coder` | Multi-source synthesis |

The router tries the preferred model first, then falls back to the other if unavailable.
A `ContextWindowBudget` tracks cumulative token usage (prompt + eval) across all calls.

### Graceful degradation

If Ollama is not running or no models are available:
- Purpose extraction falls back to **heuristic** mode — every module gets a purpose statement generated from its role, dbt-refs, function names, and imports (no LLM required)
- Domain clustering falls back to **heuristic-only** mode: lineage dataset-subject extraction first, role-based grouping as secondary fallback
- Doc drift detection falls back to **documentation-presence scan** — each module is scanned for the presence of inline documentation; undocumented files receive `documentation_missing=True`
- Day-One synthesis is **skipped**

Phase 1 and Phase 2 artifacts remain fully intact in all cases.

---

## Output Artifacts

| File | Location | Description |
|------|----------|-------------|
| `semantic_enrichment.json` | `.cartography/semantics/` | Full purpose statements, domain clustering result, and doc drift results for every module |
| `semantic_index.json` | `.cartography/semantics/` | Compact lookup: module→purpose+score, domain→members, top 10 business logic hotspots, top 20 reading-order entries |
| `day_one_answers.json` | `.cartography/semantics/` | Five FDE Day-One Q&A with cited files and confidence scores |
| `reading_order.json` | `.cartography/semantics/` | Ranked onboarding guide listing every module ordered by domain importance and business logic score |
| `semanticist_stats.json` | `.cartography/semantics/` | Run statistics: LLM calls, token usage, elapsed time, drift count, documentation-missing count, reading-order item count |

---

## Purpose Extraction

For each module with ≥3 lines of code (excluding trivial YAML), the Semanticist:

1. Reads the source code with **smart language-aware truncation** (6,000 chars default):
   - *Python*: extracts skeleton — imports, class/def signatures, and docstrings
   - *SQL/YAML/others*: head (⅔) + tail (⅓) to preserve header context and final SELECT
2. Builds **graph context** from Phase 1+2: hub status, cycle membership, entry point flag, dbt refs, lineage edges, velocity
3. Builds an **imports summary** listing key dependencies
4. Sends to the LLM — large/hub files sent individually; **small files batched** (up to 4 per call) to reduce total call count
5. If no LLM is available, **heuristic purpose statements** are generated from metadata (role, dbt-refs, function names, YAML keys)

The LLM returns structured JSON per file:
- `purpose_statement` — one-sentence explanation of what the file does
- `business_logic_score` — 0.0 (infrastructure) to 1.0 (core business logic)
- `key_concepts` — list of domain concepts the file implements
- `evidence` — reasoning for the score
- `confidence` — LLM self-rated confidence (0.0–1.0)

Modules are processed **hub-first** — architecturally important files get purpose statements before less connected ones.

---

## Domain Clustering

### Heuristic baseline (always runs)

Groups modules first by **lineage dataset subjects**: for each SQL transformation, the
dominant subject noun is extracted from its dataset references (e.g. `model.stg_orders`
→ `orders`) and the module is assigned to an `"Orders Pipeline"` domain. Modules not
covered by any transformation fall back to role-based grouping:

| Role pattern | Domain name |
|-------------|-------------|
| staging | Data Staging |
| mart | Analytics & Marts |
| macro | Utility Macros |
| config | Configuration |
| seed | Seed Data |
| Other | General |

### LLM refinement (when available)

When the LLM is available, the Semanticist sends all module names + purpose statements
to the LLM and asks it to identify **semantic domains** — groups of modules that work
together on a specific business function. The LLM returns domain names, descriptions,
member lists, and reasoning.

---

## Documentation Drift Detection

For each module that has a purpose statement, the Semanticist performs one of two scans
depending on LLM availability:

**With LLM**: Extract all inline documentation (docstrings, SQL comments, YAML comments),
compare documentation against the LLM-generated purpose statement, and report a **drift level**:
- `no_drift` — docs accurately describe the code
- `possible_drift` — minor discrepancies or outdated references
- `likely_drift` — docs are misleading or significantly stale
- `documentation_missing=True` — no inline documentation found at all

**Without LLM**: Perform a documentation-presence scan only. Every module without
inline documentation receives `documentation_missing=True`. This lets teams identify
undocumented files even when no LLM is running.

---

## Reading Order

After all semantic analysis is complete, the Semanticist generates a **reading order** —
a ranked list of every module designed to help a new engineer navigate the codebase
systematically:

1. Domains are sorted by combined business-logic score (most impactful domain first)
2. Within each domain, modules are sorted by business-logic score descending
3. Each entry includes: `step`, `file_path`, `domain`, `purpose`, `business_logic_score`, `reason`

The `reason` field provides a short rationale (e.g. "core business logic; analytical output",
"architectural hub", "data foundation"). The full list is written to `reading_order.json`
and the top 20 entries are embedded in `semantic_index.json` for quick access.

---

## Day-One Synthesis

The Semanticist builds a comprehensive synthesis prompt containing:

- Project statistics (from Phase 1 surveyor stats)
- Top-10 modules ranked by business logic score + their purpose statements
- Lineage summary (from Phase 2 hydrologist stats)
- Blind spot and high-risk data
- Domain clustering results

It then asks the LLM to answer the **five Day-One questions** that every new FDE asks:

1. **What does this codebase do at a high level?**
2. **What are the main data flows and where does data come from?**
3. **What are the critical modules a new engineer should understand first?**
4. **Where are the highest-risk areas and technical debt?**
5. **What are the blind spots — areas where the analysis may be incomplete?**

Each answer includes **cited files** from the codebase and a confidence score.

---

## New Node Fields (ModuleNode)

Phase 3 extends `ModuleNode` with these semantic attributes:

| Field | Type | Description |
|-------|------|-------------|
| `purpose_statement` | `Optional[str]` | LLM-generated purpose sentence |
| `semantic_summary` | `Optional[str]` | Short domain summary |
| `business_logic_score` | `float` | 0.0 (infra) to 1.0 (core business) |
| `domain_cluster` | `Optional[str]` | Assigned semantic domain name |
| `doc_drift_detected` | `bool` | Whether drift was found |
| `doc_drift_level` | `Optional[str]` | no_drift / possible_drift / likely_drift |
| `semantic_confidence` | `float` | LLM confidence in the purpose extraction |
| `semantic_evidence` | `Optional[str]` | Reasoning behind the score |

---

## Sample Results: jaffle-shop

### Run statistics

| Metric | Value |
|--------|-------|
| Ollama available | Yes |
| Purpose statements | 31/31 modules |
| Domain clusters | 7 (LLM-refined) |
| Doc drift detections | 31 checked, 6 with drift |
| Files missing documentation | 22 |
| Reading order items | 33 |
| Day-One answers | 5 generated |
| LLM calls | 47 (13 individual + 5 batch + clustering + Day-One) |
| Prompt tokens | ~30,976 |
| Eval tokens | ~8,987 |
| Total elapsed | ~1,271s |

### Domain clusters discovered

| Domain | Members |
|--------|---------|
| Order Analytics | 4 |
| Customer Analytics | 2 |
| Product & Supply Chain | 4 |
| Data Ingestion & Staging | 6 |
| Infrastructure & Configuration | 5 |
| Data Validation & Quality | 7 |
| Location Management | 2 |
| Time Analytics | 1 |

### Day-One synthesis (sample)

**Q: What does this codebase do at a high level?**
> This is a dbt-based analytics platform for a food service/e-commerce business
> that transforms raw order, customer, product, and supply chain data into
> analytical models for business intelligence. It provides order analytics,
> customer segmentation, product catalog management, and supply chain analytics
> with a focus on revenue tracking, profitability analysis, and customer lifetime
> value.
> *— Confidence: 0.95, cited: order_items.yml, orders.sql, customers.yml, products.yml*

---

## Limitations

- **LLM latency**: individual purpose extraction is sequential; batching reduces call count but does not fully eliminate wait time for large repos
- **Model dependency**: Day-One synthesis and full LLM drift comparison require Ollama with at least one of `qwen3-coder` or `deepseek-v3.1` available (heuristic fallbacks cover all other steps)
- **Drift detection depth**: documentation-presence scan runs without LLM, but semantic drift comparison (detecting *misleading* docs) still requires an LLM call per file

---

## Improvements Implemented

| Improvement | Implementation |
|-------------|----------------|
| Batch purpose extraction | Small files (≤1,500 bytes) grouped in batches of 4 per LLM call via `BATCH_PURPOSE_EXTRACTION_PROMPT`; reduces call count by ~50–65% on typical dbt projects |
| Smart code truncation | Language-aware `_smart_truncate_code`: Python skeleton (imports + signatures), SQL/YAML head+tail; preserves structural context within the token budget |
| Heuristic purpose fallback | `_heuristic_purpose_statement` generates purpose from role, dbt-refs, function names, YAML keys — no LLM required; `documentation_missing` scan also runs without LLM |
| Lineage-aware domain clustering | `_extract_subject_from_dataset` strips role prefixes to extract subject nouns (`stg_orders` → `orders`); modules grouped as `"Orders Pipeline"` etc. before LLM refinement |
| Documentation-missing flagging | New `documentation_missing: bool` field on `DriftResult`; set by both LLM drift detection (no docs found) and heuristic doc-presence scan |
| Reading order for new engineers | `_compute_reading_order` ranks all modules: domain by combined BL score, within domain by individual BL score; written to `reading_order.json` and embedded in `semantic_index.json` |
