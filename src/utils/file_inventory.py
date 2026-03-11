"""
FileInventory: recursively walks a repository directory and returns a filtered
list of source files that the Cartographer knows how to analyze.

Skips binaries, generated files, lock files, large files, and internal dirs
(.git, node_modules, __pycache__, .cartography, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from src.analyzers.language_router import LanguageRouter
from src.models.nodes import Language

logger = logging.getLogger(__name__)

# Maximum file size in bytes to analyze (avoids reading minified JS, huge CSVs)
DEFAULT_MAX_BYTES = 512 * 1024  # 512 KB


@dataclass
class InventoryItem:
    """A single analyzable file in the repository."""

    abs_path: Path
    rel_path: Path
    """POSIX-style path relative to repo root."""

    language: Language
    size_bytes: int

    def rel_posix(self) -> str:
        """Return the POSIX string of the relative path (consistent across OSes)."""
        return self.rel_path.as_posix()


class FileInventory:
    """
    Walks a repo directory and returns a flat list of InventoryItems.

    Usage::

        inventory = FileInventory()
        items = inventory.scan(Path("/some/repo"))
        python_files = [i for i in items if i.language == Language.PYTHON]
    """

    def __init__(self) -> None:
        self._router = LanguageRouter()

    def scan(
        self,
        root: Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> list[InventoryItem]:
        """
        Recursively scan *root* and return all analyzable files.

        Files larger than *max_bytes* are skipped to avoid blowing the memory
        budget on generated or minified content.
        """
        items: list[InventoryItem] = []
        self._walk(root, root, max_bytes, items)

        by_lang: dict[str, int] = {}
        for item in items:
            by_lang[item.language.value] = by_lang.get(item.language.value, 0) + 1

        lang_summary = ", ".join(f"{v} {k}" for k, v in sorted(by_lang.items()))
        logger.info(
            "FileInventory: %d files in %s  [%s]",
            len(items),
            root,
            lang_summary,
        )
        return items

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _walk(
        self,
        root: Path,
        current: Path,
        max_bytes: int,
        items: list[InventoryItem],
    ) -> None:
        try:
            children = sorted(current.iterdir())
        except PermissionError:
            logger.debug("Permission denied: %s — skipping", current)
            return

        for child in children:
            if child.is_symlink():
                continue  # skip symlinks to avoid cycles

            if child.is_dir():
                if self._router.should_skip_dir(child.name):
                    logger.debug("Skipping dir: %s", child.name)
                    continue
                self._walk(root, child, max_bytes, items)

            elif child.is_file():
                route = self._router.route(child)
                if not route.supported:
                    logger.debug("Skipping %s: %s", child.name, route.reason)
                    continue

                try:
                    size = child.stat().st_size
                except OSError:
                    logger.debug("Cannot stat %s — skipping", child)
                    continue

                if size > max_bytes:
                    logger.debug(
                        "Skipping %s: %d bytes exceeds limit", child.name, size
                    )
                    continue

                items.append(
                    InventoryItem(
                        abs_path=child,
                        rel_path=child.relative_to(root),
                        language=route.language,
                        size_bytes=size,
                    )
                )
