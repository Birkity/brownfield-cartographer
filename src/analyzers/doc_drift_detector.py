"""
Documentation drift detector â€” compares code reality vs. documentation claims.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field, field_validator

from src.llm.model_router import ModelRouter, TaskType
from src.llm.ollama_client import ContextWindowBudget
from src.llm.prompt_builder import (
    DOC_DRIFT_PROMPT,
    DOC_DRIFT_PROMPT_VERSION,
    SYSTEM_CODE_ANALYST,
)
from src.models.nodes import SemanticEvidence

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
    evidence: list[SemanticEvidence] = Field(default_factory=list)
    confidence: float = 0.0
    model_used: str = ""
    prompt_version: str = DOC_DRIFT_PROMPT_VERSION
    generation_timestamp: Optional[datetime] = None
    error: Optional[str] = None
    has_documentation: bool = False
    documentation_missing: bool = False

    @field_validator("evidence", mode="before")
    @classmethod
    def _validate_evidence(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            return [{
                "source_phase": "phase3",
                "file_path": "",
                "line_start": None,
                "line_end": None,
                "extraction_method": "legacy_string",
                "description": text,
            }]
        return []


def _extract_documentation(abs_path: str, language: str) -> tuple[str, list[SemanticEvidence]]:
    """Extract docstrings and comments from a source file with line-aware evidence."""
    try:
        p = Path(abs_path)
        if not p.exists():
            return "", []
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", []

    parts: list[str] = []
    evidence: list[SemanticEvidence] = []
    lines = text.split("\n")

    def add_evidence(start: Optional[int], end: Optional[int], method: str, description: str) -> None:
        evidence.append(SemanticEvidence(
            source_phase="phase3",
            file_path=str(p),
            line_start=start,
            line_end=end,
            extraction_method=method,
            description=description,
        ))

    if language in ("python",):
        for pattern in (r'"""(.*?)"""', r"'''(.*?)'''"):
            for match in re.finditer(pattern, text, re.DOTALL):
                doc = match.group(1).strip()
                if not doc:
                    continue
                start = text[: match.start()].count("\n") + 1
                end = start + match.group(0).count("\n")
                parts.append(doc)
                add_evidence(start, end, "docstring_scan", "Python triple-quoted docstring")
        for line_no, line in enumerate(lines[:30], start=1):
            stripped = line.strip()
            if stripped.startswith("#") and not stripped.startswith("#!"):
                parts.append(stripped[1:].strip())
                add_evidence(line_no, line_no, "comment_scan", "Python comment")

    elif language in ("sql",):
        for match in re.finditer(r"/\*(.*?)\*/", text, re.DOTALL):
            doc = match.group(1).strip()
            if not doc:
                continue
            start = text[: match.start()].count("\n") + 1
            end = start + match.group(0).count("\n")
            parts.append(doc)
            add_evidence(start, end, "comment_scan", "SQL block comment")
        for line_no, line in enumerate(lines[:30], start=1):
            stripped = line.strip()
            if stripped.startswith("--"):
                parts.append(stripped[2:].strip())
                add_evidence(line_no, line_no, "comment_scan", "SQL line comment")

    elif language in ("yaml",):
        for line_no, line in enumerate(lines[:30], start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                parts.append(stripped[1:].strip())
                add_evidence(line_no, line_no, "comment_scan", "YAML comment")

    elif language in ("javascript", "typescript"):
        for match in re.finditer(r"/\*(.*?)\*/", text, re.DOTALL):
            doc = match.group(1).strip()
            if not doc:
                continue
            start = text[: match.start()].count("\n") + 1
            end = start + match.group(0).count("\n")
            parts.append(doc)
            add_evidence(start, end, "comment_scan", "JS/TS block comment")
        for line_no, line in enumerate(lines[:30], start=1):
            stripped = line.strip()
            if stripped.startswith("//"):
                parts.append(stripped[2:].strip())
                add_evidence(line_no, line_no, "comment_scan", "JS/TS line comment")

    doc_text = "\n".join(part for part in parts if part.strip())
    return (doc_text[:_MAX_DOC_CHARS] if doc_text else ""), evidence[:8]


def _read_numbered_code_excerpt(abs_path: str, max_chars: int = _MAX_CODE_CHARS) -> str:
    try:
        lines = Path(abs_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return "(could not read)"
    numbered = "\n".join(f"{idx:04d}: {line}" for idx, line in enumerate(lines, start=1))
    return numbered[:max_chars]


def detect_drift_single(
    module: "ModuleNode",
    purpose_result: "PurposeResult",
    router: ModelRouter,
    budget: ContextWindowBudget,
) -> DriftResult:
    """Detect documentation drift for a single module via LLM."""
    documentation, doc_evidence = _extract_documentation(module.abs_path, module.language.value)

    if not documentation.strip():
        return DriftResult(
            file_path=module.path,
            drift_level="no_drift",
            explanation="No inline documentation found â€” documentation is missing.",
            evidence=[SemanticEvidence(
                source_phase="phase3",
                file_path=module.path,
                line_start=None,
                line_end=None,
                extraction_method="documentation_scan",
                description="No inline documentation found in the module.",
            )],
            confidence=1.0,
            has_documentation=False,
            documentation_missing=True,
            generation_timestamp=datetime.now(timezone.utc),
        )

    if not purpose_result.purpose_statement:
        return DriftResult(
            file_path=module.path,
            drift_level="no_drift",
            explanation="No purpose statement available for comparison.",
            evidence=doc_evidence,
            confidence=0.2,
            has_documentation=True,
            generation_timestamp=datetime.now(timezone.utc),
        )

    prompt = DOC_DRIFT_PROMPT.format(
        file_path=module.path,
        purpose_statement=purpose_result.purpose_statement,
        prompt_version=DOC_DRIFT_PROMPT_VERSION,
        documentation=documentation,
        code_snippet=_read_numbered_code_excerpt(module.abs_path),
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
            evidence=doc_evidence,
            has_documentation=True,
            generation_timestamp=datetime.now(timezone.utc),
        )

    parsed = resp.parse_json()
    if not isinstance(parsed, dict):
        return DriftResult(
            file_path=module.path,
            drift_level="possible_drift",
            explanation="LLM returned non-JSON response.",
            evidence=doc_evidence,
            confidence=0.2,
            model_used=resp.model,
            has_documentation=True,
            generation_timestamp=datetime.now(timezone.utc),
        )

    drift_level = parsed.get("drift_level", "no_drift")
    if drift_level not in ("no_drift", "possible_drift", "likely_drift"):
        drift_level = "possible_drift"

    return DriftResult(
        file_path=module.path,
        drift_level=drift_level,
        explanation=parsed.get("explanation", ""),
        stale_references=parsed.get("stale_references", []),
        evidence=parsed.get("evidence", []) or doc_evidence,
        confidence=float(parsed.get("confidence", 0.5)),
        model_used=resp.model,
        has_documentation=True,
        generation_timestamp=datetime.now(timezone.utc),
    )


def detect_all_drift(
    graph: "KnowledgeGraph",
    purpose_results: list["PurposeResult"],
    router: Optional[ModelRouter] = None,
    budget: Optional[ContextWindowBudget] = None,
    max_modules: int = 50,
) -> list[DriftResult]:
    """Detect documentation drift for all modules that have purpose statements.

    When ``router`` is ``None``, performs a documentation-presence scan only
    (no LLM comparison â€” every file is flagged as ``documentation_missing=True``
    if it has no inline docs). This is useful even without a live LLM.

    Prioritizes modules with higher ``business_logic_score``.
    """
    purpose_lookup: dict[str, "PurposeResult"] = {
        pr.file_path: pr for pr in purpose_results if pr.purpose_statement
    }

    eligible: list[tuple["ModuleNode", "PurposeResult"]] = [
        (module, pr)
        for module in graph.all_modules()
        if (pr := purpose_lookup.get(module.path)) is not None
    ]
    eligible.sort(key=lambda item: item[1].business_logic_score, reverse=True)
    eligible = eligible[:max_modules]

    results: list[DriftResult] = []
    for i, (module, pr) in enumerate(eligible, 1):
        logger.info("Doc drift detection [%d/%d]: %s", i, len(eligible), module.path)

        if router is None or budget is None:
            documentation, doc_evidence = _extract_documentation(module.abs_path, module.language.value)
            has_doc = bool(documentation.strip())
            results.append(DriftResult(
                file_path=module.path,
                drift_level="no_drift",
                explanation="Documentation presence scan (no LLM available).",
                evidence=doc_evidence,
                confidence=0.0,
                has_documentation=has_doc,
                documentation_missing=not has_doc,
                generation_timestamp=datetime.now(timezone.utc),
            ))
        else:
            results.append(detect_drift_single(module, pr, router, budget))

    return results
