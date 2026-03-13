"""
Helpers for extracting analyzable Python-like source from Jupyter notebooks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class NotebookSource:
    """Cell-aware notebook reconstruction for downstream analyzers."""

    rendered_code: str = ""
    rendered_to_cell_line: dict[int, tuple[int, int]] = field(default_factory=dict)
    skipped_non_python_cells: list[int] = field(default_factory=list)

    def map_rendered_line(self, rendered_line: int) -> tuple[int | None, int | None]:
        """Return (cell_index, cell_line) for a rendered line number."""
        return self.rendered_to_cell_line.get(rendered_line, (None, None))


def _comment_line(line: str, prefix: str) -> str:
    stripped = line.rstrip("\n")
    return f"# {prefix}{stripped}" if stripped else "#"


def _normalise_cell_lines(lines: list[str]) -> tuple[list[str], bool]:
    """
    Convert a notebook cell into Python-safe lines while preserving line count.

    Cells that begin with `%%...` are treated as non-Python cells and commented
    out entirely so tree-sitter can still analyze the notebook without failing.
    Single-line magics like `%sql` and shell escapes like `!pip install ...`
    are commented out individually.
    """
    first_meaningful = next((line.strip() for line in lines if line.strip()), "")
    if first_meaningful.startswith("%%"):
        return ([_comment_line(line, "NOTEBOOK_MAGIC ") for line in lines], True)

    rendered: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("%") or stripped.startswith("!"):
            rendered.append(_comment_line(line, "NOTEBOOK_MAGIC "))
        else:
            rendered.append(line.rstrip("\n"))
    return rendered, False


def extract_notebook_source(path: Path) -> NotebookSource:
    """
    Read a .ipynb file and reconstruct code cells into a Python-like document.

    The reconstructed text preserves code-cell ordering and records a mapping
    from rendered line numbers back to (cell index, cell-local line number).
    Returns an empty NotebookSource when the notebook cannot be parsed.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return NotebookSource()

    cells = payload.get("cells", [])
    if not isinstance(cells, list):
        return NotebookSource()

    rendered_lines: list[str] = []
    rendered_to_cell_line: dict[int, tuple[int, int]] = {}
    skipped_non_python_cells: list[int] = []

    for idx, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue

        source = cell.get("source", [])
        if isinstance(source, list):
            raw_lines = [str(part) for part in source]
        else:
            raw_lines = str(source).splitlines(keepends=True)

        if not raw_lines:
            continue

        normalised_lines, skipped = _normalise_cell_lines(raw_lines)
        if skipped:
            skipped_non_python_cells.append(idx)

        rendered_lines.append(f"# Notebook cell {idx}")
        header_line = len(rendered_lines)
        rendered_to_cell_line[header_line] = (idx, 0)

        for cell_line, line in enumerate(normalised_lines, start=1):
            rendered_lines.append(line)
            rendered_to_cell_line[len(rendered_lines)] = (idx, cell_line)

        rendered_lines.append("")

    rendered_code = "\n".join(rendered_lines).strip()
    if not rendered_code:
        return NotebookSource()

    return NotebookSource(
        rendered_code=rendered_code,
        rendered_to_cell_line=rendered_to_cell_line,
        skipped_non_python_cells=skipped_non_python_cells,
    )


def extract_notebook_code(path: Path) -> str:
    """
    Backward-compatible convenience wrapper returning only rendered code.
    """
    return extract_notebook_source(path).rendered_code
