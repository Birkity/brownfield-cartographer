"""
dbt-specific pattern extraction helpers.

Uses regex (not tree-sitter) to extract dbt Jinja templating patterns from SQL files.
These run on raw source text because dbt's Jinja is not valid SQL and is
invisible to a standard SQL grammar.

Patterns extracted:
  - {{ ref('model_name') }}           — intra-project model references
  - {{ source('schema', 'table') }}   — raw source declarations

These are consumed by the Surveyor to build DBT_REF edges in the import graph,
giving a meaningful graph for dbt projects where Python imports don't exist.
"""

from __future__ import annotations

import re

# Matches {{ ref('model') }} and {{ ref("model") }}
_DBT_REF_RE = re.compile(r"\{\{\s*ref\(['\"]([^'\"]+)['\"]\)\s*\}\}")

# Matches {{ source('schema', 'table') }} and {{ source("schema", "table") }}
_DBT_SOURCE_RE = re.compile(
    r"\{\{\s*source\(['\"]([^'\"]+)['\"],\s*['\"]([^'\"]+)['\"]\)\s*\}\}"
)


def extract_dbt_refs(sql_text: str) -> list[str]:
    """
    Return all model names referenced via {{ ref('model_name') }} in *sql_text*.

    Example:
        sql = "SELECT * FROM {{ ref('stg_orders') }} JOIN {{ ref('stg_customers') }}"
        extract_dbt_refs(sql)  # → ['stg_orders', 'stg_customers']
    """
    return _DBT_REF_RE.findall(sql_text)


def extract_dbt_sources(sql_text: str) -> list[tuple[str, str]]:
    """
    Return (schema, table) pairs from {{ source('schema', 'table') }} calls.

    Example:
        sql = "SELECT * FROM {{ source('raw', 'orders') }}"
        extract_dbt_sources(sql)  # → [('raw', 'orders')]
    """
    return _DBT_SOURCE_RE.findall(sql_text)
