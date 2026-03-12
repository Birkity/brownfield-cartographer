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
| 2 | **Purpose extraction**: for each eligible module, read source code + graph context, prompt the LLM to produce a purpose statement, business logic score (0–1), key concepts, and confidence |
| 3 | **Domain clustering**: group all modules into semantic domains — heuristic baseline (always works) + optional LLM refinement for semantic grouping |
| 4 | **Documentation drift**: compare each module's inline documentation against its purpose statement, flag stale/misleading docs |
| 5 | **Day-One synthesis**: build a comprehensive prompt with all Phase 1–3 evidence, generate answers to the five FDE Day-One questions |
| 6 | **Enrich graph**: write purpose, business_logic_score, domain_cluster, doc_drift back to every `ModuleNode` |

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
- Purpose extraction is **skipped** (modules keep `purpose_statement = None`)
- Domain clustering falls back to **heuristic-only** mode (role-based grouping)
- Doc drift detection is **skipped**
- Day-One synthesis is **skipped**

Phase 1 and Phase 2 artifacts remain fully intact in all cases.

---

## Output Artifacts

| File | Location | Description |
|------|----------|-------------|
| `semantic_enrichment.json` | `.cartography/semantics/` | Full purpose statements, domain clustering result, and doc drift results for every module |
| `semantic_index.json` | `.cartography/semantics/` | Compact lookup: module→purpose+score, domain→members, top 10 business logic hotspots |
| `day_one_answers.json` | `.cartography/semantics/` | Five FDE Day-One Q&A with cited files and confidence scores |
| `semanticist_stats.json` | `.cartography/semantics/` | Run statistics: LLM calls, token usage, elapsed time, drift count |

---

## Purpose Extraction

For each module with ≥3 lines of code (excluding trivial YAML), the Semanticist:

1. Reads the source code (truncated to 6,000 chars if larger)
2. Builds **graph context** from Phase 1+2: hub status, cycle membership, entry point flag, dbt refs, lineage edges, velocity
3. Builds an **imports summary** listing key dependencies
4. Prompts the LLM to return structured JSON:
   - `purpose_statement` — one-sentence explanation of what the file does
   - `business_logic_score` — 0.0 (infrastructure) to 1.0 (core business logic)
   - `key_concepts` — list of domain concepts the file implements
   - `evidence` — reasoning for the score
   - `confidence` — LLM self-rated confidence (0.0–1.0)

Modules are processed **hub-first** — architecturally important files get purpose statements before less connected ones.

---

## Domain Clustering

### Heuristic baseline (always runs)

Groups modules by their **role** (assigned in Phase 1 enrichment):

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

For each module that has inline documentation (docstrings, SQL comments, YAML comments):

1. Extract all documentation text using language-specific rules
2. Compare documentation against the LLM-generated purpose statement
3. Report a **drift level**:
   - `no_drift` — docs accurately describe the code
   - `possible_drift` — minor discrepancies or outdated references
   - `likely_drift` — docs are misleading or significantly stale

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
| Domain clusters | 8 (LLM-refined) |
| Doc drift detections | 31 checked, 5 with drift |
| Day-One answers | 5 generated |
| LLM calls | 42 |
| Prompt tokens | ~25,412 |
| Eval tokens | ~6,763 |
| Total elapsed | ~938s |

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

- **LLM latency**: purpose extraction is sequential (one file at a time) — large repos may take several minutes
- **Token budget**: source code is truncated to 6,000 characters; very large files may lose context
- **Model dependency**: full analysis requires Ollama running locally with at least one of qwen3-coder or deepseek-v3.1 available
- **Heuristic clustering**: without LLM, domain clusters are purely role-based (staging/mart/config/etc.)
- **Drift detection**: only checks files with extractable documentation; files without comments get `has_documentation=False` and are skipped for LLM drift analysis

---

## Future Improvements

- Batch purpose extraction (multiple files per prompt) to reduce LLM call count
- Cross-reference domain clusters with lineage graph for data-flow-aware grouping
- Track semantic drift over time (compare purpose statements across git revisions)
- Generate a "reading order" for new engineers based on dependency + business logic score ranking
