"""
Python dataflow analyzer.

Detects common data I/O patterns in Python-like source using structural parsing
via tree-sitter, with a regex fallback for graceful degradation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.analyzers.ts_grammar import _get_grammar, _make_parser

logger = logging.getLogger(__name__)


@dataclass
class DataIORecord:
    """A single detected data read or write in Python source."""

    source_file: str
    line: int = 0
    direction: str = "read"
    io_type: str = "unknown"
    target: str = ""
    pattern_matched: str = ""
    confidence: float = 1.0
    is_dynamic: bool = False
    extraction_method: str = "tree_sitter_ast"


@dataclass
class PythonDataflowResult:
    """Aggregated data I/O detections from a single Python file."""

    source_file: str
    records: list[DataIORecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class _ResolvedValue:
    value: str = ""
    is_dynamic: bool = True


_PANDAS_READ_METHODS = {
    "read_csv",
    "read_parquet",
    "read_excel",
    "read_json",
    "read_sql",
    "read_table",
    "read_feather",
    "read_orc",
    "read_hdf",
    "read_pickle",
    "read_stata",
    "read_sas",
    "read_spss",
    "read_fwf",
    "read_clipboard",
}
_PANDAS_WRITE_METHODS = {
    "to_csv",
    "to_parquet",
    "to_excel",
    "to_json",
    "to_feather",
    "to_orc",
    "to_hdf",
    "to_pickle",
    "to_stata",
    "to_sql",
}
_SPARK_READ_METHODS = {"parquet", "csv", "json", "orc", "text", "format", "table", "jdbc"}
_SPARK_WRITE_METHODS = {"parquet", "csv", "json", "orc", "text", "format", "saveAsTable", "insertInto", "save"}
_SQL_EXEC_BASES = {"cursor", "engine", "session", "conn", "connection"}


# ---------------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[re.Pattern[str], str, str, str, int | None]] = [
    (
        re.compile(
            r"""pd\.read_(?:csv|parquet|excel|json|sql|table|feather|orc|hdf|pickle|stata|sas|spss|fwf|clipboard)\s*\(\s*['"]([^'"]+)['"]""",
            re.IGNORECASE,
        ),
        "read", "pandas", "pd.read_*", 1,
    ),
    (
        re.compile(
            r"""pandas\.read_(?:csv|parquet|excel|json|sql|table|feather|orc|hdf|pickle|stata|sas|spss|fwf|clipboard)\s*\(\s*['"]([^'"]+)['"]""",
            re.IGNORECASE,
        ),
        "read", "pandas", "pandas.read_*", 1,
    ),
    (
        re.compile(
            r"""pd\.read_(?:csv|parquet|excel|json|sql|table)\s*\(\s*[^'"]""",
            re.IGNORECASE,
        ),
        "read", "pandas", "pd.read_*(dynamic)", None,
    ),
    (
        re.compile(
            r"""\.to_(?:csv|parquet|excel|json|feather|orc|hdf|pickle|stata|sql)\s*\(\s*['"]([^'"]+)['"]""",
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
    (
        re.compile(
            r"""spark\.read\.(?:parquet|csv|json|orc|text|format|table|jdbc)\s*\(\s*['"]([^'"]+)['"]""",
            re.IGNORECASE,
        ),
        "read", "spark", "spark.read.*", 1,
    ),
    (
        re.compile(
            r"""spark\.table\s*\(\s*['"]([^'"]+)['"]""",
            re.IGNORECASE,
        ),
        "read", "spark", "spark.table", 1,
    ),
    (
        re.compile(
            r"""\.write\.(?:parquet|csv|json|orc|text|format|saveAsTable|insertInto)\s*\(\s*['"]([^'"]+)['"]""",
            re.IGNORECASE,
        ),
        "write", "spark", "df.write.*", 1,
    ),
    (
        re.compile(
            r"""(?:cursor|engine|session|conn(?:ection)?)\.execute\s*\(\s*['"]([^'"]{5,})['"]""",
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
    (
        re.compile(
            r"""text\s*\(\s*['"]([^'"]{5,})['"]""",
            re.IGNORECASE,
        ),
        "read", "sql_exec", "text(sql)", 1,
    ),
    (
        re.compile(
            r"""open\s*\(\s*['"]([^'"]+)['"].*?['"]r""",
            re.IGNORECASE,
        ),
        "read", "file_io", "open(read)", 1,
    ),
    (
        re.compile(
            r"""open\s*\(\s*['"]([^'"]+)['"].*?['"][wa]""",
            re.IGNORECASE,
        ),
        "write", "file_io", "open(write)", 1,
    ),
]


def _analyze_python_file_regex(source_text: str, rel_path: str) -> PythonDataflowResult:
    """Legacy fallback for environments where tree-sitter parsing is unavailable."""
    result = PythonDataflowResult(source_file=rel_path)
    lines = source_text.splitlines()

    for line_idx, line_text in enumerate(lines, start=1):
        stripped = line_text.strip()
        if stripped.startswith("#"):
            continue

        for pattern, direction, io_type, name, target_group in _PATTERNS:
            match = pattern.search(line_text)
            if not match:
                continue

            target = ""
            confidence = 1.0
            is_dynamic = False

            if target_group is not None:
                try:
                    target = match.group(target_group)
                except IndexError:
                    is_dynamic = True
                    confidence = 0.5
            else:
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
                    extraction_method="regex",
                )
            )
            break

    return result


# ---------------------------------------------------------------------------
# tree-sitter-based analysis
# ---------------------------------------------------------------------------

def _node_text(node: Any) -> str:
    try:
        return node.text.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _named_children(node: Any) -> list[Any]:
    return [child for child in node.children if getattr(child, "is_named", False)]


def _flatten_attr(node: Any) -> str:
    if node is None:
        return ""
    if node.type == "identifier":
        return _node_text(node).strip()
    if node.type == "attribute":
        parts = [_flatten_attr(child) for child in _named_children(node)]
        return ".".join(part for part in parts if part)
    return _node_text(node).strip()


def _string_literal(node: Any) -> _ResolvedValue:
    raw = _node_text(node).strip()
    if not raw:
        return _ResolvedValue()
    if node.type in {"string", "concatenated_string"}:
        if "{" in raw and "}" in raw:
            return _ResolvedValue(is_dynamic=True)
        if raw.startswith(("f'", 'f"', "f'''", 'f"""')):
            return _ResolvedValue(is_dynamic=True)
        for quote in ('"""', "'''", '"', "'"):
            if raw.startswith(quote) and raw.endswith(quote) and len(raw) >= 2 * len(quote):
                return _ResolvedValue(value=raw[len(quote):-len(quote)], is_dynamic=False)
    return _ResolvedValue()


def _resolve_expression(node: Any, variables: dict[str, _ResolvedValue]) -> _ResolvedValue:
    if node is None:
        return _ResolvedValue()
    if node.type in {"string", "concatenated_string"}:
        return _string_literal(node)
    if node.type == "identifier":
        return variables.get(_node_text(node).strip(), _ResolvedValue())
    if node.type == "interpolation":
        return _ResolvedValue(is_dynamic=True)
    if node.type == "binary_operator":
        named = _named_children(node)
        if len(named) >= 2:
            left = _resolve_expression(named[0], variables)
            right = _resolve_expression(named[1], variables)
            if not left.is_dynamic and not right.is_dynamic:
                return _ResolvedValue(value=left.value + right.value, is_dynamic=False)
        return _ResolvedValue(is_dynamic=True)
    if node.type == "call":
        call_name = _flatten_attr(node.child_by_field_name("function") or _named_children(node)[0])
        if call_name.endswith("text"):
            arguments = _extract_call_arguments(node)
            if arguments:
                return _resolve_expression(arguments[0], variables)
    return _ResolvedValue()


def _extract_call_arguments(call_node: Any) -> list[Any]:
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        for child in call_node.children:
            if child.type == "argument_list":
                args_node = child
                break
    if args_node is None:
        return []
    return [
        child for child in args_node.children
        if getattr(child, "is_named", False)
    ]


def _extract_keyword_arguments(call_node: Any) -> dict[str, Any]:
    keyword_args: dict[str, Any] = {}
    for arg in _extract_call_arguments(call_node):
        if arg.type != "keyword_argument":
            continue
        named = _named_children(arg)
        if len(named) >= 2 and named[0].type == "identifier":
            keyword_args[_node_text(named[0]).strip()] = named[1]
    return keyword_args


def _record_from_call(
    call_node: Any,
    rel_path: str,
    alias_map: dict[str, str],
    variables: dict[str, _ResolvedValue],
) -> DataIORecord | None:
    function_node = call_node.child_by_field_name("function")
    call_name = _flatten_attr(function_node)
    if not call_name:
        return None

    arguments = _extract_call_arguments(call_node)
    keyword_args = _extract_keyword_arguments(call_node)
    line = call_node.start_point[0] + 1
    parts = call_name.split(".")
    if not parts:
        return None

    first = parts[0]
    last = parts[-1]
    resolved_first = alias_map.get(first, first)

    record: DataIORecord | None = None
    target_expr: Any | None = None

    if resolved_first == "pandas" and last in _PANDAS_READ_METHODS:
        target_expr = arguments[0] if arguments else None
        record = DataIORecord(
            source_file=rel_path,
            line=line,
            direction="read",
            io_type="pandas",
            pattern_matched=call_name,
        )
    elif last in _PANDAS_WRITE_METHODS:
        target_expr = arguments[0] if arguments else None
        record = DataIORecord(
            source_file=rel_path,
            line=line,
            direction="write",
            io_type="pandas",
            pattern_matched=call_name,
        )
    elif call_name == "spark.table" or (call_name.startswith("spark.read.") and last in _SPARK_READ_METHODS):
        target_expr = arguments[0] if arguments else None
        record = DataIORecord(
            source_file=rel_path,
            line=line,
            direction="read",
            io_type="spark",
            pattern_matched=call_name,
        )
    elif ".write." in call_name and last in _SPARK_WRITE_METHODS:
        target_expr = arguments[0] if arguments else None
        record = DataIORecord(
            source_file=rel_path,
            line=line,
            direction="write",
            io_type="spark",
            pattern_matched=call_name,
        )
    elif last == "execute" and any(part in _SQL_EXEC_BASES for part in parts[:-1]):
        target_expr = arguments[0] if arguments else None
        record = DataIORecord(
            source_file=rel_path,
            line=line,
            direction="read",
            io_type="sql_exec",
            pattern_matched=call_name,
        )
    elif last == "text":
        target_expr = arguments[0] if arguments else None
        record = DataIORecord(
            source_file=rel_path,
            line=line,
            direction="read",
            io_type="sql_exec",
            pattern_matched=call_name,
        )
    elif call_name == "open":
        target_expr = arguments[0] if arguments else None
        mode_expr = keyword_args.get("mode")
        if mode_expr is None and len(arguments) >= 2:
            mode_expr = arguments[1]
        mode = _resolve_expression(mode_expr, variables)
        direction = "read"
        if not mode.is_dynamic and any(flag in mode.value for flag in ("w", "a", "x", "+")):
            direction = "write"
        elif mode.is_dynamic:
            direction = "write"
        record = DataIORecord(
            source_file=rel_path,
            line=line,
            direction=direction,
            io_type="file_io",
            pattern_matched=call_name,
        )

    if record is None:
        return None

    target = _resolve_expression(target_expr, variables)
    if not target.is_dynamic and target.value:
        record.target = target.value
        record.confidence = 1.0
        record.is_dynamic = False
    else:
        record.confidence = 0.5
        record.is_dynamic = True
    return record


def _collect_import_aliases(root: Any) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "import_statement":
            for child in _named_children(node):
                if child.type == "dotted_name":
                    module = _node_text(child).strip()
                    if module:
                        alias_map[module.split(".")[0]] = module.split(".")[0]
                elif child.type == "aliased_import":
                    named = _named_children(child)
                    if len(named) >= 2:
                        module = _node_text(named[0]).strip()
                        alias = _node_text(named[1]).strip()
                        if module and alias:
                            alias_map[alias] = module.split(".")[0]
        stack.extend(reversed(_named_children(node)))
    return alias_map


def _collect_records_from_tree(root: Any, rel_path: str) -> list[DataIORecord]:
    alias_map = _collect_import_aliases(root)
    variables: dict[str, _ResolvedValue] = {}
    records: list[DataIORecord] = []

    def visit(node: Any, scope: dict[str, _ResolvedValue]) -> dict[str, _ResolvedValue]:
        local_scope = dict(scope)

        if node.type in {"module", "block"}:
            for child in _named_children(node):
                local_scope = visit(child, local_scope)
            return local_scope

        if node.type == "expression_statement":
            named = _named_children(node)
            if named:
                return visit(named[0], local_scope)
            return local_scope

        if node.type == "assignment":
            named = _named_children(node)
            if len(named) >= 2 and named[0].type == "identifier":
                local_scope[named[0].text.decode("utf-8", errors="replace")] = _resolve_expression(
                    named[1], local_scope
                )
            for child in named:
                if child.type == "call":
                    record = _record_from_call(child, rel_path, alias_map, local_scope)
                    if record:
                        records.append(record)
                else:
                    local_scope = visit(child, local_scope)
            return local_scope

        if node.type == "call":
            record = _record_from_call(node, rel_path, alias_map, local_scope)
            if record:
                records.append(record)
            for child in _named_children(node):
                if child.type == "call":
                    local_scope = visit(child, local_scope)
            return local_scope

        for child in _named_children(node):
            local_scope = visit(child, local_scope)
        return local_scope

    visit(root, variables)
    return records


def _analyze_python_file_tree_sitter(source_text: str, rel_path: str) -> PythonDataflowResult:
    result = PythonDataflowResult(source_file=rel_path)
    grammar = _get_grammar("python")
    if grammar is None:
        result.errors.append("tree-sitter grammar for python not installed")
        return result

    try:
        parser = _make_parser(grammar)
        tree = parser.parse(source_text.encode("utf-8"))
    except Exception as exc:
        result.errors.append(f"tree-sitter parse failed: {exc}")
        return result

    if tree.root_node.has_error:
        result.errors.append("tree-sitter parse recovered with syntax errors")

    result.records = _collect_records_from_tree(tree.root_node, rel_path)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_python_file(source_text: str, rel_path: str) -> PythonDataflowResult:
    """
    Scan Python-like source for data I/O patterns.

    Uses tree-sitter first for structural accuracy, then falls back to the
    legacy regex analyzer if structural parsing is unavailable.
    """
    structural = _analyze_python_file_tree_sitter(source_text, rel_path)
    if structural.records:
        return structural

    fallback = _analyze_python_file_regex(source_text, rel_path)
    fallback.errors = structural.errors
    return fallback
