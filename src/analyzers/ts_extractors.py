"""
Language-specific AST extraction functions for the Brownfield Cartographer.

All functions accept pre-parsed tree-sitter root nodes and return typed lists
of ImportInfo, FunctionNode, ClassNode, or primitive values.  They rely on
ts_grammar.py for grammar loading, query execution, and text decoding.

Covered languages:
  - Python  : imports, functions, classes, cyclomatic complexity
  - SQL     : table references (best-effort, optional grammar)
  - YAML    : top-level keys
  - JS/TS   : imports (ES6 + require), function declarations
  - Regex   : imports for Java, Kotlin, Scala, Go, Rust, C#, Ruby
"""

from __future__ import annotations

import re
import logging
from typing import Any, Optional

from src.models.nodes import ClassNode, FunctionNode, ImportInfo, Language
from src.analyzers.ts_grammar import _get_grammar, _run_query, _text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Python — S-expression queries
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
            results.append(ImportInfo(module=_text(child), line=node.start_point[0] + 1))
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
                    ImportInfo(module=module_name, alias=alias or None,
                               line=node.start_point[0] + 1)
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
        return [ImportInfo(module=module_name, names=names, is_relative=is_relative,
                           line=node.start_point[0] + 1)]
    return []


def _parse_python_functions(root_node: Any, rel_path: str) -> list[FunctionNode]:
    lang = _get_grammar("python")
    if lang is None:
        return []

    captures = _run_query(lang, _PY_FUNCTION_QUERY, root_node)
    name_nodes = captures.get("fn.name", [])
    param_nodes = captures.get("fn.params", [])
    def_nodes = captures.get("fn.def", [])

    functions: list[FunctionNode] = []
    for idx, name_node in enumerate(name_nodes):
        name = _text(name_node)
        if not name:
            continue
        params = _text(param_nodes[idx]) if idx < len(param_nodes) else "()"
        def_node = def_nodes[idx] if idx < len(def_nodes) else None
        signature = f"def {name}{params}"
        if def_node is not None:
            for child in def_node.children:
                if child.type == "type":
                    signature += f" -> {_text(child)}"
        docstring = _extract_docstring(def_node) if def_node is not None else None
        functions.append(
            FunctionNode(
                name=name, qualified_name=name, parent_module=rel_path,
                signature=signature, is_public_api=not name.startswith("_"),
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

    name_captures = _run_query(lang, _PY_CLASS_QUERY, root_node)
    base_captures = _run_query(lang, _PY_CLASS_BASES_QUERY, root_node)

    bases_by_start: dict[int, list[str]] = {}
    base_cls_names = base_captures.get("cls.name", [])
    base_cls_bases = base_captures.get("cls.base", [])
    for node in base_cls_bases:
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
        classes.append(
            ClassNode(
                name=cls_name, qualified_name=cls_name, parent_module=rel_path,
                bases=bases_by_start.get(name_node.start_point[0], []),
                line=name_node.start_point[0] + 1,
                end_line=(def_node.end_point[0] + 1 if def_node else 0),
                methods=_extract_class_methods(def_node) if def_node else [],
                docstring=_extract_docstring(def_node) if def_node else None,
            )
        )
    return classes


def _extract_class_methods(class_node: Any) -> list[str]:
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
    for child in fn_or_class_node.children:
        if child.type == "block":
            for stmt in child.children:
                if stmt.type == "expression_statement":
                    for sub in stmt.children:
                        if sub.type in ("string", "concatenated_string"):
                            raw = _text(sub)
                            for q in ('"""', "'''", '"', "'"):
                                if (raw.startswith(q) and raw.endswith(q)
                                        and len(raw) > 2 * len(q)):
                                    return raw[len(q) : -len(q)].strip()
    return None


# ---------------------------------------------------------------------------
# Cyclomatic complexity (Python only)
# ---------------------------------------------------------------------------

_BRANCH_NODE_TYPES = frozenset({
    "if_statement", "elif_clause", "for_statement", "while_statement",
    "except_clause", "conditional_expression", "boolean_operator", "with_statement",
})


def _count_branch_nodes(root: Any) -> int:
    stack = [root]
    count = 0
    while stack:
        node = stack.pop()
        if node.type in _BRANCH_NODE_TYPES:
            count += 1
        stack.extend(node.children)
    return count


def _compute_python_complexity(root_node: Any) -> float:
    lang = _get_grammar("python")
    if lang is None:
        return 0.0
    captures = _run_query(lang, _PY_FUNCTION_QUERY, root_node)
    fn_defs = captures.get("fn.def", [])
    if not fn_defs:
        return float(1 + _count_branch_nodes(root_node))
    return float(max(1 + _count_branch_nodes(fn) for fn in fn_defs))


# ---------------------------------------------------------------------------
# SQL — best-effort table references
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
    lang = _get_grammar("sql")
    if lang is None:
        return []
    captures = _run_query(lang, _SQL_TABLE_QUERY, root_node)
    tables: list[str] = []
    seen: set[str] = set()
    for node in captures.get("table", []):
        name = _text(node)
        if name and name.lower() not in {"select", "where", "on", "set"} and name not in seen:
            tables.append(name)
            seen.add(name)
    return tables


# ---------------------------------------------------------------------------
# YAML — top-level keys
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
    lang = _get_grammar("yaml")
    if lang is None:
        return []
    captures = _run_query(lang, _YAML_KEY_QUERY, root_node)
    if not captures.get("key"):
        captures = _run_query(lang, _YAML_KEY_QUERY_ALT, root_node)
    keys: list[str] = []
    seen: set[str] = set()
    for node in captures.get("key", []):
        if node.start_point[1] <= 2:
            k = _text(node)
            if k and k not in seen:
                keys.append(k)
                seen.add(k)
    return keys[:20]


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
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
            imports.append(ImportInfo(module=raw, is_relative=raw.startswith("."),
                                      line=node.start_point[0] + 1))
            seen.add(raw)
    return imports


def _parse_js_functions(root_node: Any, language: Language, rel_path: str) -> list[FunctionNode]:
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
                name=name, qualified_name=name, parent_module=rel_path,
                signature=f"function {name}(...)", is_public_api=not name.startswith("_"),
                line=name_node.start_point[0] + 1,
                end_line=(def_node.end_point[0] + 1 if def_node else 0),
            )
        )
    return functions


# ---------------------------------------------------------------------------
# Regex-based imports (Java, Kotlin, Scala, Go, Rust, C#, Ruby)
# ---------------------------------------------------------------------------

_REGEX_IMPORT_PATTERNS: dict[Language, re.Pattern[str]] = {}


def _init_regex_patterns() -> None:
    _REGEX_IMPORT_PATTERNS[Language.JAVA] = re.compile(
        r"^\s*import\s+(?:static\s+)?([a-zA-Z_][\w.]*)\s*;", re.MULTILINE)
    _REGEX_IMPORT_PATTERNS[Language.KOTLIN] = re.compile(
        r"^\s*import\s+([a-zA-Z_][\w.]+(?:\.\*)?)", re.MULTILINE)
    _REGEX_IMPORT_PATTERNS[Language.SCALA] = re.compile(
        r"^\s*import\s+([a-zA-Z_][\w.]+(?:\.\{[^}]*\}|\._|\.[\w*]+)?)", re.MULTILINE)
    _REGEX_IMPORT_PATTERNS[Language.GO] = re.compile(
        r'^\s*"([a-zA-Z][a-zA-Z0-9_.\-/]+)"', re.MULTILINE)
    _REGEX_IMPORT_PATTERNS[Language.RUST] = re.compile(
        r"^\s*use\s+([\w:]+(?:::[\w*{}|, ]+)?)", re.MULTILINE)
    _REGEX_IMPORT_PATTERNS[Language.CSHARP] = re.compile(
        r"^\s*using\s+(?:static\s+)?(?!var\b)([a-zA-Z_][\w.]*)\s*;", re.MULTILINE)
    _REGEX_IMPORT_PATTERNS[Language.RUBY] = re.compile(
        r"^\s*require(?:_relative)?\s+['\"]([^'\"]+)['\"]", re.MULTILINE)


def extract_imports_by_regex(text: str, language: Language) -> list[ImportInfo]:
    """Extract imports for languages handled by regex (no tree-sitter grammar needed)."""
    if not _REGEX_IMPORT_PATTERNS:
        _init_regex_patterns()
    pattern = _REGEX_IMPORT_PATTERNS.get(language)
    if pattern is None:
        return []
    imports: list[ImportInfo] = []
    for match in pattern.finditer(text):
        module = match.group(1).strip()
        if not module:
            continue
        line = text[: match.start()].count("\n") + 1
        imports.append(ImportInfo(module=module, is_relative=False, line=line))
    return imports
