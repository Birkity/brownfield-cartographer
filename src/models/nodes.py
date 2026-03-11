"""
Pydantic data models for the Brownfield Cartographer knowledge graph.

These schemas are the shared contract between all agents.  Each agent
populates a different subset of fields; no agent breaks another's contract.

Phase ownership:
  Phase 1 (Surveyor)    — ModuleNode, FunctionNode, ClassNode, ImportInfo, TraceEntry
  Phase 2 (Hydrologist) — DatasetNode, TransformationNode
  Phase 3 (Semanticist) — purpose_statement / domain_cluster fields on ModuleNode
  Phase 4 (Archivist)   — consumes all of the above to write artifacts
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Language(str, Enum):
    """Supported source-file languages."""

    PYTHON = "python"
    SQL = "sql"
    YAML = "yaml"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    UNKNOWN = "unknown"


class StorageType(str, Enum):
    """Type of data artifact tracked in the lineage graph."""

    TABLE = "table"
    FILE = "file"
    STREAM = "stream"
    API = "api"


class AnalysisMethod(str, Enum):
    """How a particular piece of intelligence was derived."""

    STATIC_ANALYSIS = "static_analysis"
    LLM_INFERENCE = "llm_inference"
    GIT_ANALYSIS = "git_analysis"
    CONFIG_PARSING = "config_parsing"


# ---------------------------------------------------------------------------
# Sub-models (embedded inside ModuleNode)
# ---------------------------------------------------------------------------


class ImportInfo(BaseModel):
    """A single import statement found in a module."""

    module: str
    """Fully-qualified module name, e.g. 'os.path', 'pandas', '.utils'."""

    names: list[str] = Field(default_factory=list)
    """Specific names imported, e.g. ['DataFrame', 'Series']. Empty for bare imports."""

    alias: Optional[str] = None
    """Alias used in the importing file, e.g. 'np' for `import numpy as np`."""

    is_relative: bool = False
    """True for relative imports (`from . import foo`)."""

    line: int = 0
    """1-based line number of the import statement."""


class FunctionNode(BaseModel):
    """A function or method definition found in a module."""

    name: str
    qualified_name: str
    """Dot-separated qualified name, e.g. 'MyClass.my_method'."""

    parent_module: str
    """Relative path of the containing module from repo root."""

    signature: str = ""
    """Text representation of the signature, e.g. 'def foo(x: int) -> str'."""

    is_public_api: bool = True
    """False if the name starts with '_'."""

    line: int = 0
    end_line: int = 0
    docstring: Optional[str] = None

    # TODO Phase 3 (Semanticist): Add purpose_statement: Optional[str] = None
    # TODO Phase 3 (Semanticist): Add doc_drift_flag: bool = False
    # TODO Phase 3 (Surveyor/graph): Add call_count_within_repo: int = 0


class ClassNode(BaseModel):
    """A class definition found in a module."""

    name: str
    qualified_name: str
    parent_module: str

    bases: list[str] = Field(default_factory=list)
    """Names of parent classes, as they appear in source (not resolved)."""

    line: int = 0
    end_line: int = 0

    methods: list[str] = Field(default_factory=list)
    """Names of methods defined directly on this class."""

    docstring: Optional[str] = None

    # TODO Phase 3 (Semanticist): Add purpose_statement: Optional[str] = None


# ---------------------------------------------------------------------------
# Primary node types
# ---------------------------------------------------------------------------


class ModuleNode(BaseModel):
    """
    Represents a single source file in the codebase.

    Populated progressively by agents:
    - Phase 1 (Surveyor):    path, language, imports, functions, classes,
                              lines_of_code, change_velocity_30d, last_modified
    - Phase 3 (Semanticist): purpose_statement, domain_cluster, doc_drift_detected
    """

    path: str
    """Relative path from repo root (POSIX separators)."""

    abs_path: str
    """Absolute filesystem path."""

    language: Language = Language.UNKNOWN
    imports: list[ImportInfo] = Field(default_factory=list)
    functions: list[FunctionNode] = Field(default_factory=list)
    classes: list[ClassNode] = Field(default_factory=list)

    lines_of_code: int = 0
    complexity_score: float = 0.0
    """Approximated cyclomatic complexity (future: tree-sitter-based)."""

    change_velocity_30d: int = 0
    """Number of git commits touching this file in the last 30 days."""

    is_dead_code_candidate: bool = False
    """True if this module exports symbols but none are imported elsewhere."""

    last_modified: Optional[datetime] = None
    parse_error: Optional[str] = None
    """Non-None when tree-sitter parsing raised an exception (graceful degradation)."""

    dbt_refs: list[str] = Field(default_factory=list)
    """Model names referenced via {{ ref('model_name') }} in SQL files (Phase 1 dbt support)."""

    # TODO Phase 3 (Semanticist): purpose_statement: Optional[str] = None
    # TODO Phase 3 (Semanticist): domain_cluster: Optional[str] = None
    # TODO Phase 3 (Semanticist): doc_drift_detected: bool = False


class DatasetNode(BaseModel):
    """
    Represents a data artifact (table, file, stream, API endpoint).

    TODO Phase 2 (Hydrologist): populate from PythonDataFlowAnalyzer + SQLLineageAnalyzer.
    """

    name: str
    storage_type: StorageType = StorageType.TABLE
    schema_snapshot: Optional[dict[str, Any]] = None
    freshness_sla: Optional[str] = None
    owner: Optional[str] = None
    is_source_of_truth: bool = False


class TransformationNode(BaseModel):
    """
    A data transformation between datasets.

    TODO Phase 2 (Hydrologist): populate from SQL/Python analysis.
    """

    transformation_type: str
    """e.g. 'select', 'join', 'cte', 'python_pandas', 'pyspark_write'."""

    source_file: str
    line_range: tuple[int, int] = (0, 0)
    sql_query: Optional[str] = None
    source_datasets: list[str] = Field(default_factory=list)
    target_datasets: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Graph edge descriptors (used by KnowledgeGraph to annotate edges)
# ---------------------------------------------------------------------------


class ImportEdge(BaseModel):
    """Edge: source_module → target_module via an import statement."""

    source: str
    target: str
    import_count: int = 1
    """How many times source imports target (multiple import lines)."""


class ProducesEdge(BaseModel):
    """Edge: transformation → dataset (data lineage). Phase 2."""

    transformation_id: str
    dataset_name: str


class ConsumesEdge(BaseModel):
    """Edge: transformation ← dataset (upstream dependency). Phase 2."""

    transformation_id: str
    dataset_name: str


# ---------------------------------------------------------------------------
# Audit trace
# ---------------------------------------------------------------------------


class TraceEntry(BaseModel):
    """
    One entry in the cartography_trace.jsonl audit log.

    Every agent action that produces intelligence must emit a TraceEntry.
    The distinction between static_analysis and llm_inference matters for
    trust calibration when reading the Onboarding Brief.
    """

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent: str
    """Which agent produced this entry, e.g. 'Surveyor'."""

    action: str
    """What the agent did, e.g. 'analyze_module', 'extract_git_velocity'."""

    target: str
    """The file path or concept being analyzed."""

    result: str
    """One-line summary of what was found or produced."""

    confidence: Optional[float] = None
    """0.0–1.0 confidence score. None = not applicable (deterministic analysis)."""

    analysis_method: AnalysisMethod = AnalysisMethod.STATIC_ANALYSIS
    error: Optional[str] = None
    """Non-None if the action partially failed but was handled gracefully."""
