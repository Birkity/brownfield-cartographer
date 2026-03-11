"""
Python dataflow analyzer — detects data I/O patterns in Python source code.

Scans Python files for common data-engineering patterns:
  - pandas read/write:  pd.read_csv(), df.to_parquet(), etc.
  - Spark I/O:          spark.read.parquet(), df.write.saveAsTable(), etc.
  - SQL execution:      cursor.execute(), engine.execute(), session.execute()
  - File I/O:           open() with read/write

For each detected pattern, produces a lightweight record describing:
  - What kind of data operation this is (read / write)
  - The target path or table name (if extractable from a string literal)
  - Confidence score (1.0 for obvious patterns, lower for dynamic arguments)

This uses **regex over source text** rather than AST walking — good enough
for >90% of real-world patterns and avoids tree-sitter dependency issues
with Python grammars across versions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DataIORecord:
    """A single detected data read or write in Python source."""

    source_file: str
    """Relative path of the Python file."""

    line: int = 0
    """1-based line number of the match."""

    direction: str = "read"
    """'read' or 'write'."""

    io_type: str = "unknown"
    """'pandas', 'spark', 'sql_exec', 'file_io', 'unknown'."""

    target: str = ""
    """File path or table name if we could extract a static string literal."""

    pattern_matched: str = ""
    """The regex pattern name that triggered this detection."""

    confidence: float = 1.0
    is_dynamic: bool = False
    """True if the target could not be statically determined."""


@dataclass
class PythonDataflowResult:
    """Aggregated data I/O detections from a single Python file."""

    source_file: str
    records: list[DataIORecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# Each pattern: (regex, direction, io_type, pattern_name, group_for_target)
# group_for_target: regex group index for the file/table name, or None

_PATTERNS: list[tuple[re.Pattern, str, str, str, int | None]] = [
    # ---- pandas reads ----
    (
        re.compile(
            r"""pd\.read_(?:csv|parquet|excel|json|sql|table|feather|orc|hdf|pickle|stata|sas|spss|fwf|clipboard)\s*\(\s*['\"]([^'"]+)['\"]""",
            re.IGNORECASE,
        ),
        "read", "pandas", "pd.read_*", 1,
    ),
    (
        re.compile(
            r"""pandas\.read_(?:csv|parquet|excel|json|sql|table|feather|orc|hdf|pickle|stata|sas|spss|fwf|clipboard)\s*\(\s*['\"]([^'"]+)['\"]""",
            re.IGNORECASE,
        ),
        "read", "pandas", "pandas.read_*", 1,
    ),
    # pandas reads with variable (dynamic)
    (
        re.compile(
            r"""pd\.read_(?:csv|parquet|excel|json|sql|table)\s*\(\s*[^'"]""",
            re.IGNORECASE,
        ),
        "read", "pandas", "pd.read_*(dynamic)", None,
    ),
    # ---- pandas writes ----
    (
        re.compile(
            r"""\.to_(?:csv|parquet|excel|json|feather|orc|hdf|pickle|stata|sql)\s*\(\s*['\"]([^'"]+)['\"]""",
            re.IGNORECASE,
        ),
        "write", "pandas", "df.to_*", 1,
    ),
    (
        re.compile(
            r"""\.to_(?:csv|parquet|excel|json|feather|orc|hdf|pickle|stata|sql)\s*\(\s*[^'"]""",
            re.IGNORECASE,
        ),
        "write", "pandas", "df.to_*(dynamic)", None,
    ),
    # ---- Spark reads ----
    (
        re.compile(
            r"""spark\.read\.(?:parquet|csv|json|orc|text|format|table|jdbc)\s*\(\s*['\"]([^'"]+)['\"]""",
            re.IGNORECASE,
        ),
        "read", "spark", "spark.read.*", 1,
    ),
    (
        re.compile(
            r"""spark\.table\s*\(\s*['\"]([^'"]+)['\"]""",
            re.IGNORECASE,
        ),
        "read", "spark", "spark.table", 1,
    ),
    # ---- Spark writes ----
    (
        re.compile(
            r"""\.write\.(?:parquet|csv|json|orc|text|format|saveAsTable|insertInto)\s*\(\s*['\"]([^'"]+)['\"]""",
            re.IGNORECASE,
        ),
        "write", "spark", "df.write.*", 1,
    ),
    # ---- SQL execution ----
    (
        re.compile(
            r"""(?:cursor|engine|session|conn(?:ection)?)\.execute\s*\(\s*['\"]([^'"]{5,})['\"]""",
            re.IGNORECASE,
        ),
        "read", "sql_exec", "*.execute(sql)", 1,
    ),
    (
        re.compile(
            r"""(?:cursor|engine|session|conn(?:ection)?)\.execute\s*\(\s*[^'"]""",
            re.IGNORECASE,
        ),
        "read", "sql_exec", "*.execute(dynamic)", None,
    ),
    # ---- SQLAlchemy text() ----
    (
        re.compile(
            r"""text\s*\(\s*['\"]([^'"]{5,})['\"]""",
            re.IGNORECASE,
        ),
        "read", "sql_exec", "text(sql)", 1,
    ),
    # ---- File I/O with open() ----
    (
        re.compile(
            r"""open\s*\(\s*['\"]([^'"]+)['\"].*?['\"]r""",
            re.IGNORECASE,
        ),
        "read", "file_io", "open(read)", 1,
    ),
    (
        re.compile(
            r"""open\s*\(\s*['\"]([^'"]+)['\"].*?['\"][wa]""",
            re.IGNORECASE,
        ),
        "write", "file_io", "open(write)", 1,
    ),
]


def analyze_python_file(
    source_text: str,
    rel_path: str,
) -> PythonDataflowResult:
    """
    Scan Python source for data I/O patterns.

    Args:
        source_text: Full Python source code.
        rel_path:    Relative path from repo root.

    Returns:
        PythonDataflowResult with any detected data I/O records.
    """
    result = PythonDataflowResult(source_file=rel_path)
    lines = source_text.splitlines()

    for line_idx, line_text in enumerate(lines, start=1):
        stripped = line_text.strip()
        if stripped.startswith("#"):
            continue

        for pattern, direction, io_type, name, target_group in _PATTERNS:
            m = pattern.search(line_text)
            if m:
                target = ""
                confidence = 1.0
                is_dynamic = False

                if target_group is not None:
                    try:
                        target = m.group(target_group)
                    except IndexError:
                        target = ""
                        is_dynamic = True
                        confidence = 0.5
                else:
                    # Dynamic pattern — couldn't extract static target
                    is_dynamic = True
                    confidence = 0.5

                result.records.append(
                    DataIORecord(
                        source_file=rel_path,
                        line=line_idx,
                        direction=direction,
                        io_type=io_type,
                        target=target,
                        pattern_matched=name,
                        confidence=confidence,
                        is_dynamic=is_dynamic,
                    )
                )
                # Only match first pattern per line to avoid duplicates
                break

    return result
