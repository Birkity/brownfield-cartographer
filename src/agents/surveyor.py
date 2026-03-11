"""
Surveyor Agent — Phase 1 of the Brownfield Cartographer pipeline.

The Surveyor performs deep static analysis of the codebase:
  1. Walks all files via FileInventory
  2. Routes each file to the correct tree-sitter grammar (LanguageRouter)
  3. Calls TreeSitterAnalyzer.analyze_file() for AST-level extraction
  4. Enriches ModuleNodes with git velocity data
  5. Builds a NetworkX DiGraph of module imports
  6. Runs PageRank (architectural hubs) and SCC (circular dependencies)
  7. Marks dead-code candidates (modules with in-degree 0)
  8. Returns the populated KnowledgeGraph + a list of TraceEntry audit events

Phase 2 integration point (Hydrologist):
  - Call surveyor.run() first; pass its KnowledgeGraph into Hydrologist.run()
  - Hydrologist will add DatasetNodes and PRODUCES/CONSUMES edges

Phase 3 integration point (Semanticist):
  - After Surveyor builds the graph, Semanticist enriches each ModuleNode
    with purpose_statement + domain_cluster by iterating graph.all_modules()
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.analyzers.tree_sitter_analyzer import analyze_file
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import AnalysisMethod, Language, ModuleNode, TraceEntry
from src.utils.file_inventory import FileInventory
from src.utils.git_tools import GitVelocityResult, extract_git_velocity, get_last_commit_date

logger = logging.getLogger(__name__)


class SurveyorResult:
    """
    Output of a Surveyor run.

    Attributes:
        graph:   The populated KnowledgeGraph.
        trace:   Ordered list of audit events (will be written to cartography_trace.jsonl).
        stats:   High-level summary numbers for progress reporting.
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        trace: list[TraceEntry],
        stats: dict,
    ) -> None:
        self.graph = graph
        self.trace = trace
        self.stats = stats


class Surveyor:
    """
    Analyzes a repository's static structure and builds the module import graph.

    Usage::

        surveyor = Surveyor()
        result = surveyor.run(repo_root=Path("/path/to/repo"), velocity_days=30)
        result.graph.save(Path(".cartography/module_graph.json"))
    """

    def __init__(
        self,
        max_file_bytes: int = 512 * 1024,
        velocity_days: int = 30,
    ) -> None:
        self._max_file_bytes = max_file_bytes
        self._velocity_days = velocity_days

    def run(
        self,
        repo_root: Path,
        velocity_days: Optional[int] = None,
    ) -> SurveyorResult:
        """
        Execute the full Surveyor analysis on *repo_root*.

        Args:
            repo_root:      Absolute path to the local repository.
            velocity_days:  Override the default window for git velocity analysis.

        Returns:
            SurveyorResult containing the KnowledgeGraph and audit trace.
        """
        days = velocity_days if velocity_days is not None else self._velocity_days
        trace: list[TraceEntry] = []
        graph = KnowledgeGraph()

        logger.info("Surveyor starting analysis of %s", repo_root)
        start_time = datetime.utcnow()

        # ---- Step 1: inventory all files --------------------------------
        inventory = FileInventory()
        items = inventory.scan(repo_root, max_bytes=self._max_file_bytes)
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="file_inventory",
                target=str(repo_root),
                result=f"Found {len(items)} analyzable files",
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )

        # ---- Step 2: git velocity ---------------------------------------
        velocity: GitVelocityResult = extract_git_velocity(repo_root, days=days)
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="extract_git_velocity",
                target=str(repo_root),
                result=(
                    f"{len(velocity.commit_counts)} files with git activity in last {days}d"
                    if velocity.available
                    else "git unavailable — velocity data skipped"
                ),
                analysis_method=AnalysisMethod.GIT_ANALYSIS,
                error=None if velocity.available else "git not available",
            )
        )

        # ---- Step 3: analyse each file ----------------------------------
        parsed_ok = 0
        parse_errors = 0
        grammar_missing = 0

        for item in items:
            rel_posix = item.rel_posix()
            module_node = analyze_file(
                abs_path=item.abs_path,
                rel_path=rel_posix,
                language=item.language,
            )

            # Enrich with git data
            module_node.change_velocity_30d = velocity.for_file(rel_posix)
            module_node.last_modified = get_last_commit_date(
                repo_root, item.rel_path
            )

            graph.add_module(module_node)

            if module_node.parse_error:
                if "not installed" in module_node.parse_error:
                    grammar_missing += 1
                else:
                    parse_errors += 1
                trace.append(
                    TraceEntry(
                        agent="Surveyor",
                        action="analyze_module",
                        target=rel_posix,
                        result="parse_error",
                        analysis_method=AnalysisMethod.STATIC_ANALYSIS,
                        error=module_node.parse_error,
                    )
                )
            else:
                parsed_ok += 1

        logger.info(
            "Surveyor: parsed %d files OK, %d grammar-missing, %d real errors",
            parsed_ok,
            grammar_missing,
            parse_errors,
        )

        # ---- Step 4: build import edges ----------------------------------
        edge_count = self._build_import_edges(graph, repo_root)
        dbt_edge_count = self._build_dbt_ref_edges(graph)
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="build_import_graph",
                target=str(repo_root),
                result=(
                    f"Added {edge_count} Python import edges and "
                    f"{dbt_edge_count} dbt {{{{ ref() }}}} edges"
                ),
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )

        # ---- Step 5: graph analytics ------------------------------------
        hubs = graph.hub_modules(top_n=10)
        cycles = graph.strongly_connected_components()
        dead = graph.dead_code_candidates()

        # Mark dead-code candidates on ModuleNodes
        for path in dead:
            mod = graph.get_module(path)
            if mod:
                mod.is_dead_code_candidate = True
                graph.add_module(mod)  # re-add to update graph node attrs

        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="pagerank_analysis",
                target="module_graph",
                result=(
                    f"Top hub: {hubs[0][0]} (score={hubs[0][1]:.4f})"
                    if hubs
                    else "No modules in graph"
                ),
                confidence=1.0,
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )
        trace.append(
            TraceEntry(
                agent="Surveyor",
                action="scc_analysis",
                target="module_graph",
                result=(
                    f"{len(cycles)} circular dependency cluster(s) detected"
                    if cycles
                    else "No circular dependencies detected"
                ),
                analysis_method=AnalysisMethod.STATIC_ANALYSIS,
            )
        )

        elapsed = (datetime.utcnow() - start_time).total_seconds()
        project_type = self._detect_project_type(repo_root)
        stats = {
            "project_type": project_type,
            "files_scanned": len(items),
            "files_parsed_ok": parsed_ok,
            "grammar_not_available": grammar_missing,
            "parse_errors": parse_errors,
            "import_edges": edge_count,
            "dbt_ref_edges": dbt_edge_count,
            "circular_dependency_clusters": len(cycles),
            "dead_code_candidates": len(dead),
            "top_hubs": hubs[:5],
            "high_velocity_files": velocity.top_files(10) if velocity.available else [],
            "pareto_core": velocity.pareto_core() if velocity.available else [],
            "elapsed_seconds": round(elapsed, 2),
        }

        logger.info(
            "Surveyor complete in %.1fs: %d modules, %d edges, %d cycles",
            elapsed,
            len(graph.all_modules()),
            edge_count,
            len(cycles),
        )
        return SurveyorResult(graph=graph, trace=trace, stats=stats)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_import_edges(self, graph: KnowledgeGraph, repo_root: Path) -> int:
        """
        Resolve imports in each module to graph edges.

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
            for suffix in (".py", ".pyi", ".java", ".kt", ".kts", ".scala", ".go", ".rs", ".cs", ".rb"):
                if dotted.endswith(suffix):
                    dotted = dotted[: -len(suffix)]
            dotted_to_path[dotted] = path

            # Also index by just the module name (last component) for fuzzy matching
            parts = dotted.split(".")
            if parts:
                dotted_to_path.setdefault(parts[-1], path)

        edges_added = 0
        for module in graph.all_modules():
            for imp in module.imports:
                if module.language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
                    target = self._resolve_js_import(
                        imp.module, module.path, tracked_paths
                    )
                else:
                    target = self._resolve_import(
                        imp.module, module.path, tracked_paths, dotted_to_path
                    )
                if target:
                    graph.add_import_edge(module.path, target)
                    edges_added += 1

        return edges_added

    def _resolve_js_import(
        self,
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
            return None  # External / scoped package — can't resolve to local file

        from pathlib import PurePosixPath
        import posixpath

        source_dir = "/".join(source_path.replace("\\", "/").split("/")[:-1])
        try:
            raw = str(PurePosixPath(source_dir) / module)
            # Normalise ../ segments so "src/components/../utils" → "src/utils"
            resolved = posixpath.normpath(raw).replace("\\", "/")
        except Exception:
            return None

        # Try each JS/TS file extension
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            candidate = resolved + ext
            if candidate in tracked_paths:
                return candidate

        # Try as a directory index file
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            candidate = resolved + "/index" + ext
            if candidate in tracked_paths:
                return candidate

        # Exact match (import already includes extension)
        if resolved in tracked_paths:
            return resolved

        return None

    def _resolve_import(
        self,
        import_module: str,
        source_path: str,
        tracked_paths: set[str],
        dotted_to_path: dict[str, str],
    ) -> Optional[str]:
        """
        Try to resolve an import string to a known module path.

        Resolution order:
        1. Exact dotted-name match in our index
        2. Relative import resolution from source file's directory
        3. Partial (suffix) match as a last resort

        Returns None for third-party or unresolvable imports.
        """
        # Strip leading dots for relative imports
        clean = import_module.lstrip(".")
        is_relative = import_module.startswith(".")
        dot_count = len(import_module) - len(clean)

        # ---- Exact match ----
        if clean in dotted_to_path:
            return dotted_to_path[clean]

        # ---- Relative import resolution ----
        if is_relative:
            parts = source_path.replace("\\", "/").split("/")
            # Go up dot_count - 1 levels from source file's directory
            if dot_count <= len(parts):
                base_parts = parts[: -(dot_count)]  # parent dir
                if clean:
                    candidate = "/".join(base_parts + clean.split(".")) + ".py"
                    if candidate in tracked_paths:
                        return candidate
                    # Try as a package __init__
                    candidate_init = "/".join(base_parts + clean.split(".")) + "/__init__.py"
                    if candidate_init in tracked_paths:
                        return candidate_init

        # ---- Partial suffix match (e.g. "utils.git_tools" matches "src/utils/git_tools.py") ----
        if clean:
            candidate_suffix = clean.replace(".", "/") + ".py"
            for path in tracked_paths:
                if path.endswith(candidate_suffix):
                    return path

        return None  # third-party or unresolvable

    def _build_dbt_ref_edges(self, graph: KnowledgeGraph) -> int:
        """
        Create DBT_REF edges from {{ ref('model_name') }} calls in SQL files.

        Algorithm:
        1. Index all SQL modules by their file stem:
           e.g. "models/staging/stg_orders.sql" → key "stg_orders"
        2. For each SQL module, iterate its dbt_refs (populated by dbt_helpers)
        3. Resolve each ref name to a SQL file path via the stem index
        4. Add a DBT_REF edge: current_file → referenced_model

        Returns the number of edges added.
        """
        # Build stem → path index for SQL modules
        stem_to_path: dict[str, str] = {}
        for mod in graph.all_modules():
            if mod.language == Language.SQL:
                from pathlib import PurePosixPath
                stem = PurePosixPath(mod.path).stem  # "stg_orders" from "models/staging/stg_orders.sql"
                stem_to_path[stem] = mod.path

        if not stem_to_path:
            return 0  # No SQL files tracked — skip

        edges_added = 0
        for module in graph.all_modules():
            if not module.dbt_refs:
                continue
            for ref_name in module.dbt_refs:
                target = stem_to_path.get(ref_name)
                if target and target != module.path:
                    graph.add_import_edge(module.path, target, edge_type="DBT_REF")
                    edges_added += 1

        return edges_added

    def _detect_project_type(self, repo_root: Path) -> str:
        """
        Detect the primary project type by scanning for well-known config files.

        Returns a lowercase string such as: "dbt", "django", "fastapi",
        "apache-airflow", "react", "nextjs", "angular", "vue", "express",
        "go", "rust", "java-maven", "java-gradle", "ruby-rails", "pyspark",
        "python", "node", or "unknown".
        """
        import json as _json

        # Ordered list of (filename, project_type) for direct config-file detection
        _ROOT_INDICATORS: list[tuple[str, str]] = [
            ("dbt_project.yml",    "dbt"),
            ("dbt_project.yaml",   "dbt"),
            ("airflow.cfg",        "apache-airflow"),
            ("pom.xml",            "java-maven"),
            ("build.gradle",       "java-gradle"),
            ("build.gradle.kts",   "java-gradle"),
            ("go.mod",             "go"),
            ("Cargo.toml",         "rust"),
            ("Gemfile",            "ruby"),
            ("mix.exs",            "elixir"),
        ]

        for filename, ptype in _ROOT_INDICATORS:
            if (repo_root / filename).exists():
                return ptype

        # package.json — check for known JS frameworks
        pkg_json = repo_root / "package.json"
        if pkg_json.exists():
            try:
                pkg = _json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                deps: set[str] = {
                    *pkg.get("dependencies", {}),
                    *pkg.get("devDependencies", {}),
                }
                for dep, ptype in [
                    ("next",           "nextjs"),
                    ("@angular/core",  "angular"),
                    ("vue",            "vue"),
                    ("react",          "react"),
                    ("express",        "express"),
                    ("fastify",        "fastify-node"),
                ]:
                    if dep in deps:
                        return ptype
            except Exception:
                pass
            return "node"

        # requirements.txt — detect Python web/data frameworks
        req_txt = repo_root / "requirements.txt"
        if req_txt.exists():
            try:
                reqs = req_txt.read_text(encoding="utf-8", errors="replace").lower()
                for keyword, ptype in [
                    ("django",           "django"),
                    ("fastapi",          "fastapi"),
                    ("flask",            "flask"),
                    ("apache-airflow",   "apache-airflow"),
                    ("pyspark",          "pyspark"),
                    ("dagster",          "dagster"),
                    ("prefect",          "prefect"),
                ]:
                    if keyword in reqs:
                        return ptype
            except Exception:
                pass
            return "python"

        # pyproject.toml — detect Python frameworks from dependencies
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

            deps_str = str(data.get("project", {}).get("dependencies", [])).lower()
            opts_str = str(data.get("project", {}).get("optional-dependencies", {})).lower()
            all_deps = deps_str + " " + opts_str
            for keyword, ptype in [
                ("django",          "django"),
                ("fastapi",         "fastapi"),
                ("flask",           "flask"),
                ("apache-airflow",  "apache-airflow"),
                ("pyspark",         "pyspark"),
                ("dagster",         "dagster"),
                ("prefect",         "prefect"),
            ]:
                if keyword in all_deps:
                    return ptype
            return "python"

        # manage.py at root → Django (even without requirements.txt)
        if (repo_root / "manage.py").exists():
            return "django"

        # Last resort: infer from dominant file extension
        py_count  = sum(1 for _ in repo_root.rglob("*.py")  if ".venv" not in str(_))
        java_count = sum(1 for _ in repo_root.rglob("*.java") if not any(s in str(_) for s in (".git", "target")))
        go_count  = sum(1 for _ in repo_root.rglob("*.go")  if ".git" not in str(_))
        rs_count  = sum(1 for _ in repo_root.rglob("*.rs")  if "target" not in str(_))
        ts_count  = sum(1 for _ in repo_root.rglob("*.ts")  if "node_modules" not in str(_))
        js_count  = sum(1 for _ in repo_root.rglob("*.js")  if "node_modules" not in str(_))

        ranked = sorted(
            [(py_count, "python"), (java_count, "java"), (go_count, "go"),
             (rs_count, "rust"), (ts_count + js_count, "node")],
            reverse=True,
        )
        if ranked[0][0] > 0:
            return ranked[0][1]
        return "unknown"

        """
        Create DBT_REF edges from {{ ref('model_name') }} calls in SQL files.

        Algorithm:
        1. Index all SQL modules by their file stem:
           e.g. "models/staging/stg_orders.sql" → key "stg_orders"
        2. For each SQL module, iterate its dbt_refs (populated by dbt_helpers)
        3. Resolve each ref name to a SQL file path via the stem index
        4. Add a DBT_REF edge: current_file → referenced_model

        Returns the number of edges added.
        """
        from src.models.nodes import Language  # avoid circular at module level

        # Build stem → path index for SQL modules
        stem_to_path: dict[str, str] = {}
        for mod in graph.all_modules():
            if mod.language == Language.SQL:
                from pathlib import PurePosixPath
                stem = PurePosixPath(mod.path).stem  # "stg_orders" from "models/staging/stg_orders.sql"
                stem_to_path[stem] = mod.path

        if not stem_to_path:
            return 0  # No SQL files tracked — skip

        edges_added = 0
        for module in graph.all_modules():
            if not module.dbt_refs:
                continue
            for ref_name in module.dbt_refs:
                target = stem_to_path.get(ref_name)
                if target and target != module.path:
                    graph.add_import_edge(module.path, target, edge_type="DBT_REF")
                    edges_added += 1

        return edges_added
