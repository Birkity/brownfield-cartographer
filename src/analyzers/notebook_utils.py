"""
Helpers for extracting analyzable Python-like source from Jupyter notebooks.
"""

from __future__ import annotations

import json
from pathlib import Path


def extract_notebook_code(path: Path) -> str:
    """
    Read a .ipynb file and concatenate code-cell sources into a single string.

    Returns an empty string when the notebook cannot be parsed.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""

    cells = payload.get("cells", [])
    if not isinstance(cells, list):
        return ""

    code_chunks: list[str] = []
    for idx, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", [])
        if isinstance(source, list):
            text = "".join(str(part) for part in source)
        else:
            text = str(source)
        if text.strip():
            code_chunks.append(f"# Notebook cell {idx}\n{text.rstrip()}")

    return "\n\n".join(code_chunks).strip()
