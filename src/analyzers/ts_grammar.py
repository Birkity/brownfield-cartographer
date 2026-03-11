"""
tree-sitter grammar loading and query execution helpers.

Provides:
  _load_ts_language(lang_name)  — load a grammar, returns None on failure
  _get_grammar(lang_name)       — cached version of the above
  _make_parser(language)        — build a Parser for a grammar
  _run_query(language, query_str, root_node) — execute a query, return captures
  _text(node)                   — decode node bytes safely
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Lazy cache: populated on first use, never reloaded
_GRAMMAR_CACHE: dict[str, Any] = {}


def _load_ts_language(lang_name: str) -> Optional[Any]:
    """
    Load a tree-sitter Language object for *lang_name*.

    Returns None — never raises — when the grammar package is not installed.
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


def _get_grammar(lang_name: str) -> Optional[Any]:
    if lang_name not in _GRAMMAR_CACHE:
        _GRAMMAR_CACHE[lang_name] = _load_ts_language(lang_name)
    return _GRAMMAR_CACHE[lang_name]


def _make_parser(language: Any) -> Any:
    from tree_sitter import Parser  # type: ignore[import]
    return Parser(language)


def _run_query(language: Any, query_str: str, root_node: Any) -> dict[str, list[Any]]:
    """
    Execute a tree-sitter query and return all captures merged by name.

    Uses tree-sitter 0.25 API: Query(lang, str) + QueryCursor.matches().
    Returns empty dict on any failure.
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
        logger.debug("Query failed: %s", exc)
        return {}


def _text(node: Any) -> str:
    """Decode a tree-sitter node's text bytes safely."""
    if node is None:
        return ""
    try:
        return node.text.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
