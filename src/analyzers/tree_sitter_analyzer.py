"""
TreeSitterAnalyzer: routes source files to the correct AST extractor.

Public API: analyze_file(), count_lines()

Grammar loading helpers live in ts_grammar.py.
Language-specific extractor functions live in ts_extractors.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.analyzers.notebook_utils import extract_notebook_source
from src.models.nodes import ImportInfo, Language, ModuleNode
from src.analyzers.ts_grammar import _get_grammar, _make_parser
from src.analyzers.ts_extractors import (
    _parse_python_imports,
    _parse_python_functions,
    _parse_python_classes,
    _compute_python_complexity,
    _parse_sql_table_refs,
    _parse_yaml_top_keys,
    _parse_js_imports,
    _parse_js_functions,
    extract_imports_by_regex,
)

logger = logging.getLogger(__name__)

_REGEX_ONLY_LANGUAGES = {
    Language.JAVA, Language.KOTLIN, Language.SCALA,
    Language.GO, Language.RUST, Language.CSHARP,
    Language.RUBY, Language.SHELL,
}

_GRAMMAR_KEY_MAP: dict[Language, str] = {
    Language.PYTHON: "python",
    Language.NOTEBOOK: "python",
    Language.SQL: "sql",
    Language.YAML: "yaml",
    Language.JAVASCRIPT: "javascript",
    Language.TYPESCRIPT: "typescript",
}


def count_lines(source: bytes) -> int:
    """Count non-empty lines of actual content."""
    return sum(1 for line in source.split(b"\n") if line.strip())


def _count_comment_ratio(text: str, language: Language) -> float:
    """Best-effort comment ratio by language using line-based heuristics."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0

    comment_lines = 0
    in_block_comment = False
    for raw_line in lines:
        line = raw_line.strip()

        if language in (Language.PYTHON, Language.YAML, Language.SHELL, Language.NOTEBOOK):
            if line.startswith("#"):
                comment_lines += 1
                continue

        if language in (Language.JAVASCRIPT, Language.TYPESCRIPT, Language.JAVA, Language.KOTLIN, Language.SCALA, Language.GO, Language.RUST, Language.CSHARP):
            if in_block_comment:
                comment_lines += 1
                if "*/" in line:
                    in_block_comment = False
                continue
            if line.startswith("//"):
                comment_lines += 1
                continue
            if line.startswith("/*"):
                comment_lines += 1
                if "*/" not in line:
                    in_block_comment = True
                continue

        if language == Language.SQL:
            if in_block_comment:
                comment_lines += 1
                if "*/" in line:
                    in_block_comment = False
                continue
            if line.startswith("--") or line.startswith("{#"):
                comment_lines += 1
                continue
            if line.startswith("/*"):
                comment_lines += 1
                if "*/" not in line:
                    in_block_comment = True
                continue

    return round(comment_lines / len(lines), 4)


def analyze_file(abs_path: Path, rel_path: str, language: Language) -> ModuleNode:
    """
    Parse a single source file and return a populated ModuleNode.

    This is the primary entry point called by the Surveyor agent.
    Never raises: all failures are captured in ``ModuleNode.parse_error``.
    """
    node = ModuleNode(path=rel_path, abs_path=str(abs_path), language=language)

    try:
        if language == Language.NOTEBOOK:
            source = extract_notebook_source(abs_path).rendered_code.encode("utf-8")
        else:
            source = abs_path.read_bytes()
    except OSError as exc:
        node.parse_error = f"Could not read file: {exc}"
        logger.warning("Cannot read %s: %s", abs_path, exc)
        return node

    node.lines_of_code = count_lines(source)
    node.comment_ratio = _count_comment_ratio(source.decode("utf-8", errors="replace"), language)

    # dbt ref() extraction for SQL (regex, no grammar needed) — runs before
    # grammar check so dbt refs are captured even without tree-sitter-sql.
    if language == Language.SQL:
        from src.analyzers.dbt_helpers import extract_dbt_refs
        node.dbt_refs = extract_dbt_refs(source.decode("utf-8", errors="replace"))

    # Regex-only languages (no AST parsing)
    if language in _REGEX_ONLY_LANGUAGES:
        if language != Language.SHELL:
            node.imports = extract_imports_by_regex(
                source.decode("utf-8", errors="replace"), language
            )
        return node

    grammar_key = _GRAMMAR_KEY_MAP.get(language)
    if grammar_key is None:
        node.parse_error = f"No grammar mapping for language {language}"
        return node

    ts_lang = _get_grammar(grammar_key)
    if ts_lang is None:
        node.parse_error = f"Grammar package for {grammar_key!r} not installed"
        logger.debug("No grammar for %s (%s)", rel_path, grammar_key)
        return node

    try:
        parser = _make_parser(ts_lang)
        tree = parser.parse(source)
        root = tree.root_node
    except Exception as exc:
        node.parse_error = f"tree-sitter parse error: {exc}"
        logger.warning("Parse error in %s: %s", rel_path, exc)
        return node

    try:
        if language in (Language.PYTHON, Language.NOTEBOOK):
            node.imports = _parse_python_imports(root, rel_path)
            node.functions = _parse_python_functions(root, rel_path)
            node.classes = _parse_python_classes(root, rel_path)
            node.complexity_score = _compute_python_complexity(root)

        elif language == Language.SQL:
            table_refs = _parse_sql_table_refs(root)
            node.imports = [ImportInfo(module=t, line=0) for t in table_refs]

        elif language == Language.YAML:
            node.yaml_keys = _parse_yaml_top_keys(root)

        elif language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
            node.imports = _parse_js_imports(root, language)
            node.functions = _parse_js_functions(root, language, rel_path)

    except Exception as exc:
        node.parse_error = f"Extraction error: {exc}"
        logger.warning("Extraction failed for %s: %s", rel_path, exc, exc_info=True)

    return node
