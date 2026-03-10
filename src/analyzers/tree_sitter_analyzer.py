"""
TreeSitterAnalyzer: extracts structural elements from source files via AST parsing.

Supported languages and what is extracted:
  Python     — imports, public/private functions, classes + inheritance, docstrings
  SQL        — table references from FROM/JOIN (requires tree-sitter-sql, optional)
  YAML       — top-level keys (structural awareness for dbt schema.yml, Airflow DAGs)
  JavaScript — ES6 import/require, function/arrow-function declarations
  TypeScript — same as JavaScript + interface/type declarations

Design principles:
  - Every language loader is wrapped in try/except; a missing grammar never
    kills the entire run.  The ModuleNode.parse_error field records failures.
  - query.matches() is used instead of query.captures() because 'matches'
    has a stable return type ({capture_name: list[Node]}) across tree-sitter
    0.21/0.22/0.23 Python bindings, while captures() changed in 0.22.
  - Node text is always decoded with errors="replace" to survive non-UTF-8 files.
  - SQL analysis via tree-sitter is a best-effort supplement.  The authoritative
    SQL lineage engine is sqlglot in Phase 2 (Hydrologist).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from src.models.nodes import ClassNode, FunctionNode, ImportInfo, Language, ModuleNode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Grammar loading helpers
# ---------------------------------------------------------------------------


def _load_ts_language(lang_name: str) -> Optional[Any]:
    """
    Attempt to load a tree-sitter Language object for *lang_name*.

    Returns None (not raises) when the grammar package is not installed,
    so that the rest of the codebase degrades gracefully.
    """
    try:
        from tree_sitter import Language as TSLanguage  # type: ignore[import]

        if lang_name == "python":
            import tree_sitter_python as mod  # type: ignore[import]
            return TSLanguage(mod.language())

        if lang_name == "yaml":
            import tree_sitter_yaml as mod  # type: ignore[import]
            return TSLanguage(mod.language())

        if lang_name == "javascript":
            import tree_sitter_javascript as mod  # type: ignore[import]
            return TSLanguage(mod.language())

        if lang_name == "typescript":
            import tree_sitter_typescript as mod  # type: ignore[import]
            # The package exposes language_typescript() and language_tsx()
            return TSLanguage(mod.language_typescript())

        if lang_name == "sql":
            import tree_sitter_sql as mod  # type: ignore[import]
            return TSLanguage(mod.language())

    except (ImportError, AttributeError, Exception) as exc:
        logger.warning(
            "tree-sitter grammar for %r not available: %s — falling back to text hints",
            lang_name,
            exc,
        )
    return None


def _make_query_cursor(language: Any, query_str: str) -> Any:
    """
    Create a (query, cursor) pair using the tree-sitter 0.25 API.

    In 0.24/0.25 the query execution was split:
      - Query(language, str) compiles the S-expression pattern.
      - QueryCursor(query)   executes it against a specific node.
    Returns None if the Query constructor raises (bad query string).
    """
    try:
        from tree_sitter import Query, QueryCursor  # type: ignore[import]
        q = Query(language, query_str)
        return QueryCursor(q)
    except Exception as exc:
        logger.debug("Could not compile query: %s", exc)
        return None


# Lazy cache: populated on first use, never reloaded
_GRAMMAR_CACHE: dict[str, Any] = {}


def _get_grammar(lang_name: str) -> Optional[Any]:
    if lang_name not in _GRAMMAR_CACHE:
        _GRAMMAR_CACHE[lang_name] = _load_ts_language(lang_name)
    return _GRAMMAR_CACHE[lang_name]


def _make_parser(language: Any) -> Any:
    """Create a tree-sitter Parser for *language*."""
    from tree_sitter import Parser  # type: ignore[import]
    return Parser(language)


# ---------------------------------------------------------------------------
# Query helper — tree-sitter 0.25 API: Query + QueryCursor
# ---------------------------------------------------------------------------


def _run_query(language: Any, query_str: str, root_node: Any) -> dict[str, list[Any]]:
    """
    Execute a tree-sitter S-expression query and return all captures merged.

    Uses the tree-sitter 0.25 API:
        Query(lang, str) → compiles the pattern
        QueryCursor(query).matches(root) → [(pattern_idx, {name: [Node]}), ...]

    Returns:
        {capture_name: [Node, ...]} — empty dict on any failure.
    """
    try:
        from tree_sitter import Query, QueryCursor  # type: ignore[import]
        q = Query(language, query_str)
        cursor = QueryCursor(q)
        result: dict[str, list[Any]] = {}
        for _pattern_index, captures in cursor.matches(root_node):
            for name, nodes in captures.items():
                node_list = nodes if isinstance(nodes, list) else [nodes]
                result.setdefault(name, []).extend(node_list)
        return result
    except Exception as exc:
        logger.debug("Query failed on node: %s", exc)
        return {}


def _text(node: Any) -> str:
    """Decode node bytes to str, replacing invalid bytes."""
    if node is None:
        return ""
    try:
        return node.text.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Python extractor
# ---------------------------------------------------------------------------

_PY_IMPORT_QUERY = """
[
  (import_statement)   @import
  (import_from_statement) @import_from
]
"""

_PY_FUNCTION_QUERY = """
(function_definition
  name: (identifier) @fn.name
  parameters: (parameters) @fn.params) @fn.def
"""

_PY_CLASS_QUERY = """
(class_definition
  name: (identifier) @cls.name) @cls.def
"""

# Separate query for base classes inside the argument_list
_PY_CLASS_BASES_QUERY = """
(class_definition
  name: (identifier) @cls.name
  superclasses: (argument_list
    [(identifier) (attribute)] @cls.base)) @cls.def
"""


def _parse_python_imports(root_node: Any, rel_path: str) -> list[ImportInfo]:
    lang = _get_grammar("python")
    if lang is None:
        return []

    captures = _run_query(lang, _PY_IMPORT_QUERY, root_node)
    imports: list[ImportInfo] = []

    for node in captures.get("import", []):
        imports.extend(_extract_import_statement(node))

    for node in captures.get("import_from", []):
        imports.extend(_extract_import_from_statement(node))

    return imports


def _extract_import_statement(node: Any) -> list[ImportInfo]:
    """Parse `import os`, `import os.path`, `import numpy as np`."""
    results: list[ImportInfo] = []
    for child in node.children:
        if child.type == "dotted_name":
            results.append(
                ImportInfo(module=_text(child), line=node.start_point[0] + 1)
            )
        elif child.type == "aliased_import":
            module_name = ""
            alias = ""
            for sub in child.children:
                if sub.type == "dotted_name":
                    module_name = _text(sub)
                elif sub.type == "identifier" and _text(sub) != "as":
                    alias = _text(sub)
            if module_name:
                results.append(
                    ImportInfo(
                        module=module_name,
                        alias=alias or None,
                        line=node.start_point[0] + 1,
                    )
                )
    return results


def _extract_import_from_statement(node: Any) -> list[ImportInfo]:
    """Parse `from foo import bar`, `from . import baz`, `from foo import *`."""
    module_name = ""
    names: list[str] = []
    is_relative = False

    for child in node.children:
        if child.type == "relative_import":
            is_relative = True
            for sub in child.children:
                if sub.type == "dotted_name":
                    module_name = "." + _text(sub)
        elif child.type == "dotted_name" and not module_name:
            module_name = _text(child)
        elif child.type == "wildcard_import":
            names = ["*"]
        elif child.type == "import_from_names":
            for sub in child.children:
                if sub.type == "dotted_name":
                    names.append(_text(sub))
                elif sub.type == "aliased_import":
                    for ss in sub.children:
                        if ss.type == "dotted_name":
                            names.append(_text(ss))
                            break

    if not module_name and is_relative:
        module_name = "."

    if module_name:
        return [
            ImportInfo(
                module=module_name,
                names=names,
                is_relative=is_relative,
                line=node.start_point[0] + 1,
            )
        ]
    return []


def _parse_python_functions(
    root_node: Any, rel_path: str
) -> list[FunctionNode]:
    lang = _get_grammar("python")
    if lang is None:
        return []

    captures = _run_query(lang, _PY_FUNCTION_QUERY, root_node)
    name_nodes = captures.get("fn.name", [])
    param_nodes = captures.get("fn.params", [])
    def_nodes = captures.get("fn.def", [])

    # Zip by index (matches() guarantees order matches across captures in same pattern)
    functions: list[FunctionNode] = []
    for idx, name_node in enumerate(name_nodes):
        name = _text(name_node)
        if not name:
            continue

        params = _text(param_nodes[idx]) if idx < len(param_nodes) else "()"
        def_node = def_nodes[idx] if idx < len(def_nodes) else None

        # Build signature text
        signature = f"def {name}{params}"
        if def_node is not None:
            # Try to capture return annotation
            for child in def_node.children:
                if child.type == "type":
                    signature += f" -> {_text(child)}"

        # Extract docstring: first statement in the body must be an expression_statement
        # containing a string
        docstring: Optional[str] = None
        if def_node is not None:
            docstring = _extract_docstring(def_node)

        functions.append(
            FunctionNode(
                name=name,
                qualified_name=name,  # Surveyor will qualify with class later
                parent_module=rel_path,
                signature=signature,
                is_public_api=not name.startswith("_"),
                line=name_node.start_point[0] + 1,
                end_line=(def_node.end_point[0] + 1 if def_node else 0),
                docstring=docstring,
            )
        )

    return functions


def _parse_python_classes(root_node: Any, rel_path: str) -> list[ClassNode]:
    lang = _get_grammar("python")
    if lang is None:
        return []

    # Get class names + definitions
    name_captures = _run_query(lang, _PY_CLASS_QUERY, root_node)
    # Get class names + explicit base classes (may be fewer if no superclasses)
    base_captures = _run_query(lang, _PY_CLASS_BASES_QUERY, root_node)

    # Build a map from class-name → bases
    bases_by_start: dict[int, list[str]] = {}
    base_cls_names = base_captures.get("cls.name", [])
    base_cls_bases = base_captures.get("cls.base", [])
    # Group bases by their parent class offset
    cur_class: Optional[tuple[int, list[str]]] = None
    for node in base_cls_bases:
        # Find the class that owns this base via its position
        for cn in base_cls_names:
            if cn.start_point[0] <= node.start_point[0]:
                key = cn.start_point[0]
                if key not in bases_by_start:
                    bases_by_start[key] = []
                if _text(node) not in bases_by_start[key]:
                    bases_by_start[key].append(_text(node))

    classes: list[ClassNode] = []
    for idx, name_node in enumerate(name_captures.get("cls.name", [])):
        def_node_list = name_captures.get("cls.def", [])
        def_node = def_node_list[idx] if idx < len(def_node_list) else None

        cls_name = _text(name_node)
        if not cls_name:
            continue

        bases = bases_by_start.get(name_node.start_point[0], [])
        methods = _extract_class_methods(def_node) if def_node else []
        docstring = _extract_docstring(def_node) if def_node else None

        classes.append(
            ClassNode(
                name=cls_name,
                qualified_name=cls_name,
                parent_module=rel_path,
                bases=bases,
                line=name_node.start_point[0] + 1,
                end_line=(def_node.end_point[0] + 1 if def_node else 0),
                methods=methods,
                docstring=docstring,
            )
        )

    return classes


def _extract_class_methods(class_node: Any) -> list[str]:
    """Return names of methods directly inside a class_definition node."""
    methods: list[str] = []
    for child in class_node.children:
        if child.type == "block":
            for item in child.children:
                if item.type == "function_definition":
                    for sub in item.children:
                        if sub.type == "identifier":
                            methods.append(_text(sub))
                            break
    return methods


def _extract_docstring(fn_or_class_node: Any) -> Optional[str]:
    """Extract the first docstring from a function_definition or class_definition."""
    for child in fn_or_class_node.children:
        if child.type == "block":
            for stmt in child.children:
                if stmt.type == "expression_statement":
                    for sub in stmt.children:
                        if sub.type in ("string", "concatenated_string"):
                            raw = _text(sub)
                            # Strip wrapping quotes
                            for q in ('"""', "'''", '"', "'"):
                                if raw.startswith(q) and raw.endswith(q) and len(raw) > 2 * len(q):
                                    return raw[len(q) : -len(q)].strip()
    return None


# ---------------------------------------------------------------------------
# SQL extractor (best-effort via tree-sitter-sql, optional)
# ---------------------------------------------------------------------------

_SQL_TABLE_QUERY = """
[
  (from_clause (identifier) @table)
  (from_clause (dotted_name) @table)
  (join_clause (identifier) @table)
  (join_clause (dotted_name) @table)
  (table_reference (identifier) @table)
  (relation (identifier) @table)
]
"""


def _parse_sql_table_refs(root_node: Any) -> list[str]:
    """Extract table names referenced in SQL (best-effort, grammar-dependent)."""
    lang = _get_grammar("sql")
    if lang is None:
        return []
    captures = _run_query(lang, _SQL_TABLE_QUERY, root_node)
    tables = []
    seen: set[str] = set()
    for node in captures.get("table", []):
        name = _text(node)
        if name and name.lower() not in {"select", "where", "on", "set"} and name not in seen:
            tables.append(name)
            seen.add(name)
    return tables


# ---------------------------------------------------------------------------
# YAML extractor — top-level keys only (structural awareness)
# ---------------------------------------------------------------------------

_YAML_KEY_QUERY = """
(block_mapping_pair
  key: (flow_node
    (plain_scalar (string_scalar) @key)))
"""

_YAML_KEY_QUERY_ALT = """
(block_mapping_pair
  key: (_) @key)
"""


def _parse_yaml_top_keys(root_node: Any) -> list[str]:
    """Extract top-level YAML mapping keys for structural awareness."""
    lang = _get_grammar("yaml")
    if lang is None:
        return []

    # Try the precise query first, fall back to the broader one
    captures = _run_query(lang, _YAML_KEY_QUERY, root_node)
    if not captures.get("key"):
        captures = _run_query(lang, _YAML_KEY_QUERY_ALT, root_node)

    keys: list[str] = []
    seen: set[str] = set()
    for node in captures.get("key", []):
        # Only include top-level keys (depth 0 or 1 in the tree)
        if node.start_point[1] <= 2:  # indent column heuristic for top-level
            k = _text(node)
            if k and k not in seen:
                keys.append(k)
                seen.add(k)
    return keys[:20]  # Cap at 20 keys to avoid noise


# ---------------------------------------------------------------------------
# JavaScript / TypeScript extractor
# ---------------------------------------------------------------------------

_JS_IMPORT_QUERY = """
[
  (import_statement source: (string) @source)
  (call_expression
    function: (identifier) @fn (#eq? @fn "require")
    arguments: (arguments (string) @source))
]
"""

_JS_FUNCTION_QUERY = """
[
  (function_declaration name: (identifier) @fn.name) @fn.def
  (lexical_declaration
    (variable_declarator
      name: (identifier) @fn.name
      value: (arrow_function) @fn.def))
  (export_statement
    declaration: (function_declaration name: (identifier) @fn.name) @fn.def)
]
"""


def _parse_js_imports(root_node: Any, language: Language) -> list[ImportInfo]:
    lang_key = "typescript" if language == Language.TYPESCRIPT else "javascript"
    lang = _get_grammar(lang_key)
    if lang is None:
        return []

    captures = _run_query(lang, _JS_IMPORT_QUERY, root_node)
    imports: list[ImportInfo] = []
    seen: set[str] = set()
    for node in captures.get("source", []):
        raw = _text(node).strip("'\"` ")
        if raw and raw not in seen:
            imports.append(
                ImportInfo(
                    module=raw,
                    is_relative=raw.startswith("."),
                    line=node.start_point[0] + 1,
                )
            )
            seen.add(raw)
    return imports


def _parse_js_functions(
    root_node: Any, language: Language, rel_path: str
) -> list[FunctionNode]:
    lang_key = "typescript" if language == Language.TYPESCRIPT else "javascript"
    lang = _get_grammar(lang_key)
    if lang is None:
        return []

    captures = _run_query(lang, _JS_FUNCTION_QUERY, root_node)
    functions: list[FunctionNode] = []
    seen: set[str] = set()

    for idx, name_node in enumerate(captures.get("fn.name", [])):
        name = _text(name_node)
        if not name or name in seen:
            continue
        seen.add(name)
        def_node_list = captures.get("fn.def", [])
        def_node = def_node_list[idx] if idx < len(def_node_list) else None

        functions.append(
            FunctionNode(
                name=name,
                qualified_name=name,
                parent_module=rel_path,
                signature=f"function {name}(...)",
                is_public_api=not name.startswith("_"),
                line=name_node.start_point[0] + 1,
                end_line=(def_node.end_point[0] + 1 if def_node else 0),
            )
        )
    return functions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def count_lines(source: bytes) -> int:
    """Count non-empty lines of actual content."""
    return sum(1 for line in source.split(b"\n") if line.strip())


def analyze_file(abs_path: Path, rel_path: str, language: Language) -> ModuleNode:
    """
    Parse a single source file and return a populated ModuleNode.

    This is the primary entry point called by the Surveyor agent.
    Never raises: all failures are captured in ``ModuleNode.parse_error``.
    """
    node = ModuleNode(path=rel_path, abs_path=str(abs_path), language=language)

    try:
        source = abs_path.read_bytes()
    except OSError as exc:
        node.parse_error = f"Could not read file: {exc}"
        logger.warning("Cannot read %s: %s", abs_path, exc)
        return node

    node.lines_of_code = count_lines(source)

    # ---- Select grammar key ---
    grammar_key_map = {
        Language.PYTHON: "python",
        Language.SQL: "sql",
        Language.YAML: "yaml",
        Language.JAVASCRIPT: "javascript",
        Language.TYPESCRIPT: "typescript",
    }
    grammar_key = grammar_key_map.get(language)
    if grammar_key is None:
        node.parse_error = f"No grammar mapping for language {language}"
        return node

    ts_lang = _get_grammar(grammar_key)
    if ts_lang is None:
        # Grammar not installed — still return the node with LOC data
        node.parse_error = f"Grammar package for {grammar_key!r} not installed"
        logger.debug("No grammar for %s (%s)", rel_path, grammar_key)
        return node

    # ---- Parse ---
    try:
        parser = _make_parser(ts_lang)
        tree = parser.parse(source)
        root = tree.root_node
    except Exception as exc:
        node.parse_error = f"tree-sitter parse error: {exc}"
        logger.warning("Parse error in %s: %s", rel_path, exc)
        return node

    # ---- Language-specific extraction ---
    try:
        if language == Language.PYTHON:
            node.imports = _parse_python_imports(root, rel_path)
            node.functions = _parse_python_functions(root, rel_path)
            node.classes = _parse_python_classes(root, rel_path)

        elif language == Language.SQL:
            # SQL: store table refs as synthetic ImportInfo for graph edges
            # The real lineage will be built in Phase 2 by sqlglot
            table_refs = _parse_sql_table_refs(root)
            node.imports = [
                ImportInfo(module=t, line=0) for t in table_refs
            ]

        elif language == Language.YAML:
            # YAML: store top-level keys as synthetic imports (structural hint)
            keys = _parse_yaml_top_keys(root)
            # Don't force-fit YAML keys into imports — just record count
            node.lines_of_code = count_lines(source)

        elif language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
            node.imports = _parse_js_imports(root, language)
            node.functions = _parse_js_functions(root, language, rel_path)

    except Exception as exc:
        node.parse_error = f"Extraction error: {exc}"
        logger.warning("Extraction failed for %s: %s", rel_path, exc, exc_info=True)

    return node
