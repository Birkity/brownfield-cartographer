# Semantic Review Queue

Modules that need human review after the latest Phase 3 run.

## `models/staging/stg_orders.sql`

- Hotspot fusion score: `0.64`
- Semantic confidence: `0.95`
- Drift level: `likely_drift`
- Reasons: documentation drift (likely_drift)

Evidence:
- `models/staging/stg_orders.sql` (lines 14-16, semantic): Field renaming: id → order_id, store_id → location_id, customer → customer_id
- `models/staging/stg_orders.sql` (lines 19-24, semantic): Currency conversion from cents to dollars using cents_to_dollars macro

## `models/marts/order_items.sql`

- Hotspot fusion score: `0.52`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/order_items.sql` (lines 43-54, semantic): Joined query combining order_items, orders, products, and order_supplies_summary with key business fields
- `models/marts/order_items.sql` (lines 30-37, semantic): Aggregation of supply costs by product_id in order_supplies_summary CTE

## `models/marts/orders.sql`

- Hotspot fusion score: `0.49`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/orders.sql` (lines 15-39, semantic): order_items_summary CTE computes supply costs, item counts and food/drink categorizations
- `models/marts/orders.sql` (lines 42-61, semantic): compute_booleans CTE joins orders with item summaries and derives business flags like is_food_order

## `macros/cents_to_dollars.sql`

- Hotspot fusion score: `0.33`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation, unresolved lineage case

Evidence:
- `macros/cents_to_dollars.sql` (lines 3-21, semantic): Macro definition shows conversion from cents to dollars with database-specific implementations for numeric formatting
- `macros/cents_to_dollars.sql` (lines 1-21, semantic): Transformation dbt_model reads none and writes model.cents_to_dollars

## `macros/generate_schema_name.sql`

- Hotspot fusion score: `0.32`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation, unresolved lineage case

Evidence:
- `macros/generate_schema_name.sql` (lines 1-23, semantic): Conditional logic for schema routing based on node.resource_type and target.name with explicit comments about seed data placement and production naming conventions
- `macros/generate_schema_name.sql` (lines 1-23, semantic): Transformation dbt_model reads none and writes model.generate_schema_name

## `models/marts/customers.sql`

- Hotspot fusion score: `0.31`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/customers.sql` (lines 18-26, semantic): Selects key customer metrics including lifetime orders, spend, and repeat purchase indicators for business analysis.
- `models/marts/customers.sql` (lines 46-49, semantic): Calculates customer_type field to classify customers as new or returning for marketing segmentation.

## `models/marts/locations.sql`

- Hotspot fusion score: `0.31`
- Semantic confidence: `0.80`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/locations.sql` (line 5, semantic): SQL query selects all location data from stg_locations reference, indicating location data management
- `models/marts/locations.sql` (lines 1-9, semantic): Transformation dbt_model reads model.stg_locations and writes model.locations

## `models/marts/order_items.yml`

- Hotspot fusion score: `0.25`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/order_items.yml` (lines 1-174, semantic): YAML structure containing models, unit_tests, semantic_models, metrics, and saved_queries sections defining business logic for order items
- `models/marts/order_items.yml` (lines 43-87, semantic): Semantic model definition with entities, dimensions, and measures including revenue calculations

## `models/marts/orders.yml`

- Hotspot fusion score: `0.24`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/orders.yml` (lines 1-3, semantic): models section defines the orders mart with description indicating key order details and granularity
- `models/marts/orders.yml` (lines 32-77, semantic): unit_tests section contains test cases validating order item boolean conversions

## `models/marts/customers.yml`

- Hotspot fusion score: `0.22`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/customers.yml` (lines 1-104, semantic): YAML configuration defining customer mart with columns for lifetime spend, order counts, and customer type classification
- `models/marts/customers.yml` (lines 33-71, semantic): Semantic model defining customer entities, dimensions, and measures for analytics

## `models/marts/locations.yml`

- Hotspot fusion score: `0.17`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/locations.yml` (lines 3-4, semantic): Description states 'Location dimension table. The grain of the table is one row per location.' establishing standardized reporting structure.
- `models/marts/locations.yml` (lines 21-24, semantic): Defines average_tax_rate measure which provides financial insights for business decision making.

## `models/marts/products.sql`

- Hotspot fusion score: `0.17`
- Semantic confidence: `0.80`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/products.sql` (line 5, semantic): select * from {{ ref('stg_products') }} indicates transformation of staging product data for mart use
- `models/marts/products.sql` (lines 1-9, semantic): Transformation dbt_model reads model.stg_products and writes model.products

## `models/marts/supplies.sql`

- Hotspot fusion score: `0.17`
- Semantic confidence: `0.80`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/marts/supplies.sql` (line 5, semantic): select * from {{ ref('stg_supplies') }} shows transformation of staging supply data for business use
- `models/marts/supplies.sql` (lines 1-9, semantic): Transformation dbt_model reads model.stg_supplies and writes model.supplies

## `models/marts/metricflow_time_spine.sql`

- Hotspot fusion score: `0.11`
- Semantic confidence: `0.80`
- Drift level: `likely_drift`
- Reasons: documentation drift (likely_drift)

Evidence:
- `models/marts/metricflow_time_spine.sql` (line 1, semantic): -- metricflow_time_spine.sql
- `models/marts/metricflow_time_spine.sql` (line 7, semantic): {{ dbt_date.get_base_dates(n_dateparts=365*10, datepart="day") }}

## `dbt_project.yml`

- Hotspot fusion score: `0.08`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `dbt_project.yml` (lines 3-4, semantic): name: "jaffle_shop" and version: "3.0.0" define core project identity
- `dbt_project.yml` (lines 12-17, semantic): model-paths, analysis-paths, test-paths, seed-paths, macro-paths, snapshot-paths define directory structure

## `models/staging/stg_customers.yml`

- Hotspot fusion score: `0.08`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/staging/stg_customers.yml` (line 3, semantic): Description states 'Customer data with basic cleaning and transformation applied, one row per customer'
- `models/staging/stg_customers.yml` (line 1, semantic): YAML key models

## `models/staging/stg_locations.yml`

- Hotspot fusion score: `0.08`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/staging/stg_locations.yml` (line 3, semantic): Description states 'List of open locations with basic cleaning and transformation applied, one row per location.'
- `models/staging/stg_locations.yml` (line 13, semantic): Unit test description confirms business logic of truncating timestamps to dates for operational reporting.

## `models/staging/stg_products.yml`

- Hotspot fusion score: `0.08`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/staging/stg_products.yml` (line 3, semantic): Description states 'Product (food and drink items that can be ordered) data with basic cleaning and transformation applied, one row per product'
- `models/staging/stg_products.yml` (line 1, semantic): YAML key models

## `models/staging/stg_supplies.yml`

- Hotspot fusion score: `0.08`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/staging/stg_supplies.yml` (lines 4-6, semantic): Description states 'List of our supply expenses data with basic cleaning and transformation applied' and explains multiple rows per supply_id due to cost fluctuations
- `models/staging/stg_supplies.yml` (line 1, semantic): YAML key models

## `models/staging/__sources.yml`

- Hotspot fusion score: `0.06`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/staging/__sources.yml` (lines 3-20, semantic): sources section defines raw_ecom schema with tables for customers, orders, items, stores, products, and supplies
- `models/staging/__sources.yml` (line 1, semantic): YAML key version

## `models/staging/stg_order_items.yml`

- Hotspot fusion score: `0.06`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/staging/stg_order_items.yml` (line 3, semantic): description: 'Individual food and drink items that make up our orders, one row per item.'
- `models/staging/stg_order_items.yml` (line 1, semantic): YAML key models

## `models/staging/stg_orders.yml`

- Hotspot fusion score: `0.06`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `models/staging/stg_orders.yml` (line 3, semantic): description: 'Order data with basic cleaning and transformation applied, one row per order.'
- `models/staging/stg_orders.yml` (line 1, semantic): YAML key models

## `.pre-commit-config.yaml`

- Hotspot fusion score: `0.03`
- Semantic confidence: `0.95`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `.pre-commit-config.yaml` (lines 1-14, semantic): YAML keys: repos
- `.pre-commit-config.yaml` (lines 5-7, semantic): hook IDs: check-yaml, end-of-file-fixer, trailing-whitespace

## `Taskfile.yml`

- Hotspot fusion score: `0.03`
- Semantic confidence: `0.90`
- Drift level: `no_drift`
- Reasons: missing documentation

Evidence:
- `Taskfile.yml` (lines 9-40, semantic): tasks section defines venv, install, gen, seed, clean, and load workflows for project automation
- `Taskfile.yml` (line 1, semantic): YAML key version
