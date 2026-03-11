"""
YAML / config file analyzer for data lineage enrichment.

Parses dbt YAML configuration files to extract:
  - source declarations  → DatasetNode(dataset_type='dbt_source')
  - model schema / columns / descriptions → enrichment for existing datasets
  - dbt_project.yml metadata → project context

Uses the ``yaml_keys`` field already populated on ModuleNode by the Surveyor
to route YAML files to the correct parsing logic without re-reading the file
from disk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SourceDeclaration:
    """A dbt source declared in a YAML file."""

    schema_name: str
    table_name: str
    description: str = ""
    columns: list[str] = field(default_factory=list)
    source_file: str = ""
    """Relative path of the YAML file that declares this source."""


@dataclass
class ModelSchema:
    """Schema information for a dbt model from YAML."""

    model_name: str
    description: str = ""
    columns: list[str] = field(default_factory=list)
    source_file: str = ""


@dataclass
class SeedInfo:
    """A dbt seed declared in YAML or detected in seeds/ directory."""

    name: str
    description: str = ""
    source_file: str = ""


@dataclass
class ConfigAnalysisResult:
    """Aggregated results from config/YAML analysis across all files."""

    sources: list[SourceDeclaration] = field(default_factory=list)
    model_schemas: list[ModelSchema] = field(default_factory=list)
    seeds: list[SeedInfo] = field(default_factory=list)
    project_name: Optional[str] = None
    project_version: Optional[str] = None
    errors: list[str] = field(default_factory=list)


def analyze_yaml_file(
    file_path: Path,
    rel_path: str,
    yaml_keys: list[str],
) -> ConfigAnalysisResult:
    """
    Parse a single YAML file for data-lineage-relevant information.

    Uses ``yaml_keys`` (already extracted by Phase 1 Surveyor) to determine
    the file's role before reading the full content.
    """
    result = ConfigAnalysisResult()

    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        docs = list(yaml.safe_load_all(text))
    except Exception as exc:
        result.errors.append(f"YAML parse error in {rel_path}: {exc}")
        return result

    for doc in docs:
        if not isinstance(doc, dict):
            continue

        # ---- dbt source declarations (sources.yml / __sources.yml) ----
        if "sources" in yaml_keys and "sources" in doc:
            _extract_sources(doc, rel_path, result)

        # ---- dbt model schemas (schema.yml) ----
        if "models" in yaml_keys and "models" in doc:
            _extract_model_schemas(doc, rel_path, result)

        # ---- dbt seeds ----
        if "seeds" in yaml_keys and "seeds" in doc:
            _extract_seeds(doc, rel_path, result)

        # ---- dbt_project.yml ----
        if "name" in yaml_keys and "version" in yaml_keys:
            result.project_name = doc.get("name")
            result.project_version = str(doc.get("version", ""))

    return result


def _extract_sources(
    doc: dict[str, Any],
    rel_path: str,
    result: ConfigAnalysisResult,
) -> None:
    """Extract source declarations from a YAML document with a 'sources' key."""
    sources_list = doc.get("sources", [])
    if not isinstance(sources_list, list):
        return

    for source_entry in sources_list:
        if not isinstance(source_entry, dict):
            continue
        schema_name = source_entry.get("name", "")
        tables = source_entry.get("tables", [])
        if not isinstance(tables, list):
            continue

        for table_entry in tables:
            if not isinstance(table_entry, dict):
                continue
            table_name = table_entry.get("name", "")
            if not table_name:
                continue

            columns = []
            for col in table_entry.get("columns", []):
                if isinstance(col, dict) and "name" in col:
                    columns.append(col["name"])

            result.sources.append(
                SourceDeclaration(
                    schema_name=schema_name,
                    table_name=table_name,
                    description=table_entry.get("description", ""),
                    columns=columns,
                    source_file=rel_path,
                )
            )


def _extract_model_schemas(
    doc: dict[str, Any],
    rel_path: str,
    result: ConfigAnalysisResult,
) -> None:
    """Extract model schema info from a YAML document with a 'models' key."""
    models_list = doc.get("models", [])
    if not isinstance(models_list, list):
        return

    for model_entry in models_list:
        if not isinstance(model_entry, dict):
            continue
        model_name = model_entry.get("name", "")
        if not model_name:
            continue

        columns = []
        for col in model_entry.get("columns", []):
            if isinstance(col, dict) and "name" in col:
                columns.append(col["name"])

        result.model_schemas.append(
            ModelSchema(
                model_name=model_name,
                description=model_entry.get("description", ""),
                columns=columns,
                source_file=rel_path,
            )
        )


def _extract_seeds(
    doc: dict[str, Any],
    rel_path: str,
    result: ConfigAnalysisResult,
) -> None:
    """Extract seed declarations from a YAML document with a 'seeds' key."""
    seeds_list = doc.get("seeds", [])
    if not isinstance(seeds_list, list):
        return

    for seed_entry in seeds_list:
        if not isinstance(seed_entry, dict):
            continue
        name = seed_entry.get("name", "")
        if name:
            result.seeds.append(
                SeedInfo(
                    name=name,
                    description=seed_entry.get("description", ""),
                    source_file=rel_path,
                )
            )
