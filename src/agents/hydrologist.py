"""
Hydrologist Agent — Phase 2 of the Brownfield Cartographer pipeline.

Maps data flow through the codebase by:
  1. Analysing SQL files for table-level lineage (sqlglot + dbt patterns)
  2. Parsing YAML configs for source declarations and model schemas
  3. Scanning Python files for data I/O patterns (pandas/spark/SQL execution)
  4. Building DatasetNode + TransformationNode records
  5. Wiring PRODUCES / CONSUMES edges in the KnowledgeGraph

Design principles:
  - Never fabricate lineage — only report what's provably in the code.
  - Mark dynamic or unresolved cases explicitly (confidence < 1.0, is_dynamic).
  - Generalise beyond dbt: detect file reads, SQL execution, API calls.
  - Emit TraceEntry records for every observation (audit trail).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from src.analyzers.config_analyzer import (
    ConfigAnalysisResult,
    analyze_yaml_file,
)
from src.analyzers.notebook_utils import extract_notebook_code
from src.analyzers.python_dataflow import analyze_python_file
from src.analyzers.sql_lineage import SQLLineageResult, analyze_sql_file
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import (
    AnalysisMethod,
    DatasetNode,
    Language,
    StorageType,
    TraceEntry,
    TransformationNode,
)

logger = logging.getLogger(__name__)


class HydrologistResult:
    """
    Output of a Hydrologist run.

    Attributes:
        graph:   The KnowledgeGraph enriched with lineage edges.
        trace:   Ordered list of audit events.
        stats:   Summary numbers for reporting.
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


class Hydrologist:
    """
    Phase 2 agent: data-flow and lineage analysis.

    Usage::

        hydrologist = Hydrologist()
        result = hydrologist.run(graph, repo_root)
        # graph is now enriched with DatasetNodes + lineage edges
    """

    def __init__(self) -> None:
        self._trace: list[TraceEntry] = []

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, graph: KnowledgeGraph, repo_root: Path) -> HydrologistResult:
        """
        Analyse data flow and lineage across the codebase.

        Args:
            graph:      KnowledgeGraph populated by Phase 1 (Surveyor).
            repo_root:  Absolute path to the repository root.

        Returns:
            HydrologistResult with enriched graph, trace, and stats.
        """
        t0 = time.time()
        self._trace = []
        modules = graph.all_modules()

        # Detect if this is a dbt project
        is_dbt = any(
            m.language == Language.YAML and "name" in m.yaml_keys
            and any(k in m.yaml_keys for k in ("profile", "model-paths", "source-paths"))
            for m in modules
        )
        if not is_dbt:
            # Fallback: check for dbt_project.yml file
            is_dbt = any(
                m.path.replace("\\", "/").endswith("dbt_project.yml")
                for m in modules
            )

        self._log_trace(
            "hydrologist_start",
            f"Starting Phase 2 analysis: {len(modules)} modules, is_dbt={is_dbt}",
        )

        # ---- Step 1: Parse YAML configs --------------------------------
        config_result = self._analyze_configs(graph, repo_root, modules)

        # ---- Step 2: Register seed datasets from seeds/ directory -------
        seeds_found = self._detect_seed_files(graph, repo_root)

        # ---- Step 3: Register source datasets from YAML -----------------
        sources_registered = self._register_sources(graph, config_result)

        # ---- Step 4: Analyse SQL files for lineage ----------------------
        sql_results = self._analyze_sql_files(graph, repo_root, modules, is_dbt)

        # ---- Step 5: Wire SQL lineage into graph -------------------------
        sql_edges = self._wire_sql_lineage(graph, sql_results, config_result)

        # ---- Step 6: Analyse Python files for data I/O ------------------
        py_results = self._analyze_python_files(graph, repo_root, modules)

        # ---- Step 7: Wire Python dataflow into graph --------------------
        py_edges = self._wire_python_dataflow(graph, py_results)

        elapsed = round(time.time() - t0, 2)

        stats = {
            "phase": "Phase 2 (Hydrologist)",
            "is_dbt_project": is_dbt,
            "yaml_configs_parsed": len(config_result.sources) + len(config_result.model_schemas),
            "sources_registered": sources_registered,
            "seeds_found": seeds_found,
            "sql_files_analyzed": len(sql_results),
            "python_files_analyzed": len(py_results),
            "datasets_total": len(graph.all_datasets()),
            "transformations_total": len(graph.all_transformations()),
            "produces_edges": sql_edges["produces"] + py_edges["produces"],
            "consumes_edges": sql_edges["consumes"] + py_edges["consumes"],
            "dynamic_transformations": sum(
                1 for t in graph.all_transformations() if t.is_dynamic
            ),
            "elapsed_seconds": elapsed,
        }

        self._log_trace(
            "hydrologist_complete",
            f"Phase 2 complete: {stats['datasets_total']} datasets, "
            f"{stats['transformations_total']} transformations, "
            f"{stats['produces_edges']} produces + {stats['consumes_edges']} consumes edges",
        )

        logger.info("Hydrologist complete in %.1fs", elapsed)
        return HydrologistResult(graph=graph, trace=self._trace, stats=stats)

    # ------------------------------------------------------------------
    # Step 1: YAML config analysis
    # ------------------------------------------------------------------

    def _analyze_configs(
        self,
        graph: KnowledgeGraph,
        repo_root: Path,
        modules: list,
    ) -> ConfigAnalysisResult:
        """Parse all YAML files for dbt config information."""
        from src.analyzers.config_analyzer import ConfigAnalysisResult

        merged = ConfigAnalysisResult()

        yaml_modules = [m for m in modules if m.language == Language.YAML]
        for mod in yaml_modules:
            abs_path = Path(mod.abs_path)
            if not abs_path.exists():
                continue

            result = analyze_yaml_file(abs_path, mod.path, mod.yaml_keys)
            merged.sources.extend(result.sources)
            merged.model_schemas.extend(result.model_schemas)
            merged.seeds.extend(result.seeds)
            merged.errors.extend(result.errors)
            if result.project_name and not merged.project_name:
                merged.project_name = result.project_name
                merged.project_version = result.project_version

        if merged.sources:
            self._log_trace(
                "config_sources_found",
                f"Found {len(merged.sources)} source declarations in YAML",
            )
        if merged.model_schemas:
            self._log_trace(
                "config_schemas_found",
                f"Found {len(merged.model_schemas)} model schemas in YAML",
            )

        return merged

    # ------------------------------------------------------------------
    # Step 2: Seed file detection
    # ------------------------------------------------------------------

    def _detect_seed_files(
        self,
        graph: KnowledgeGraph,
        repo_root: Path,
    ) -> int:
        """Register CSV files in seeds/ directories as seed datasets."""
        count = 0
        # Look for CSV files in seeds/ directories
        for item in repo_root.rglob("seeds/**/*.csv"):
            rel = item.relative_to(repo_root).as_posix()
            name = item.stem
            ds = DatasetNode(
                name=f"seed.{name}",
                storage_type=StorageType.FILE,
                dataset_type="dbt_seed",
                source_file=rel,
                confidence=1.0,
            )
            graph.add_dataset_node(ds)
            count += 1

        if count:
            self._log_trace("seeds_detected", f"Found {count} seed CSV files")
        return count

    # ------------------------------------------------------------------
    # Step 3: Register source datasets from YAML
    # ------------------------------------------------------------------

    def _register_sources(
        self,
        graph: KnowledgeGraph,
        config_result: ConfigAnalysisResult,
    ) -> int:
        """Create DatasetNode for each declared dbt source."""
        count = 0
        for src in config_result.sources:
            ds = DatasetNode(
                name=f"source.{src.schema_name}.{src.table_name}",
                storage_type=StorageType.TABLE,
                dataset_type="dbt_source",
                source_file=src.source_file,
                description=src.description,
                columns=src.columns,
                confidence=1.0,
            )
            graph.add_dataset_node(ds)
            count += 1
        return count

    # ------------------------------------------------------------------
    # Step 4: SQL lineage analysis
    # ------------------------------------------------------------------

    def _analyze_sql_files(
        self,
        graph: KnowledgeGraph,
        repo_root: Path,
        modules: list,
        is_dbt: bool,
    ) -> list[SQLLineageResult]:
        """Run SQL lineage analysis on all SQL files."""
        results: list[SQLLineageResult] = []
        sql_modules = [m for m in modules if m.language == Language.SQL]

        for mod in sql_modules:
            abs_path = Path(mod.abs_path)
            if not abs_path.exists():
                continue

            try:
                sql_text = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.debug("Could not read %s: %s", mod.path, exc)
                continue

            result = analyze_sql_file(sql_text, mod.path, is_dbt=is_dbt)
            results.append(result)

            if result.errors:
                for err in result.errors:
                    self._log_trace("sql_parse_error", err)

        if results:
            self._log_trace(
                "sql_analysis_complete",
                f"Analyzed {len(results)} SQL files",
            )

        return results

    # ------------------------------------------------------------------
    # Step 5: Wire SQL lineage into graph
    # ------------------------------------------------------------------

    def _wire_sql_lineage(
        self,
        graph: KnowledgeGraph,
        sql_results: list[SQLLineageResult],
        config_result: ConfigAnalysisResult,
    ) -> dict[str, int]:
        """Create TransformationNode + DatasetNode + edges from SQL results."""
        # Build a lookup of model schemas for enrichment
        model_desc: dict[str, str] = {}
        model_cols: dict[str, list[str]] = {}
        for ms in config_result.model_schemas:
            model_desc[ms.model_name] = ms.description
            model_cols[ms.model_name] = ms.columns

        edge_counts = {"produces": 0, "consumes": 0}

        for result in sql_results:
            # Skip macros — they're templates, not transformations
            if result.transformation_type == "dbt_macro":
                continue

            # Create the transformation node
            xform_id = f"sql:{result.source_file}"
            xform = TransformationNode(
                id=xform_id,
                transformation_type=result.transformation_type,
                source_file=result.source_file,
                line_range=result.line_range,
                sql_query=result.sql_preview,
                source_datasets=result.upstream_tables,
                target_datasets=result.downstream_tables,
                confidence=result.confidence,
                is_dynamic=result.is_dynamic,
            )
            graph.add_transformation_node(xform)

            # Register downstream datasets (outputs)
            for ds_name in result.downstream_tables:
                stem = ds_name.split(".")[-1] if "." in ds_name else ds_name
                ds = DatasetNode(
                    name=ds_name,
                    storage_type=StorageType.TABLE,
                    dataset_type=(
                        "dbt_model"
                        if result.transformation_type == "dbt_model"
                        else "table_ref"
                    ),
                    source_file=result.source_file,
                    description=model_desc.get(stem, ""),
                    columns=model_cols.get(stem, []),
                    confidence=result.confidence,
                )
                graph.add_dataset_node(ds)
                graph.add_produces_edge(
                    xform_id, ds_name,
                    confidence=result.confidence,
                    evidence={
                        "source_file": result.source_file,
                        "extraction_method": "sqlglot" if not result.is_dynamic else "sqlglot_dynamic",
                        "transformation_type": result.transformation_type,
                        "sql_preview": result.sql_preview[:120] if result.sql_preview else "",
                    },
                )
                edge_counts["produces"] += 1

            # Register upstream datasets (inputs) and create consumes edges
            for ds_name in result.upstream_tables:
                # Only create dataset node if it doesn't exist already
                if not graph.get_dataset(ds_name):
                    ds = DatasetNode(
                        name=ds_name,
                        storage_type=StorageType.TABLE,
                        dataset_type=_infer_dataset_type(ds_name),
                        confidence=result.confidence,
                    )
                    graph.add_dataset_node(ds)
                graph.add_consumes_edge(
                    xform_id, ds_name,
                    confidence=result.confidence,
                    evidence={
                        "source_file": result.source_file,
                        "extraction_method": "sqlglot" if not result.is_dynamic else "sqlglot_dynamic",
                        "transformation_type": result.transformation_type,
                    },
                )
                edge_counts["consumes"] += 1

        return edge_counts

    # ------------------------------------------------------------------
    # Step 6: Python dataflow analysis
    # ------------------------------------------------------------------

    def _analyze_python_files(
        self,
        graph: KnowledgeGraph,
        repo_root: Path,
        modules: list,
    ) -> list:
        """Run Python dataflow detection on all Python files."""
        from src.analyzers.python_dataflow import PythonDataflowResult

        results: list[PythonDataflowResult] = []
        py_modules = [m for m in modules if m.language in (Language.PYTHON, Language.NOTEBOOK)]

        for mod in py_modules:
            abs_path = Path(mod.abs_path)
            if not abs_path.exists():
                continue

            try:
                if mod.language == Language.NOTEBOOK:
                    source_text = extract_notebook_code(abs_path)
                else:
                    source_text = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            if not source_text.strip():
                continue

            result = analyze_python_file(source_text, mod.path)
            if result.records:
                results.append(result)

        if results:
            total_records = sum(len(r.records) for r in results)
            self._log_trace(
                "python_dataflow_complete",
                f"Found {total_records} data I/O patterns in {len(results)} Python files",
            )

        return results

    # ------------------------------------------------------------------
    # Step 7: Wire Python dataflow into graph
    # ------------------------------------------------------------------

    def _wire_python_dataflow(
        self,
        graph: KnowledgeGraph,
        py_results: list,
    ) -> dict[str, int]:
        """Create TransformationNode + DatasetNode + edges from Python results."""
        edge_counts = {"produces": 0, "consumes": 0}

        for result in py_results:
            for rec in result.records:
                xform_id = f"py:{rec.source_file}:{rec.line}"

                # Determine dataset name
                if rec.target:
                    ds_name = f"file.{rec.target}" if rec.io_type == "file_io" else rec.target
                else:
                    ds_name = f"dynamic.{rec.io_type}.{rec.source_file}:{rec.line}"

                # Determine dataset type
                if rec.io_type == "pandas":
                    ds_type = "file_read" if rec.direction == "read" else "file_write"
                    storage = StorageType.FILE
                elif rec.io_type == "spark":
                    ds_type = "table_ref"
                    storage = StorageType.TABLE
                elif rec.io_type == "sql_exec":
                    ds_type = "table_ref"
                    storage = StorageType.TABLE
                elif rec.io_type == "file_io":
                    ds_type = "file_read" if rec.direction == "read" else "file_write"
                    storage = StorageType.FILE
                else:
                    ds_type = "unknown"
                    storage = StorageType.FILE

                # Map Python io_type to transformation_type
                xform_type_map = {
                    "pandas": "python_pandas",
                    "spark": "python_spark",
                    "sql_exec": "python_sql_exec",
                    "file_io": "python_pandas",
                }

                xform = TransformationNode(
                    id=xform_id,
                    transformation_type=xform_type_map.get(rec.io_type, "unknown"),
                    source_file=rec.source_file,
                    line_range=(rec.line, rec.line),
                    source_datasets=[ds_name] if rec.direction == "read" else [],
                    target_datasets=[ds_name] if rec.direction == "write" else [],
                    confidence=rec.confidence,
                    is_dynamic=rec.is_dynamic,
                )
                graph.add_transformation_node(xform)

                ds = DatasetNode(
                    name=ds_name,
                    storage_type=storage,
                    dataset_type=ds_type,
                    source_file=rec.source_file,
                    confidence=rec.confidence,
                )
                graph.add_dataset_node(ds)

                if rec.direction == "read":
                    graph.add_consumes_edge(xform_id, ds_name)
                    edge_counts["consumes"] += 1
                else:
                    graph.add_produces_edge(xform_id, ds_name)
                    edge_counts["produces"] += 1

        return edge_counts

    # ------------------------------------------------------------------
    # Trace logging helper
    # ------------------------------------------------------------------

    def _log_trace(self, event: str, detail: str) -> None:
        """Emit a TraceEntry for the audit log."""
        entry = TraceEntry(
            timestamp=datetime.now(timezone.utc),
            agent="Hydrologist",
            action=event,
            target="lineage",
            result=detail,
            analysis_method=AnalysisMethod.STATIC_ANALYSIS,
        )
        self._trace.append(entry)
        logger.info("[Hydrologist] %s: %s", event, detail)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_dataset_type(name: str) -> str:
    """Infer dataset_type from the naming convention."""
    if name.startswith("source."):
        return "dbt_source"
    if name.startswith("model."):
        return "dbt_model"
    if name.startswith("seed."):
        return "dbt_seed"
    if name.startswith("file."):
        return "file_read"
    return "table_ref"
