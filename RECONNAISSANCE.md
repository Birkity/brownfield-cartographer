# RECONNAISSANCE.md

## Manual Day-One Analysis for Target Codebase: dbt-labs/jaffle-shop-classic

**Target Repo**: https://github.com/dbt-labs/jaffle-shop-classic  
**Description**: This is the classic version of the Jaffle Shop dbt example project, a self-contained dbt project for testing and demonstrating dbt workflows. It models data from a fictional e-commerce store with raw data in CSV seeds, staged models, and final mart models for analytics.

I manually explored the repo by browsing the GitHub tree, reading key files like dbt_project.yml, schema.yml, and .sql models, and reviewing commit history. Exploration took approximately 30 minutes (simulated via web searches and page browses for structure and content).

### Answers to the Five FDE Day-One Questions

1. **What is the primary data ingestion path?**  
   The primary data ingestion path is through dbt seed command, which loads raw CSV files from the `seeds/` directory (raw_customers.csv, raw_orders.csv, raw_payments.csv) into warehouse tables as sources (raw.jaffle_shop.customers, raw.jaffle_shop.orders, raw.stripe.payments). These represent replicated app data (customers and orders from "jaffle_shop" source, payments from "stripe" source). No live ingestion; it's static seeds for demo purposes.

2. **What are the 3-5 most critical output datasets/endpoints?**  
   - `customers`: A dimensional model joining staged customers with aggregated orders and payments to compute metrics like first_order_date, number_of_orders, and customer_lifetime_value.  
   - `orders`: A fact model with order details, status, and payment breakdowns by method (e.g., credit_card_amount, total_amount).  
   These are the main outputs in `models/marts/` ready for analytics. No other critical ones in this small repo; perhaps implied endpoints like BI dashboards consuming these.

3. **What is the blast radius if the most critical module fails?**  
   The most critical module is likely `stg_payments.sql` (staging payments data), as it feeds into both `orders.sql` (for payment aggregations) and indirectly `customers.sql` (via orders for lifetime value). If it fails, `orders` and `customers` models will fail during dbt run, breaking all downstream analytics. Blast radius: entire mart layer (100% of outputs). Circular deps: none visible.

4. **Where is the business logic concentrated vs. distributed?**  
   Business logic is concentrated in the mart models (`customers.sql` and `orders.sql`): complex joins, aggregations (e.g., customer lifetime value, payment method pivots), and derivations like order status filtering. Distributed in staging models (`stg_customers.sql`, `stg_orders.sql`, `stg_payments.sql`): simple renaming, type casting (e.g., amount / 100), and basic selects from sources. No heavy logic in macros or analyses.

5. **What has changed most frequently in the last 90 days (git velocity map)?**  
   No changes in the last 90 days (from Dec 10, 2025, to March 10, 2026). The repo was archived on Feb 10, 2025, making it read-only. Last commit was April 18, 2024. Historically, changes were infrequent, frequent files include README.md, dbt_project.yml, and model .sql files like customers.sql (updates for features/demos).

### Difficulty Analysis

- **What was hardest to figure out manually?**  
  Tracing the full data lineage and dependencies without running `dbt docs generate` or `dbt compile`. I had to manually read each .sql file to see {{ ref() }} and {{ source() }} calls, then map them to sources in schema.yml. Understanding business metrics (e.g., lifetime value calculation) required parsing complex CTEs in customers.sql.

- **Where did you get lost?**  
  Initially confused file paths (e.g., schema.yml vs. src_jaffle_shop.yml, some docs show variations).  Commit history was easy (no recent activity), but assessing velocity in an archived repo felt irrelevant—had to note zero changes. Overall, small repo made it manageable, but in a larger brownfield, manual grep/search would be tedious.