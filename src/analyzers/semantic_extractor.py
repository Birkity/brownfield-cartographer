"""
Semantic extractor — LLM-powered purpose statement generation for modules.

For each eligible module, gathers a bounded context window (code snippet,
structural metadata, lineage context) and asks the LLM for a structured
JSON response describing the module's business purpose.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from src.llm.model_router import ModelRouter, TaskType
from src.llm.ollama_client import ContextWindowBudget, OllamaResponse
from src.llm.prompt_builder import (
    PURPOSE_EXTRACTION_PROMPT,
    SYSTEM_CODE_ANALYST,
)

if TYPE_CHECKING:
    from src.graph.knowledge_graph import KnowledgeGraph
    from src.models.nodes import ModuleNode

logger = logging.getLogger(__name__)

_MAX_CODE_CHARS = 6000  # truncate code snippets to this length


class PurposeResult(BaseModel):
    """Validated result of a purpose extraction for one module."""

    file_path: str
    purpose_statement: str = ""
    business_logic_score: float = 0.0
    key_concepts: list[str] = Field(default_factory=list)
    evidence: str = ""
    confidence: float = 0.0
    model_used: str = ""
    is_fallback: bool = False
    error: Optional[str] = None


def _read_source_code(abs_path: str, max_chars: int = _MAX_CODE_CHARS) -> str:
    """Read source code from disk, truncated to max_chars."""
    try:
        p = Path(abs_path)
        if not p.exists() or not p.is_file():
            return ""
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated at {max_chars} chars]"
        return text
    except Exception:
        return ""


def _build_graph_context(module: "ModuleNode", graph: "KnowledgeGraph") -> str:
    """Summarize the module's position in the graph for the prompt."""
    parts: list[str] = []

    if module.is_hub:
        parts.append("This module is an ARCHITECTURAL HUB (top PageRank).")
    if module.in_cycle:
        parts.append("This module is in a CIRCULAR DEPENDENCY.")
    if module.is_entry_point:
        parts.append("This module is an ENTRY POINT (nothing imports it).")
    if module.is_dead_code_candidate:
        parts.append("This module appears to be DEAD CODE (no one imports it).")
    if module.dbt_refs:
        parts.append(f"dbt references: {', '.join(module.dbt_refs[:10])}")

    # Lineage context: check if this file is associated with any transformations
    for xform in graph.all_transformations():
        if xform.source_file and xform.source_file == module.path:
            sources = ", ".join(xform.source_datasets[:5]) if xform.source_datasets else "none"
            targets = ", ".join(xform.target_datasets[:5]) if xform.target_datasets else "none"
            parts.append(f"Transformation: reads [{sources}] → writes [{targets}]")
            break  # one is enough for context

    if module.change_velocity_30d > 0:
        parts.append(f"Git velocity: {module.change_velocity_30d} commits in last 30 days.")

    return " | ".join(parts) if parts else "No special graph position."


def _build_imports_summary(module: "ModuleNode") -> str:
    """Concise summary of imports for the prompt."""
    if module.dbt_refs:
        return f"dbt refs: {', '.join(module.dbt_refs[:8])}"
    if module.imports:
        names = [imp.module for imp in module.imports[:8]]
        extra = f" +{len(module.imports) - 8} more" if len(module.imports) > 8 else ""
        return f"imports: {', '.join(names)}{extra}"
    if module.yaml_keys:
        return f"YAML keys: {', '.join(module.yaml_keys[:8])}"
    return "none"


def extract_purpose(
    module: "ModuleNode",
    graph: "KnowledgeGraph",
    router: ModelRouter,
    budget: ContextWindowBudget,
) -> PurposeResult:
    """Extract a purpose statement for a single module via LLM.

    Returns a PurposeResult (always — never raises).
    """
    code = _read_source_code(module.abs_path)
    if not code:
        return PurposeResult(
            file_path=module.path,
            error="Could not read source code",
            confidence=0.0,
        )

    # Check token budget
    if not budget.can_fit(code):
        code = code[: budget.max_prompt_tokens * 4]  # rough truncation

    functions_str = ", ".join(f.name for f in module.functions[:10]) or "none"
    classes_str = ", ".join(c.name for c in module.classes[:10]) or "none"

    prompt = PURPOSE_EXTRACTION_PROMPT.format(
        file_path=module.path,
        language=module.language.value,
        role=module.role,
        lines_of_code=module.lines_of_code,
        functions=functions_str,
        classes=classes_str,
        imports_summary=_build_imports_summary(module),
        graph_context=_build_graph_context(module, graph),
        max_chars=_MAX_CODE_CHARS,
        code_snippet=code,
    )

    resp, selection = router.generate(
        task=TaskType.PURPOSE_EXTRACTION,
        prompt=prompt,
        system=SYSTEM_CODE_ANALYST,
        temperature=0.1,
        max_tokens=1024,
        format_json=True,
    )
    budget.record(resp)

    if not resp.success:
        return PurposeResult(
            file_path=module.path,
            model_used=resp.model,
            error=resp.error,
            confidence=0.0,
        )

    parsed = resp.parse_json()
    if parsed is None:
        return PurposeResult(
            file_path=module.path,
            purpose_statement=resp.text[:500].strip(),
            model_used=resp.model,
            confidence=0.3,
            evidence="raw text (JSON parse failed)",
            is_fallback=selection.is_fallback if selection else False,
        )

    return PurposeResult(
        file_path=module.path,
        purpose_statement=parsed.get("purpose_statement", ""),
        business_logic_score=float(parsed.get("business_logic_score", 0.0)),
        key_concepts=parsed.get("key_concepts", []),
        evidence=parsed.get("evidence", ""),
        confidence=float(parsed.get("confidence", 0.5)),
        model_used=resp.model,
        is_fallback=selection.is_fallback if selection else False,
    )


def extract_all_purposes(
    graph: "KnowledgeGraph",
    router: ModelRouter,
    budget: ContextWindowBudget,
    max_modules: int = 100,
) -> list[PurposeResult]:
    """Extract purpose statements for all eligible modules.

    Skips modules that are too small (< 3 lines) or config-only YAML.
    Returns results ordered by business_logic_score descending.
    """
    modules = graph.all_modules()
    # Filter: skip trivial files
    eligible = [
        m for m in modules
        if m.lines_of_code >= 3
        and not (m.language.value == "yaml" and m.role == "config" and m.lines_of_code < 10)
    ]
    # Prioritize: hubs and high-role modules first
    eligible.sort(
        key=lambda m: (
            m.is_hub,
            m.role in ("mart", "staging", "intermediate", "source"),
            m.lines_of_code,
        ),
        reverse=True,
    )
    eligible = eligible[:max_modules]

    results: list[PurposeResult] = []
    for i, module in enumerate(eligible, 1):
        logger.info(
            "Purpose extraction [%d/%d]: %s", i, len(eligible), module.path,
        )
        result = extract_purpose(module, graph, router, budget)
        results.append(result)

    # Sort by business logic score descending
    results.sort(key=lambda r: r.business_logic_score, reverse=True)
    return results
