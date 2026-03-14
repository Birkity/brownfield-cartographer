# CODEBASE

Generated: 2026-03-14T05:31:21.513133+00:00

## Architecture Overview
This dbt repository maps to 33 modules, 25 datasets, and 13 transformations. This is a dbt-based analytics engineering codebase that transforms raw e-commerce data into business-ready analytics models. It processes order transactions, customer behavior, product catalog, and supply chain data to support revenue analytics, customer segmentation, product performance analysis, and geographic reporting. The codebase follows a staging → marts architecture with semantic modeling for business intelligence.

## Critical Path
- `models/staging/stg_products.sql` (0.68): This staging model transforms raw product data from the e-commerce system into a standardized format with cleaned column names and calculated fields. It prepares product information for downstream analytics by renaming columns, converting price units, and adding boolean flags for product categories.
- `models/staging/stg_orders.sql` (0.66): This staging model transforms raw e-commerce order data into a standardized format suitable for analytics by renaming fields, converting currency values from cents to dollars, and truncating timestamps to daily granularity.
- `models/staging/stg_supplies.sql` (0.66): This staging model transforms raw supply data from the e-commerce system into a standardized format for downstream analytics. It cleans and renames fields, generates surrogate keys, and converts cost values from cents to dollars while preserving all original supply information.
- `models/marts/order_items.sql` (0.50): This model creates a comprehensive view of order items by joining staging tables for orders, products, and supplies to include product details, order timestamps, and calculated supply costs. It serves as a centralized dataset for analyzing order item profitability and product performance.
- `models/marts/orders.sql` (0.47): This model creates a denormalized view of orders with enriched business metrics including item counts, cost summaries, and customer ordering sequence. It combines order data with detailed item information to enable downstream analytics around order composition and customer behavior.

## Data Sources And Sinks
Sources: `seed.raw_customers`, `seed.raw_items`, `seed.raw_orders`, `seed.raw_products`, `seed.raw_stores`
Sinks: `model.customers`, `model.locations`, `model.metricflow_time_spine`, `model.products`, `model.supplies`

## Known Debt
Circular dependency clusters: 0. Blind spots: 0. Semantic review queue items: 25.

## High-Velocity Files
- No high-velocity files were detected in the configured git window.
