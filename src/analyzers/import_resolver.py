"""
Import resolution and project-type detection helpers for the Surveyor.

All functions are pure (accept graph/paths as arguments, no Surveyor self dependency)
so they can be unit-tested and reused independently.
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path, PurePosixPath
from typing import Optional
import posixpath

from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import Language

logger = logging.getLogger(__name__)


def build_import_edges(graph: KnowledgeGraph, repo_root: Path) -> int:
    """
    Resolve imports in each module to directed graph edges.

    Python: dotted-name lookup + relative import resolution.
    JS/TS:  relative path resolution (./Foo, ../utils/bar).
    Other languages (Java, Go, Rust, etc.): dotted/path suffix matching.

    Returns the total number of edges added.
    """
    tracked_paths = {mod.path for mod in graph.all_modules()}

    # Build dotted-name → path index (used by Python and JVM languages)
    dotted_to_path: dict[str, str] = {}
    for path in tracked_paths:
        dotted = path.replace("/", ".").replace("\\", ".")
        for suffix in (".py", ".pyi", ".java", ".kt", ".kts", ".scala",
                       ".go", ".rs", ".cs", ".rb"):
            if dotted.endswith(suffix):
                dotted = dotted[: -len(suffix)]
        dotted_to_path[dotted] = path
        parts = dotted.split(".")
        if parts:
            dotted_to_path.setdefault(parts[-1], path)

    edges_added = 0
    for module in graph.all_modules():
        for imp in module.imports:
            if module.language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
                target = resolve_js_import(imp.module, module.path, tracked_paths)
            else:
                target = resolve_import(
                    imp.module, module.path, tracked_paths, dotted_to_path
                )
            if target:
                # Resolved to a known path in the repo → high confidence
                confidence = 1.0
                evidence = {
                    "source_file": module.path,
                    "line": imp.line,
                    "expression": imp.module,
                    "is_relative": imp.is_relative,
                    "extraction_method": "tree_sitter_ast",
                }
                graph.add_import_edge(
                    module.path, target,
                    confidence=confidence,
                    evidence=evidence,
                )
                edges_added += 1

    return edges_added


def resolve_js_import(
    module: str,
    source_path: str,
    tracked_paths: set[str],
) -> Optional[str]:
    """
    Resolve a JS/TS import specifier to a known module path.

    Handles:
      - Relative paths: ./Button, ../utils/helpers
      - Extension-less paths (tries .ts, .tsx, .js, .jsx)
      - Index files: ./components → ./components/index.tsx

    Returns None for bare package names (react, lodash, @scope/pkg).
    """
    if not module.startswith("."):
        return None

    source_dir = "/".join(source_path.replace("\\", "/").split("/")[:-1])
    try:
        raw = str(PurePosixPath(source_dir) / module)
        resolved = posixpath.normpath(raw).replace("\\", "/")
    except Exception:
        return None

    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        candidate = resolved + ext
        if candidate in tracked_paths:
            return candidate

    for ext in (".ts", ".tsx", ".js", ".jsx"):
        candidate = resolved + "/index" + ext
        if candidate in tracked_paths:
            return candidate

    if resolved in tracked_paths:
        return resolved

    return None


def resolve_import(
    import_module: str,
    source_path: str,
    tracked_paths: set[str],
    dotted_to_path: dict[str, str],
) -> Optional[str]:
    """
    Try to resolve an import string to a known module path.

    Resolution order:
    1. Exact dotted-name match in the index
    2. Relative import resolution from source file's directory
    3. Partial (suffix) match as a last resort

    Returns None for third-party or unresolvable imports.
    """
    clean = import_module.lstrip(".")
    is_relative = import_module.startswith(".")
    dot_count = len(import_module) - len(clean)

    if clean in dotted_to_path:
        return dotted_to_path[clean]

    if is_relative:
        parts = source_path.replace("\\", "/").split("/")
        if dot_count <= len(parts):
            base_parts = parts[:-dot_count]
            if clean:
                candidate = "/".join(base_parts + clean.split(".")) + ".py"
                if candidate in tracked_paths:
                    return candidate
                candidate_init = "/".join(base_parts + clean.split(".")) + "/__init__.py"
                if candidate_init in tracked_paths:
                    return candidate_init

    if clean:
        candidate_suffix = clean.replace(".", "/") + ".py"
        for path in tracked_paths:
            if path.endswith(candidate_suffix):
                return path

    return None


def build_dbt_ref_edges(graph: KnowledgeGraph) -> int:
    """
    Create DBT_REF edges from {{ ref('model_name') }} calls in SQL files.

    Builds a stem → path index for all SQL modules, then for each SQL
    module resolves its dbt_refs to target paths and adds edges.

    Returns the number of edges added.
    """
    stem_to_path: dict[str, str] = {}
    for mod in graph.all_modules():
        if mod.language == Language.SQL:
            stem = PurePosixPath(mod.path).stem
            stem_to_path[stem] = mod.path

    if not stem_to_path:
        return 0

    edges_added = 0
    for module in graph.all_modules():
        if not module.dbt_refs:
            continue
        for ref_name in module.dbt_refs:
            target = stem_to_path.get(ref_name)
            if target and target != module.path:
                graph.add_import_edge(
                    module.path, target,
                    edge_type="DBT_REF",
                    confidence=1.0,
                    evidence={
                        "source_file": module.path,
                        "expression": f"{{{{ ref('{ref_name}') }}}}",
                        "extraction_method": "dbt_jinja_regex",
                        "ref_name": ref_name,
                    },
                )
                edges_added += 1

    return edges_added


def detect_project_type(repo_root: Path) -> str:
    """
    Detect the primary project type by scanning for well-known config files.

    Returns a lowercase string such as: "dbt", "django", "fastapi",
    "apache-airflow", "react", "nextjs", "angular", "vue", "express",
    "go", "rust", "java-maven", "java-gradle", "ruby-rails", "pyspark",
    "python", "node", or "unknown".
    """
    _ROOT_INDICATORS: list[tuple[str, str]] = [
        ("dbt_project.yml",   "dbt"),
        ("dbt_project.yaml",  "dbt"),
        ("airflow.cfg",       "apache-airflow"),
        ("pom.xml",           "java-maven"),
        ("build.gradle",      "java-gradle"),
        ("build.gradle.kts",  "java-gradle"),
        ("go.mod",            "go"),
        ("Cargo.toml",        "rust"),
        ("Gemfile",           "ruby"),
        ("mix.exs",           "elixir"),
    ]

    for filename, ptype in _ROOT_INDICATORS:
        if (repo_root / filename).exists():
            return ptype

    pkg_json = repo_root / "package.json"
    if pkg_json.exists():
        try:
            pkg = _json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            deps: set[str] = {
                *pkg.get("dependencies", {}),
                *pkg.get("devDependencies", {}),
            }
            for dep, ptype in [
                ("next",          "nextjs"),
                ("@angular/core", "angular"),
                ("vue",           "vue"),
                ("react",         "react"),
                ("express",       "express"),
                ("fastify",       "fastify-node"),
            ]:
                if dep in deps:
                    return ptype
        except Exception:
            pass
        return "node"

    req_txt = repo_root / "requirements.txt"
    if req_txt.exists():
        try:
            reqs = req_txt.read_text(encoding="utf-8", errors="replace").lower()
            for keyword, ptype in [
                ("django",          "django"),
                ("fastapi",         "fastapi"),
                ("flask",           "flask"),
                ("apache-airflow",  "apache-airflow"),
                ("pyspark",         "pyspark"),
                ("dagster",         "dagster"),
                ("prefect",         "prefect"),
            ]:
                if keyword in reqs:
                    return ptype
        except Exception:
            pass
        return "python"

    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
                data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
            except ImportError:
                data = {}
        except Exception:
            data = {}

        all_deps = (
            str(data.get("project", {}).get("dependencies", [])).lower()
            + " "
            + str(data.get("project", {}).get("optional-dependencies", {})).lower()
        )
        for keyword, ptype in [
            ("django",         "django"),
            ("fastapi",        "fastapi"),
            ("flask",          "flask"),
            ("apache-airflow", "apache-airflow"),
            ("pyspark",        "pyspark"),
            ("dagster",        "dagster"),
            ("prefect",        "prefect"),
        ]:
            if keyword in all_deps:
                return ptype
        return "python"

    if (repo_root / "manage.py").exists():
        return "django"

    py_count  = sum(1 for _ in repo_root.rglob("*.py")   if ".venv" not in str(_))
    java_count = sum(1 for _ in repo_root.rglob("*.java") if not any(s in str(_) for s in (".git", "target")))
    go_count  = sum(1 for _ in repo_root.rglob("*.go")   if ".git" not in str(_))
    rs_count  = sum(1 for _ in repo_root.rglob("*.rs")   if "target" not in str(_))
    ts_count  = sum(1 for _ in repo_root.rglob("*.ts")   if "node_modules" not in str(_))
    js_count  = sum(1 for _ in repo_root.rglob("*.js")   if "node_modules" not in str(_))

    ranked = sorted(
        [(py_count, "python"), (java_count, "java"), (go_count, "go"),
         (rs_count, "rust"), (ts_count + js_count, "node")],
        reverse=True,
    )
    if ranked[0][0] > 0:
        return ranked[0][1]
    return "unknown"
