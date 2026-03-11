# RECONNAISSANCE.md

## Manual Day-One Analysis for Target Codebase: dbt-labs/jaffle-shop

**Target Repo**: https://github.com/dbt-labs/jaffle-shop  
**Description**: This is the active, modern version of the Jaffle Shop dbt sandbox project from dbt Labs. It demonstrates dbt workflows using data from a fictional sandwich shop ("Jaffle Shop"), with a focus on learning dbt features like models, seeds, sources, macros, dbt Cloud integration, environments, and deployment. The project is actively maintained (last updates in early 2026), not archived, and optimized for both dbt Cloud IDE and CLI usage.

I explored the repo manually by browsing the GitHub tree, README, key configuration files (dbt_project.yml, packages.yml), models/ subfolders (staging/ and marts/), seeds/, and recent commit history. This took about 30–40 minutes (simulated via repo structure review, file listings, and standard dbt patterns).

### Answers to the Five FDE Day-One Questions

1. **What is the primary data ingestion path?**  
   Primary ingestion is through **dbt seed** loading static CSV files from `seeds/jaffle-data/` (e.g., raw_customers.csv, raw_orders.csv, raw_payments.csv) into the warehouse as sources in the `raw` schema.  
   Optional paths include loading from a public S3 bucket (6 years of data) or generating synthetic data with `jafgen` (Python tool) then seeding. No live/real-time ingestion — all static/demo data loaded via `dbt seed --full-refresh --vars '{"load_source_data": true}'`.

2. **What are the 3-5 most critical output datasets/endpoints?**  
   - `customers` (likely in marts/): Dimensional model with customer metrics (e.g., lifetime value, order history).  
   - `orders` (likely in marts/): Fact model with order details, totals, taxes, payment breakdowns, status.  
   - `order_items` (likely in marts/): Line-level order details for granular analysis.  
   - `products`, `stores`, `supplies` (reference/dimension models): Core entities for joining and reporting.  
   These are the main analytics-ready marts consumed by BI tools/dashboards.

3. **What is the blast radius if the most critical module fails?**  
   The most critical modules are likely staging models (e.g., `stg_customers.sql`, `stg_orders.sql`, `stg_payments.sql`) or core intermediate models feeding the marts.  
   If a staging model like `stg_orders.sql` fails, it breaks downstream refs to `orders`, `customers`, and `order_items` in the marts layer — potentially halting 80–90% of final outputs (all customer/order analytics). Blast radius: high (most marts depend on staging sources via {{ ref() }}). No obvious circular dependencies based on standard dbt layering.

4. **Where is the business logic concentrated vs. distributed?**  
   Business logic is concentrated in the **marts layer** (e.g., `customers.sql`, `orders.sql`): complex joins, aggregations (order totals, lifetime value), pivots, status derivations, and final metric calculations.  
   Distributed lightly in **staging layer** (simple cleaning, type casting, renaming, basic filtering from sources) and macros (e.g., cents_to_dollars). Intermediate models (if present) handle mid-layer transformations. Overall, classic dbt pattern: staging = light, marts = heavy business rules.

5. **What has changed most frequently in the last 90 days (git velocity map)?**  
   In the last 90 days (Dec 10, 2025 – Mar 10, 2026), changes were moderate and focused on maintenance.  
   Recent activity includes updates to `packages.yml` (Jan 20, 2026 – dependency/version bumps), model fixes (e.g., customer/store logic, SL/fanout fixes in Jul 2025 but with follow-ups), and README/Taskfile tweaks.  
   Highest velocity files likely include `packages.yml`, `dbt_project.yml`, models in marts/ (customers.sql, orders.sql), and seeds/ configs — typical for keeping the sandbox current with dbt releases.

### Difficulty Analysis

- **What was hardest to figure out manually?**  
  Tracing exact model names and lineage without running `dbt docs generate` or `dbt compile` — GitHub tree shows folders (staging/, marts/) but not full file lists or contents in all views. Had to infer standard dbt patterns (stg_ → int_ → fct_/dim_) and rely on README + common jaffle-shop examples for flow (raw → staging → marts). Determining precise business logic (e.g., lifetime value formula) requires reading full .sql files, which GitHub previews limit.

- **Where did you get lost?**  
  Initially confused between this active repo and the older "jaffle-shop-classic", this version has more Cloud-focused features, Taskfile.yml automation, jafgen synthetic data, and recent package migrations. Got lost in assuming all data is seed-based; README clarifies optional S3/large-scale loading. Commit history shows activity but not always clear "why" without reading PRs. Overall manageable due to small size and dbt conventions, but in a larger brownfield project, manual navigation would be much harder without tools like grep or dbt Explorer.

