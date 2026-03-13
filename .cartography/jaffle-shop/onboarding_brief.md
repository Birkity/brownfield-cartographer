# Onboarding Brief

Generated: 2026-03-13T13:42:31.935157+00:00

## What does this codebase do at a high level?
This is a dbt-based analytics project for an e-commerce 'jaffle shop' that transforms raw order, product, customer, and location data into business-ready data marts for analytics and reporting. It focuses on order analytics, customer segmentation, product management, and location-based analysis with standardized financial calculations.

Supporting citations:
- `Taskfile.yml`:9-40 [phase1/semantic] Defines the complete data workflow for the jaffle shop analytics project including environment setup, data generation, and database loading
- `models/staging/__sources.yml`:3-20 [phase1/semantic] Establishes foundational data source definitions mapping raw database tables to business entities for e-commerce analytics
- `models/marts/orders.yml`:3 [phase3/semantic] Defines the orders data mart providing comprehensive order analytics including financial details and customer information

## What are the main data flows and where does data come from?
Data flows from raw sources through staging models to analytical marts. Raw data comes from database tables (6 sources) and seed files (6 seeds), is transformed through 13 dbt models, with lineage showing 13 produce edges and 17 consume edges. The main flow: source tables → staging models (stg_orders, stg_order_items, stg_products) → mart models (orders, order_items, customers, locations) for business analysis.

Supporting citations:
- `models/staging/__sources.yml`:3-20 [phase1/lineage] Defines 6 source tables from raw database providing foundational data for the e-commerce analytics system
- `models/staging/stg_orders.yml`:3 [phase1/lineage] First transformation layer that cleans and validates order data with financial integrity checks
- `models/marts/orders.sql`:15-39 [phase3/lineage] Aggregates order data with item-level details, showing consumption of staged data to create business-ready marts

## What are the critical modules that a new engineer should understand first?
1. Taskfile.yml (orchestrates complete workflow), 2. models/marts/order_items.yml (core business metrics), 3. models/marts/orders.sql (comprehensive order view), 4. models/staging/__sources.yml (source definitions), 5. macros/cents_to_dollars.sql (financial standardization), 6. models/marts/customers.yml (customer analytics). These represent the highest PageRank modules covering orchestration, core business logic, and data standardization.

Supporting citations:
- `Taskfile.yml`:9-40 [phase1/semantic] Top PageRank module that orchestrates the complete data workflow including environment setup and data loading
- `models/marts/order_items.yml`:43-87 [phase3/semantic] Second highest PageRank module defining core business metrics for order items including revenue breakdown and profit calculations
- `models/marts/orders.sql`:15-39 [phase3/semantic] Fourth highest PageRank module creating comprehensive order views with business-relevant computed fields

## Where are the highest-risk areas and technical debt?
The codebase shows low architectural risk with 0 hubs and 0 circular dependencies. However, there are 7 dead-code candidates that represent potential technical debt. The main risk areas are likely in the dead code modules that may need cleanup or documentation. No parse errors or high-complexity hubs were detected in the architecture analysis.

Supporting citations:
- `BLIND SPOTS analysis` [phase3/hotspot] Identifies 7 dead-code candidates as the primary technical debt requiring investigation and potential cleanup
- `HIGH RISK AREAS analysis` [phase3/hotspot] Confirms 0 hubs and 0 circular dependencies indicating low architectural risk in the current codebase

## What are the blind spots — areas where the analysis may be incomplete?
The analysis identifies 7 dead-code candidates as blind spots requiring investigation. These modules may be unused code, deprecated functionality, or incorrectly tagged assets. Additionally, the project type is marked as 'unknown' suggesting the analysis couldn't definitively classify the project framework. There are 0 parse errors, but the dead code represents the main area where understanding is incomplete.

Supporting citations:
- `BLIND SPOTS analysis` [phase3/drift] Specifically identifies 7 dead-code candidates as the primary blind spots requiring manual investigation
- `EVIDENCE SUMMARY` [phase1/drift] Notes project type as 'unknown' indicating incomplete framework classification in the analysis

