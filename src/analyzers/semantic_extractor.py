"""
Semantic extractor — LLM-powered purpose statement generation for modules.

For each eligible module, gathers a bounded context window (code snippet,
structural metadata, lineage context) and asks the LLM for a structured
JSON response describing the module's business purpose.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from src.llm.model_router import ModelRouter, TaskType
from src.llm.ollama_client import ContextWindowBudget, OllamaResponse
from src.llm.prompt_builder import (
    BATCH_PURPOSE_EXTRACTION_PROMPT,
    PURPOSE_EXTRACTION_PROMPT,
    SYSTEM_CODE_ANALYST,
)

if TYPE_CHECKING:
    from src.graph.knowledge_graph import KnowledgeGraph
    from src.models.nodes import ModuleNode

logger = logging.getLogger(__name__)

_MAX_CODE_CHARS = 6000   # truncate code snippets to this length
_BATCH_SIZE = 4          # max files to group in one batch LLM call
_SMALL_FILE_LIMIT = 1500 # byte/char threshold below which a file qualifies for batching


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


def _python_skeleton(text: str) -> str:
    """Extract imports, signatures, and docstrings from Python source."""
    important: list[str] = []
    in_docstring = False
    for line in text.split("\n"):
        s = line.strip()
        if '"""' in s or "'''" in s:
            in_docstring = not in_docstring
            important.append(line)
            continue
        if in_docstring or s.startswith(("#", "import ", "from ", "def ", "class ", "@")):
            important.append(line)
    return "\n".join(important)


def _smart_truncate_code(text: str, language: str, max_chars: int = _MAX_CODE_CHARS) -> str:
    """Truncate code keeping the most semantically informative parts.

    - Python: extracts imports + class/def signatures (skeleton) rather than random truncation
    - SQL/YAML/others: head + tail truncation preserving header context and final output
    """
    if len(text) <= max_chars:
        return text

    truncated = len(text) - max_chars
    marker = f"\n... [{truncated} chars truncated] ...\n"
    head_budget = (max_chars * 2) // 3
    tail_budget = max_chars - head_budget - len(marker)

    if language == "python":
        skeleton = _python_skeleton(text)
        if len(skeleton) < max_chars:
            fill = max_chars - len(skeleton) - len(marker)
            if fill > 200:
                return text[:fill] + marker + skeleton
            return skeleton

    return text[:head_budget] + marker + text[max(0, len(text) - tail_budget):]


def _read_source_code(abs_path: str, language: str = "unknown", max_chars: int = _MAX_CODE_CHARS) -> str:
    """Read source code from disk using smart language-aware truncation."""
    try:
        p = Path(abs_path)
        if not p.exists() or not p.is_file():
            return ""
        text = p.read_text(encoding="utf-8", errors="replace")
        return _smart_truncate_code(text, language, max_chars)
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


def _heuristic_purpose_statement(module: "ModuleNode") -> "PurposeResult":
    """Generate a purpose statement from static metadata alone — no LLM required."""
    role = module.role
    path = module.path
    parent = Path(path).parent.name

    if module.dbt_refs:
        refs = ", ".join(module.dbt_refs[:5])
        extra = f" +{len(module.dbt_refs) - 5} more" if len(module.dbt_refs) > 5 else ""
        if role == "mart":
            statement = (
                f"dbt mart model that consolidates data from {refs}{extra} "
                f"into an analytics-ready table for business intelligence."
            )
            score = 0.7
        elif role == "staging":
            statement = (
                f"dbt staging model that cleans and standardizes source data "
                f"from {refs}{extra} for use by downstream mart models."
            )
            score = 0.5
        elif role == "macro":
            statement = (
                f"dbt macro referenced by {refs}{extra} providing reusable SQL logic."
            )
            score = 0.3
        else:
            statement = f"dbt {role} model referencing {refs}{extra}."
            score = 0.4
    elif module.functions:
        func_names = ", ".join(f.name for f in module.functions[:5])
        extra = f" +{len(module.functions) - 5} more" if len(module.functions) > 5 else ""
        statement = f"Python {role} module defining: {func_names}{extra}."
        score = 0.4
    elif module.yaml_keys:
        keys = ", ".join(module.yaml_keys[:4])
        statement = f"Schema configuration declaring {keys} in {parent}/."
        score = 0.1
    elif module.imports:
        mods = ", ".join(imp.module for imp in module.imports[:4])
        statement = f"Python {role} module importing {mods}."
        score = 0.2
    else:
        statement = f"{role.capitalize()} file in {parent}/."
        score = 0.1

    return PurposeResult(
        file_path=path,
        purpose_statement=statement,
        business_logic_score=score,
        key_concepts=[role, parent] if parent else [role],
        evidence="heuristic: role, dbt-refs, function names",
        confidence=0.3,
        model_used="heuristic",
    )


def _batch_extract_purposes(
    modules: list["ModuleNode"],
    graph: "KnowledgeGraph",
    router: ModelRouter,
    budget: ContextWindowBudget,
) -> list["PurposeResult"]:
    """Extract purpose statements for a group of small files in a single LLM call.

    Falls back to individual extraction or heuristic on parse failure.
    """
    files_data = []
    for m in modules:
        code = _read_source_code(m.abs_path, language=m.language.value, max_chars=_SMALL_FILE_LIMIT)
        files_data.append({
            "file_path": m.path,
            "language": m.language.value,
            "role": m.role,
            "functions": ", ".join(f.name for f in m.functions[:6]) or "none",
            "imports_summary": _build_imports_summary(m),
            "graph_context": _build_graph_context(m, graph),
            "code": code,
        })

    files_json = json.dumps(files_data, indent=1)
    prompt = BATCH_PURPOSE_EXTRACTION_PROMPT.format(files_json=files_json)

    if not budget.can_fit(prompt):
        return [extract_purpose(m, graph, router, budget) for m in modules]

    resp, selection = router.generate(
        task=TaskType.PURPOSE_EXTRACTION,
        prompt=prompt,
        system=SYSTEM_CODE_ANALYST,
        temperature=0.1,
        max_tokens=2048,
        format_json=True,
    )
    budget.record(resp)

    if not resp.success:
        return [_heuristic_purpose_statement(m) for m in modules]

    parsed = resp.parse_json()
    if not isinstance(parsed, list):
        return [extract_purpose(m, graph, router, budget) for m in modules]

    result_map = {item.get("file_path"): item for item in parsed if isinstance(item, dict)}
    results: list[PurposeResult] = []
    for m in modules:
        item = result_map.get(m.path)
        if not item:
            results.append(_heuristic_purpose_statement(m))
            continue
        results.append(PurposeResult(
            file_path=m.path,
            purpose_statement=str(item.get("purpose_statement", "")),
            business_logic_score=float(item.get("business_logic_score", 0.0)),
            key_concepts=list(item.get("key_concepts", [])),
            evidence=str(item.get("evidence", "")),
            confidence=float(item.get("confidence", 0.5)),
            model_used=resp.model,
            is_fallback=selection.is_fallback if selection else False,
        ))
    return results


def extract_purpose(
    module: "ModuleNode",
    graph: "KnowledgeGraph",
    router: ModelRouter,
    budget: ContextWindowBudget,
) -> PurposeResult:
    """Extract a purpose statement for a single module via LLM.

    Returns a PurposeResult (always — never raises).
    """
    code = _read_source_code(module.abs_path, language=module.language.value)
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
    router: Optional[ModelRouter],
    budget: ContextWindowBudget,
    max_modules: int = 100,
) -> list[PurposeResult]:
    """Extract purpose statements for all eligible modules.

    When ``router`` is provided, large/hub files are processed individually and
    smaller files are batched (``_BATCH_SIZE`` per call) to reduce LLM latency.
    When ``router`` is ``None``, heuristic purpose statements are generated so
    that domain clustering and reading-order generation still work.

    Returns results ordered by ``business_logic_score`` descending.
    """
    modules = graph.all_modules()
    eligible = [
        m for m in modules
        if m.lines_of_code >= 3
        and not (m.language.value == "yaml" and m.role == "config" and m.lines_of_code < 10)
    ]
    eligible.sort(
        key=lambda m: (
            m.is_hub,
            m.role in ("mart", "staging", "intermediate", "source"),
            m.lines_of_code,
        ),
        reverse=True,
    )
    eligible = eligible[:max_modules]

    if router is None:
        results = [_heuristic_purpose_statement(m) for m in eligible]
        logger.info("Heuristic purposes generated for %d modules (no LLM)", len(results))
        results.sort(key=lambda r: r.business_logic_score, reverse=True)
        return results

    individual: list["ModuleNode"] = []
    batchable: list["ModuleNode"] = []
    for m in eligible:
        try:
            byte_size = Path(m.abs_path).stat().st_size if m.abs_path else _SMALL_FILE_LIMIT + 1
        except OSError:
            byte_size = _SMALL_FILE_LIMIT + 1
        if m.is_hub or byte_size > _SMALL_FILE_LIMIT:
            individual.append(m)
        else:
            batchable.append(m)

    results: list[PurposeResult] = []
    total = len(eligible)
    done = 0

    for m in individual:
        done += 1
        logger.info("Purpose extraction [%d/%d]: %s", done, total, m.path)
        results.append(extract_purpose(m, graph, router, budget))

    for i in range(0, len(batchable), _BATCH_SIZE):
        batch = batchable[i : i + _BATCH_SIZE]
        done += len(batch)
        logger.info(
            "Purpose extraction [%d/%d (batch of %d)]: %s…",
            done, total, len(batch), batch[0].path,
        )
        results.extend(_batch_extract_purposes(batch, graph, router, budget))

    results.sort(key=lambda r: r.business_logic_score, reverse=True)
    return results
