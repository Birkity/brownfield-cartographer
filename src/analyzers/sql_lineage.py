"""
SQL lineage extraction using sqlglot.

Extracts table-level data lineage from SQL files:
  - Which tables/models are read (SELECT FROM, JOIN)
  - Which tables are written (INSERT INTO, CREATE TABLE AS, MERGE INTO)
  - CTE intermediate tables
  - dbt {{ ref() }} and {{ source() }} resolved to dataset names

Works on raw SQL text.  For dbt files the Jinja patterns are extracted
by regex (via dbt_helpers) *before* sqlglot parsing, so the lineage
covers both standard SQL table refs and dbt model references.

Design principle: never fabricate lineage.  If parsing fails or a table
reference is ambiguous, mark it with ``confidence < 1.0`` or ``is_dynamic = True``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from src.analyzers.dbt_helpers import extract_dbt_refs, extract_dbt_sources

logger = logging.getLogger(__name__)


@dataclass
class SQLLineageResult:
    """Lineage extracted from a single SQL file."""

    source_file: str
    """Relative path of the SQL file analysed."""

    upstream_tables: list[str] = field(default_factory=list)
    """Tables/models this SQL reads from (SELECT FROM / JOIN)."""

    downstream_tables: list[str] = field(default_factory=list)
    """Tables this SQL writes to (INSERT INTO / CREATE TABLE AS / model output)."""

    dbt_refs: list[str] = field(default_factory=list)
    """Model names from {{ ref('x') }} calls."""

    dbt_sources: list[tuple[str, str]] = field(default_factory=list)
    """(schema, table) pairs from {{ source('s', 't') }} calls."""

    cte_names: list[str] = field(default_factory=list)
    """Names of CTEs defined in this file (WITH x AS ...)."""

    transformation_type: str = "sql_query"
    """'dbt_model', 'dbt_macro', 'sql_query'."""

    sql_preview: str = ""
    """First 500 chars of the SQL for context."""

    confidence: float = 1.0
    is_dynamic: bool = False
    """True if SQL contains Jinja blocks we couldn't fully resolve."""

    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Jinja-stripping for sqlglot compatibility
# ---------------------------------------------------------------------------

# Match Jinja blocks that sqlglot can't parse
_JINJA_BLOCK_RE = re.compile(r"\{[%#].*?[%#]\}", re.DOTALL)
_JINJA_EXPR_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)


def _strip_jinja(sql_text: str) -> str:
    """
    Replace Jinja expressions with SQL-safe placeholders so sqlglot can parse.

    {{ ref('stg_orders') }}  →  __dbt_ref__stg_orders
    {{ source('raw', 'x') }} →  __dbt_source__raw__x
    {{ config(...) }}         →  (removed)
    {% ... %}                 →  (removed)
    {# ... #}                 →  (removed)
    """
    # Replace ref() calls with placeholder table names
    def _ref_replacer(m: re.Match) -> str:
        inner = m.group(0)
        ref_match = re.search(r"ref\(['\"]([^'\"]+)['\"]\)", inner)
        if ref_match:
            return f"__dbt_ref__{ref_match.group(1)}"
        return "'__jinja_expr__'"

    # Replace source() calls with placeholder table names
    def _source_replacer(m: re.Match) -> str:
        inner = m.group(0)
        src_match = re.search(
            r"source\(['\"]([^'\"]+)['\"],\s*['\"]([^'\"]+)['\"]\)", inner
        )
        if src_match:
            return f"__dbt_source__{src_match.group(1)}__{src_match.group(2)}"
        return "'__jinja_expr__'"

    result = sql_text
    # First pass: replace ref/source expressions with identifiers
    result = re.sub(
        r"\{\{\s*ref\([^)]+\)\s*\}\}", _ref_replacer, result
    )
    result = re.sub(
        r"\{\{\s*source\([^)]+\)\s*\}\}", _source_replacer, result
    )
    # Remove remaining Jinja (config, set, for, if blocks, comments)
    result = _JINJA_BLOCK_RE.sub("", result)
    result = _JINJA_EXPR_RE.sub("'__jinja_expr__'", result)
    return result


# ---------------------------------------------------------------------------
# sqlglot-based table extraction
# ---------------------------------------------------------------------------


def _extract_tables_sqlglot(sql_text: str) -> tuple[list[str], list[str], list[str]]:
    """
    Use sqlglot to extract upstream tables, downstream tables, and CTE names.

    Returns (upstream, downstream, cte_names).
    Falls back to regex on parse failure.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        logger.warning("sqlglot not installed — falling back to regex extraction")
        return _extract_tables_regex(sql_text)

    cleaned = _strip_jinja(sql_text)
    upstream: list[str] = []
    downstream: list[str] = []
    cte_names: list[str] = []

    try:
        # Try multiple dialects — dbt SQL may not parse as standard SQL
        parsed = None
        for dialect in (None, "duckdb", "postgres", "bigquery"):
            try:
                statements = sqlglot.parse(cleaned, dialect=dialect)
                if statements:
                    parsed = statements
                    break
            except Exception:
                continue

        if not parsed:
            return _extract_tables_regex(sql_text)

        for stmt in parsed:
            if stmt is None:
                continue

            # Extract CTE names (these are internal, not real tables)
            for cte in stmt.find_all(exp.CTE):
                alias = cte.alias
                if alias:
                    cte_names.append(alias)

            # Extract all table references
            for table in stmt.find_all(exp.Table):
                name = table.name
                db = table.db
                catalog = table.catalog
                if not name:
                    continue

                # Skip placeholder names from Jinja stripping
                if name.startswith("__jinja_expr__"):
                    continue

                # Build qualified name
                parts = [p for p in (catalog, db, name) if p]
                qualified = ".".join(parts)

                # Determine if this is upstream or downstream
                # Walk up the AST to see if we're inside an INSERT/CREATE/MERGE
                parent = table.parent
                is_write_target = False
                while parent:
                    if isinstance(parent, (exp.Insert, exp.Create, exp.Merge)):
                        # Check if this table is the target (not a source in subquery)
                        if hasattr(parent, "this") and parent.this is not None:
                            # The first table in INSERT INTO / CREATE TABLE is the target
                            target_tables = list(parent.this.find_all(exp.Table))
                            if target_tables and target_tables[0] is table:
                                is_write_target = True
                        break
                    parent = parent.parent

                if is_write_target:
                    downstream.append(qualified)
                else:
                    upstream.append(qualified)

    except Exception as exc:
        logger.debug("sqlglot parse failed: %s — falling back to regex", exc)
        return _extract_tables_regex(sql_text)

    # Deduplicate while preserving order; exclude CTEs from upstream
    cte_set = set(cte_names)
    upstream = _dedup([t for t in upstream if t not in cte_set])
    downstream = _dedup(downstream)

    return upstream, downstream, cte_names


def _extract_tables_regex(sql_text: str) -> tuple[list[str], list[str], list[str]]:
    """
    Regex fallback for SQL table extraction when sqlglot fails.

    Less accurate but handles broken/partial SQL.
    """
    upstream: list[str] = []
    downstream: list[str] = []
    cte_names: list[str] = []

    # FROM / JOIN table names
    from_re = re.compile(
        r"(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in from_re.finditer(sql_text):
        name = m.group(1)
        # Skip Jinja placeholders and SQL keywords
        if name.upper() not in ("SELECT", "WHERE", "SET", "VALUES", "LATERAL"):
            upstream.append(name)

    # INSERT INTO / CREATE TABLE
    write_re = re.compile(
        r"(?:INSERT\s+INTO|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|MERGE\s+INTO)\s+([a-zA-Z_][\w.]*)",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in write_re.finditer(sql_text):
        downstream.append(m.group(1))

    # CTE names
    cte_re = re.compile(r"(\w+)\s+AS\s*\(", re.IGNORECASE)
    for m in cte_re.finditer(sql_text):
        cte_names.append(m.group(1))

    cte_set = set(cte_names)
    upstream = _dedup([t for t in upstream if t not in cte_set])
    downstream = _dedup(downstream)

    return upstream, downstream, cte_names


def _dedup(items: list[str]) -> list[str]:
    """Deduplicate while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# dbt placeholder resolution
# ---------------------------------------------------------------------------


def _resolve_dbt_placeholders(
    tables: list[str],
    dbt_refs: list[str],
    dbt_sources: list[tuple[str, str]],
) -> list[str]:
    """
    Replace __dbt_ref__X and __dbt_source__S__T placeholders with
    proper dataset names.
    """
    resolved: list[str] = []
    ref_set = {f"__dbt_ref__{r}": f"model.{r}" for r in dbt_refs}
    src_set = {
        f"__dbt_source__{s}__{t}": f"source.{s}.{t}" for s, t in dbt_sources
    }
    for table in tables:
        if table in ref_set:
            resolved.append(ref_set[table])
        elif table in src_set:
            resolved.append(src_set[table])
        elif table.startswith("__dbt_"):
            # Unresolved dbt placeholder — skip
            continue
        else:
            resolved.append(table)
    return resolved


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_sql_file(
    sql_text: str,
    rel_path: str,
    is_dbt: bool = False,
) -> SQLLineageResult:
    """
    Extract data lineage from a single SQL file.

    Args:
        sql_text:  Raw SQL source text (may contain Jinja).
        rel_path:  Relative path from repo root.
        is_dbt:    Whether this file is part of a dbt project.

    Returns:
        SQLLineageResult with upstream/downstream datasets and metadata.
    """
    result = SQLLineageResult(
        source_file=rel_path,
        sql_preview=sql_text[:500],
    )

    # Detect dynamic SQL / heavy Jinja usage
    jinja_block_count = len(_JINJA_BLOCK_RE.findall(sql_text))
    jinja_expr_count = len(_JINJA_EXPR_RE.findall(sql_text))
    if jinja_block_count > 5 or jinja_expr_count > 10:
        result.is_dynamic = True
        result.confidence = max(0.3, 1.0 - (jinja_block_count * 0.05))

    # Extract dbt-specific references
    result.dbt_refs = extract_dbt_refs(sql_text)
    result.dbt_sources = extract_dbt_sources(sql_text)

    # Determine transformation type
    stem = PurePosixPath(rel_path).stem
    posix = rel_path.replace("\\", "/")
    if is_dbt:
        if "/macros/" in posix:
            result.transformation_type = "dbt_macro"
        else:
            result.transformation_type = "dbt_model"

    # Extract table-level lineage via sqlglot (or regex fallback)
    try:
        upstream, downstream, cte_names = _extract_tables_sqlglot(sql_text)
        result.cte_names = cte_names
    except Exception as exc:
        result.errors.append(f"Table extraction failed: {exc}")
        upstream, downstream = [], []

    # Resolve dbt placeholders to proper dataset names
    if result.dbt_refs or result.dbt_sources:
        upstream = _resolve_dbt_placeholders(
            upstream, result.dbt_refs, result.dbt_sources
        )

    result.upstream_tables = upstream

    # For dbt models, the file itself is the downstream target
    if is_dbt and result.transformation_type == "dbt_model":
        result.downstream_tables = [f"model.{stem}"]
    else:
        result.downstream_tables = downstream

    return result
