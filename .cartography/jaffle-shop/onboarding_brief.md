# Onboarding Brief

Generated: 2026-03-14T05:31:21.515007+00:00

## What does this codebase do at a high level?
This is a dbt-based analytics engineering codebase that transforms raw e-commerce data into business-ready analytics models. It processes order transactions, customer behavior, product catalog, and supply chain data to support revenue analytics, customer segmentation, product performance analysis, and geographic reporting. The codebase follows a staging → marts architecture with semantic modeling for business intelligence.

Supporting citations:
- `models/marts/order_items.yml`:43-87 [phase3/semantic] Defines core business logic for order items including revenue calculations and product categorization, indicating analytics focus
- `models/staging/__sources.yml`:3-20 [phase1/semantic] Configures source data definitions for raw e-commerce entities (customers, orders, products), confirming data transformation purpose
- `models/marts/customers.sql`:20-26 [phase2/semantic] Creates comprehensive customer view with lifetime purchasing behavior and segmentation, showing customer analytics focus

## What are the main data flows and where does data come from?
Data flows from raw e-commerce sources through staging models to analytical marts. Source data includes customers, orders, products, and supplies from an e-commerce system (models/staging/__sources.yml). The lineage shows 6 dbt_source datasets feeding into 13 dbt_model transformations. Key flows: 1) Raw orders → stg_orders → orders mart → order_items mart; 2) Raw products → stg_products → supplies mart; 3) Raw customers → customers mart. Data is transformed using standardized macros like cents_to_dollars for currency conversion.

Supporting citations:
- `models/staging/__sources.yml`:3-20 [phase1/lineage] Defines 6 source data entities for raw e-commerce data, establishing data origins
- `models/staging/stg_orders.sql`:14-16 [phase3/semantic] Transforms raw order data with currency conversion and timestamp truncation, showing first transformation step
- `models/marts/orders.sql`:15-40 [phase3/semantic] Creates denormalized order view combining order data with item information, showing downstream data flow

## What are the critical modules that a new engineer should understand first?
Start with these 5 critical modules: 1) models/marts/order_items.yml (core business logic for revenue calculations), 2) models/staging/__sources.yml (data source definitions), 3) models/marts/customers.sql (customer analytics foundation), 4) macros/cents_to_dollars.sql (standardized financial calculations), 5) Taskfile.yml (automated workflows). These represent the highest PageRank modules and cover core business domains, data ingestion, and operational workflows.

Supporting citations:
- `models/marts/order_items.yml`:43-87 [phase3/semantic] Top PageRank module defining core business logic for order items and revenue calculations
- `models/staging/__sources.yml`:3-20 [phase1/semantic] Critical for understanding data origins and source system connections
- `Taskfile.yml`:9-40 [phase1/semantic] 4th highest PageRank module controlling environment setup and data loading operations

## Where are the highest-risk areas and technical debt?
The codebase shows low architectural risk with 0 hubs and 0 circular dependencies. However, there are 7 dead-code candidates indicating potential technical debt from unused or orphaned code. The absence of dynamic transformations suggests limited runtime flexibility. The main risk areas are likely in the dead code candidates which could cause maintenance overhead, though specific files aren't identified in the evidence.

Supporting citations:
- `EVIDENCE SUMMARY` [phase1/hotspot] 7 dead-code candidates identified as potential technical debt areas
- `EVIDENCE SUMMARY` [phase1/hotspot] 0 hubs and 0 circular dependencies indicate clean architecture but 0 dynamic transformations suggests limited runtime flexibility

## What are the blind spots — areas where the analysis may be incomplete?
Key blind spots: 1) Specific dead-code candidate files not identified, 2) No information about data quality tests or documentation, 3) Unknown project type and deployment environment details, 4) No visibility into actual data volumes or performance characteristics, 5) Missing information about orchestration/scheduling (though Taskfile.yml suggests some automation). The analysis shows 0 parse errors but lacks granularity on the 7 dead-code candidates.

Supporting citations:
- `EVIDENCE SUMMARY` [phase1/drift] 7 dead-code candidates identified but specific files not listed, creating analysis gap
- `EVIDENCE SUMMARY` [phase1/drift] Project type: unknown - missing context about deployment and operational environment
- `Taskfile.yml`:9-40 [phase1/semantic] Suggests automation workflows but lacks details about orchestration and scheduling

