# Blind Spots & Unresolved References

This report lists files, edges, and datasets where the Cartographer
could not establish high-confidence intelligence.  Use it to prioritise
manual review or refactoring.

## Summary

| Category | Count |
|---|---|
| Parse failures | 0 |
| Grammar not installed | 0 |
| Structurally empty files (no imports/refs extracted) | 2 |
| Dynamic / partially-unresolved SQL transformations | 2 |
| Low-confidence datasets (< 70 %) | 2 |
| Low-confidence edges (< 70 %) | 2 |

## Parse Failures

_None detected._

## Grammar Not Installed

_None detected._

## Structurally Empty Files

- `macros/cents_to_dollars.sql` (sql, 16 lines) — no imports or refs extracted
- `macros/generate_schema_name.sql` (sql, 16 lines) — no imports or refs extracted

## Dynamic SQL Transformations

- `macros/cents_to_dollars.sql` (id=sql:macros/cents_to_dollars.sql, confidence=0.45) — Contains dynamic Jinja/SQL that could not be fully resolved
- `macros/generate_schema_name.sql` (id=sql:macros/generate_schema_name.sql, confidence=0.40) — Contains dynamic Jinja/SQL that could not be fully resolved

## Low-Confidence Datasets

- `model.cents_to_dollars` (type=dbt_model, confidence=0.45)
- `model.generate_schema_name` (type=dbt_model, confidence=0.40)

## Low-Confidence Edges

- `sql:macros/cents_to_dollars.sql` → `model.cents_to_dollars` [PRODUCES] confidence=0.45
- `sql:macros/generate_schema_name.sql` → `model.generate_schema_name` [PRODUCES] confidence=0.40

