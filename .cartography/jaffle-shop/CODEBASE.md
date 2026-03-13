# CODEBASE

Generated: 2026-03-13T13:42:31.933745+00:00

## Architecture Overview
This dbt repository maps to 33 modules, 25 datasets, and 13 transformations. This is a dbt-based analytics project for an e-commerce 'jaffle shop' that transforms raw order, product, customer, and location data into business-ready data marts for analytics and reporting. It focuses on order analytics, customer segmentation, product management, and location-based analysis with standardized financial calculations.

## Critical Path
- `models/staging/stg_products.sql` (0.69)
- `models/staging/stg_orders.sql` (0.62)
- `models/staging/stg_supplies.sql` (0.58)
- `models/marts/orders.sql` (0.49)
- `models/marts/order_items.sql` (0.49)

## Data Sources And Sinks
Sources: `seed.raw_customers`, `seed.raw_items`, `seed.raw_orders`, `seed.raw_products`, `seed.raw_stores`
Sinks: `model.customers`, `model.locations`, `model.metricflow_time_spine`, `model.products`, `model.supplies`

## Known Debt
Circular dependency clusters: 0. Blind spots: 0. Semantic review queue items: 27.

## High-Velocity Files
- No high-velocity files were detected in the configured git window.
