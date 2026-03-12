"""
Documentation drift detector — compares code reality vs. documentation claims.

For each module that has both a purpose statement (from LLM analysis) and
existing documentation (docstrings, comments, README references), asks the
LLM whether the documentation accurately describes the implementation.

Drift levels:
  - no_drift:       Docs match implementation well.
  - possible_drift: Minor mismatch or out-of-date phrasing.
  - likely_drift:   Docs contradict or significantly diverge from code.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from src.llm.model_router import ModelRouter, TaskType
from src.llm.ollama_client import ContextWindowBudget
from src.llm.prompt_builder import DOC_DRIFT_PROMPT, SYSTEM_CODE_ANALYST

if TYPE_CHECKING:
    from src.analyzers.semantic_extractor import PurposeResult
    from src.graph.knowledge_graph import KnowledgeGraph
    from src.models.nodes import ModuleNode

logger = logging.getLogger(__name__)

_MAX_DOC_CHARS = 3000
_MAX_CODE_CHARS = 4000


class DriftResult(BaseModel):
    """Documentation drift detection result for one module."""

    file_path: str
    drift_level: str = "no_drift"  # no_drift | possible_drift | likely_drift
    explanation: str = ""
    stale_references: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    model_used: str = ""
    error: Optional[str] = None
    has_documentation: bool = False


def _extract_documentation(abs_path: str, language: str) -> str:
    """Extract docstrings and comments from a source file."""
    try:
        p = Path(abs_path)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    parts: list[str] = []

    if language in ("python",):
        # Extract module-level docstring
        triple_q = re.findall(r'"""(.*?)"""', text, re.DOTALL)
        triple_s = re.findall(r"'''(.*?)'''", text, re.DOTALL)
        parts.extend(triple_q[:5])
        parts.extend(triple_s[:5])
        # Extract # comments (header block)
        for line in text.split("\n")[:30]:
            stripped = line.strip()
            if stripped.startswith("#") and not stripped.startswith("#!"):
                parts.append(stripped[1:].strip())

    elif language in ("sql",):
        # SQL comments: -- and /* */
        block_comments = re.findall(r'/\*(.*?)\*/', text, re.DOTALL)
        parts.extend(block_comments[:5])
        for line in text.split("\n")[:30]:
            stripped = line.strip()
            if stripped.startswith("--"):
                parts.append(stripped[2:].strip())

    elif language in ("yaml",):
        # YAML comments: #
        for line in text.split("\n")[:30]:
            stripped = line.strip()
            if stripped.startswith("#"):
                parts.append(stripped[1:].strip())

    elif language in ("javascript", "typescript"):
        # JS/TS: // and /* */
        block_comments = re.findall(r'/\*(.*?)\*/', text, re.DOTALL)
        parts.extend(block_comments[:5])
        for line in text.split("\n")[:30]:
            stripped = line.strip()
            if stripped.startswith("//"):
                parts.append(stripped[2:].strip())

    doc_text = "\n".join(p for p in parts if p.strip())
    return doc_text[:_MAX_DOC_CHARS] if doc_text else ""


def detect_drift_single(
    module: "ModuleNode",
    purpose_result: "PurposeResult",
    router: ModelRouter,
    budget: ContextWindowBudget,
) -> DriftResult:
    """Detect documentation drift for a single module via LLM."""
    documentation = _extract_documentation(module.abs_path, module.language.value)

    if not documentation.strip():
        return DriftResult(
            file_path=module.path,
            drift_level="no_drift",
            explanation="No documentation found to compare against.",
            confidence=0.5,
            has_documentation=False,
        )

    if not purpose_result.purpose_statement:
        return DriftResult(
            file_path=module.path,
            drift_level="no_drift",
            explanation="No purpose statement available for comparison.",
            confidence=0.2,
            has_documentation=True,
        )

    # Read a code excerpt
    try:
        code = Path(module.abs_path).read_text(encoding="utf-8", errors="replace")
        code = code[:_MAX_CODE_CHARS]
    except Exception:
        code = "(could not read)"

    prompt = DOC_DRIFT_PROMPT.format(
        file_path=module.path,
        purpose_statement=purpose_result.purpose_statement,
        documentation=documentation,
        code_snippet=code,
    )

    resp, selection = router.generate(
        task=TaskType.DOC_DRIFT_DETECTION,
        prompt=prompt,
        system=SYSTEM_CODE_ANALYST,
        temperature=0.1,
        max_tokens=512,
        format_json=True,
    )
    budget.record(resp)

    if not resp.success:
        return DriftResult(
            file_path=module.path,
            error=resp.error,
            has_documentation=True,
        )

    parsed = resp.parse_json()
    if not parsed:
        return DriftResult(
            file_path=module.path,
            drift_level="possible_drift",
            explanation="LLM returned non-JSON response.",
            confidence=0.2,
            model_used=resp.model,
            has_documentation=True,
        )

    drift_level = parsed.get("drift_level", "no_drift")
    if drift_level not in ("no_drift", "possible_drift", "likely_drift"):
        drift_level = "possible_drift"

    return DriftResult(
        file_path=module.path,
        drift_level=drift_level,
        explanation=parsed.get("explanation", ""),
        stale_references=parsed.get("stale_references", []),
        confidence=float(parsed.get("confidence", 0.5)),
        model_used=resp.model,
        has_documentation=True,
    )


def detect_all_drift(
    graph: "KnowledgeGraph",
    purpose_results: list["PurposeResult"],
    router: ModelRouter,
    budget: ContextWindowBudget,
    max_modules: int = 50,
) -> list[DriftResult]:
    """Detect documentation drift for all modules that have documentation.

    Prioritizes modules with higher business_logic_score.
    """
    # Build a lookup of purpose results by path
    purpose_lookup: dict[str, "PurposeResult"] = {
        pr.file_path: pr for pr in purpose_results if pr.purpose_statement
    }

    eligible: list[tuple["ModuleNode", "PurposeResult"]] = []
    for module in graph.all_modules():
        pr = purpose_lookup.get(module.path)
        if pr:
            eligible.append((module, pr))

    # Sort by business logic score descending — check high-value modules first
    eligible.sort(key=lambda x: x[1].business_logic_score, reverse=True)
    eligible = eligible[:max_modules]

    results: list[DriftResult] = []
    for i, (module, pr) in enumerate(eligible, 1):
        logger.info("Doc drift detection [%d/%d]: %s", i, len(eligible), module.path)
        result = detect_drift_single(module, pr, router, budget)
        results.append(result)

    return results
