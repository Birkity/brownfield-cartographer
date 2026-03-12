# High-Risk Areas

Files and datasets that carry the highest structural, velocity, or
dependency risk in this repository.  Prioritise code review and
test coverage for these items.

## 1. High Change-Velocity Files

_No git velocity data available._

## 2. Top Architectural Hubs (PageRank)

Nodes with the highest PageRank are depended on by many others.
Breaking changes here will cascade widely.

| Module / Dataset | PageRank Score |
|---|---|
| `models/staging/stg_products.sql` | 0.0562 |
| `models/staging/stg_supplies.sql` | 0.0562 |
| `models/staging/stg_orders.sql` | 0.0499 |
| `models/staging/stg_locations.sql` | 0.0475 |
| `models/marts/order_items.sql` | 0.0412 |

## 3. Circular Dependencies

_No circular dependencies detected._

## 4. Files with Parse Warnings

_No parse warnings._

## 5. High-Fan-Out Transformations

_None detected._

## 6. Unresolved / Dynamic Hotspots

These files contain dynamic SQL or Jinja that could not be fully resolved.
Lineage from these files may be incomplete.

| File | Confidence |
|---|---|
| `macros/cents_to_dollars.sql` | 0.45 |
| `macros/generate_schema_name.sql` | 0.40 |

