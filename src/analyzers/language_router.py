"""
LanguageRouter: maps file extensions to Language enum values.

This is the single authoritative source for:
  - Which extensions the Cartographer processes
  - Which directories to skip
  - Why a file was skipped (for audit logging)

All agents and utilities that need to decide "should I look at this file?"
must go through the LanguageRouter instead of hardcoding extension checks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from src.models.nodes import Language

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extension → Language mapping
# ---------------------------------------------------------------------------

EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    # Python
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    # SQL (dbt models, raw SQL, stored procs)
    ".sql": Language.SQL,
    # YAML (Airflow, dbt schema.yml, CI configs)
    ".yml": Language.YAML,
    ".yaml": Language.YAML,
    # JavaScript
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    # TypeScript
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    # JVM / compiled (regex-based import extraction)
    ".java": Language.JAVA,
    ".scala": Language.SCALA,
    ".sc": Language.SCALA,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    # Systems languages
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".cs": Language.CSHARP,
    # Scripting
    ".rb": Language.RUBY,
    ".sh": Language.SHELL,
    ".bash": Language.SHELL,
    ".zsh": Language.SHELL,
    ".ipynb": Language.NOTEBOOK,
}

# Extensions we explicitly skip (not an error, just not useful to analyze)
_SKIP_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".so",
        ".dll",
        ".dylib",  # compiled
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".ico",
        ".webp",  # images
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",  # docs
        ".zip",
        ".tar",
        ".gz",
        ".whl",
        ".egg",  # archives
        ".map",  # JS source maps
        ".lock",  # any lockfile (poetry.lock, yarn.lock, etc.)
        ".csv",
        ".parquet",
        ".json",  # data files (lineage may reference, but we don't AST-parse)
    }
)

# Directories to never descend into
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",  # VCS metadata
        ".venv",
        "venv",
        "env",
        ".env",
        "virtualenv",  # Python envs
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",  # Python caches
        "node_modules",  # JS deps
        "dist",
        "build",
        ".build",
        "target",  # build outputs
        ".tox",
        ".nox",  # test envs
        ".cartography",  # our own output
        "site-packages",  # installed packages inside venv (belt-and-suspenders)
    }
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RouterResult:
    """Outcome of routing a single file path."""

    path: Path
    language: Language
    supported: bool
    reason: str = ""
    """Human-readable explanation when supported=False."""


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class LanguageRouter:
    """Routes files to their Language and decides which directories to skip."""

    def route(self, path: Path) -> RouterResult:
        """
        Classify *path* by language.

        Returns a RouterResult where ``supported=True`` means the Cartographer
        has a grammar for this file type.  ``supported=False`` is not an error —
        it just means we skip this file silently (logged at DEBUG level).
        """
        suffix = path.suffix.lower()

        if suffix in _SKIP_EXTENSIONS:
            return RouterResult(
                path=path,
                language=Language.UNKNOWN,
                supported=False,
                reason=f"Extension {suffix!r} is in the skip list",
            )

        if not suffix:
            return RouterResult(
                path=path,
                language=Language.UNKNOWN,
                supported=False,
                reason="No file extension",
            )

        language = EXTENSION_TO_LANGUAGE.get(suffix)
        if language is None:
            return RouterResult(
                path=path,
                language=Language.UNKNOWN,
                supported=False,
                reason=f"Unrecognized extension: {suffix!r}",
            )

        return RouterResult(path=path, language=language, supported=True)

    def should_skip_dir(self, dir_name: str) -> bool:
        """Return True if this directory name should be excluded from analysis."""
        return dir_name in SKIP_DIRS or dir_name.startswith(".")
