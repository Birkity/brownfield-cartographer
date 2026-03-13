"""
Semantic extractor — LLM-powered purpose statement generation for modules.

For each eligible module, gathers a bounded context window (code snippet,
structural metadata, lineage context) and asks the LLM for a structured
JSON response describing the module's business purpose.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field, field_validator

from src.llm.model_router import ModelRouter, TaskType
from src.llm.ollama_client import ContextWindowBudget, OllamaResponse
from src.llm.prompt_builder import (
    BATCH_PURPOSE_EXTRACTION_PROMPT,
    PURPOSE_EXTRACTION_PROMPT_VERSION,
    PURPOSE_EXTRACTION_PROMPT,
    SYSTEM_CODE_ANALYST,
)
from src.models.nodes import SemanticEvidence

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
    evidence: list[SemanticEvidence] = Field(default_factory=list)
    confidence: float = 0.0
    model_used: str = ""
    prompt_version: str = PURPOSE_EXTRACTION_PROMPT_VERSION
    generation_timestamp: Optional[datetime] = None
    is_fallback: bool = False
    error: Optional[str] = None

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


def _read_source_lines(abs_path: str) -> list[tuple[int, str]]:
    """Read source code from disk and preserve original line numbers."""
    try:
        p = Path(abs_path)
        if not p.exists() or not p.is_file():
            return []
        text = p.read_text(encoding="utf-8", errors="replace")
        return list(enumerate(text.splitlines(), start=1))
    except Exception:
        return []


def _python_skeleton(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Extract imports, signatures, and docstrings from Python source."""
    important: list[tuple[int, str]] = []
    in_docstring = False
    for line_no, line in lines:
        stripped = line.strip()
        if '"""' in stripped or "'''" in stripped:
            in_docstring = not in_docstring
            important.append((line_no, line))
            continue
        if in_docstring or stripped.startswith(("#", "import ", "from ", "def ", "class ", "@")):
            important.append((line_no, line))
    return important


def _format_numbered_lines(lines: list[tuple[int, str]]) -> str:
    return "\n".join(f"{line_no:04d}: {text}" for line_no, text in lines)


def _truncate_numbered_lines(
    lines: list[tuple[int, str]],
    language: str,
    max_chars: int = _MAX_CODE_CHARS,
) -> str:
    """Truncate numbered lines while preserving the most useful evidence."""
    if not lines:
        return ""

    full = _format_numbered_lines(lines)
    if len(full) <= max_chars:
        return full

    if language == "python":
        skeleton = _python_skeleton(lines)
        skeleton_text = _format_numbered_lines(skeleton)
        if skeleton and len(skeleton_text) <= max_chars:
            return skeleton_text

    if len(lines) <= 12:
        return full[:max_chars]

    head_count = max(1, len(lines) // 2)
    tail_count = max(1, len(lines) // 3)
    head = lines[:head_count]
    tail = lines[-tail_count:]
    skipped = max(0, tail[0][0] - head[-1][0] - 1)
    marker = (
        f"... [{skipped} lines omitted between {head[-1][0]} and {tail[0][0]}] ..."
        if skipped > 0 else "... [excerpt truncated] ..."
    )
    candidate = _format_numbered_lines(head) + "\n" + marker + "\n" + _format_numbered_lines(tail)

    while len(candidate) > max_chars and (len(head) > 3 or len(tail) > 3):
        if len(head) >= len(tail) and len(head) > 3:
            head = head[:-1]
        elif len(tail) > 3:
            tail = tail[1:]
        skipped = max(0, tail[0][0] - head[-1][0] - 1)
        marker = (
            f"... [{skipped} lines omitted between {head[-1][0]} and {tail[0][0]}] ..."
            if skipped > 0 else "... [excerpt truncated] ..."
        )
        candidate = _format_numbered_lines(head) + "\n" + marker + "\n" + _format_numbered_lines(tail)

    return candidate if len(candidate) <= max_chars else candidate[:max_chars]


def _read_source_code(abs_path: str, language: str = "unknown", max_chars: int = _MAX_CODE_CHARS) -> str:
    """Read source code from disk using smart line-aware truncation."""
    return _truncate_numbered_lines(_read_source_lines(abs_path), language, max_chars=max_chars)


def _line_span_for_text(lines: list[tuple[int, str]], needle: str) -> tuple[Optional[int], Optional[int]]:
    """Locate a text snippet in the source file and return a best-effort line span."""
    normalized = needle.strip().lower()
    if not normalized:
        return None, None
    for line_no, line in lines:
        if normalized in line.lower():
            return line_no, line_no
    return None, None


def _module_static_evidence(
    module: "ModuleNode",
    graph: "KnowledgeGraph",
) -> list[SemanticEvidence]:
    """Collect best-effort Phase 1 and Phase 2 evidence for a module."""
    lines = _read_source_lines(module.abs_path)
    evidence: list[SemanticEvidence] = []

    for xform in graph.all_transformations():
        if xform.source_file != module.path:
            continue
        evidence.append(SemanticEvidence(
            source_phase="phase2",
            file_path=module.path,
            line_start=xform.line_range[0] or None,
            line_end=xform.line_range[1] or None,
            extraction_method="phase2_lineage",
            description=(
                f"Transformation {xform.transformation_type} reads {', '.join(xform.source_datasets[:3]) or 'none'} "
                f"and writes {', '.join(xform.target_datasets[:3]) or 'none'}"
            ),
        ))

    for imp in module.imports[:3]:
        evidence.append(SemanticEvidence(
            source_phase="phase1",
            file_path=module.path,
            line_start=imp.line or None,
            line_end=imp.line or None,
            extraction_method="phase1_import",
            description=f"Import reference to {imp.module}",
        ))

    for fn in module.functions[:2]:
        evidence.append(SemanticEvidence(
            source_phase="phase1",
            file_path=module.path,
            line_start=fn.line or None,
            line_end=fn.end_line or fn.line or None,
            extraction_method="phase1_symbol",
            description=f"Function definition {fn.name}",
        ))

    for cls in module.classes[:2]:
        evidence.append(SemanticEvidence(
            source_phase="phase1",
            file_path=module.path,
            line_start=cls.line or None,
            line_end=cls.end_line or cls.line or None,
            extraction_method="phase1_symbol",
            description=f"Class definition {cls.name}",
        ))

    for key in module.yaml_keys[:3]:
        line_start, line_end = _line_span_for_text(lines, f"{key}:")
        evidence.append(SemanticEvidence(
            source_phase="phase1",
            file_path=module.path,
            line_start=line_start,
            line_end=line_end,
            extraction_method="phase1_yaml_key",
            description=f"YAML key {key}",
        ))

    for ref in module.dbt_refs[:3]:
        line_start, line_end = _line_span_for_text(lines, ref)
        evidence.append(SemanticEvidence(
            source_phase="phase1",
            file_path=module.path,
            line_start=line_start,
            line_end=line_end,
            extraction_method="phase1_dbt_ref",
            description=f"dbt reference {ref}",
        ))

    # Deduplicate while keeping the most precise entries first.
    deduped: list[SemanticEvidence] = []
    seen: set[tuple[str, Optional[int], Optional[int], str]] = set()
    for item in evidence:
        key = (item.description, item.line_start, item.line_end, item.extraction_method)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:6]


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


def _heuristic_purpose_statement(
    module: "ModuleNode",
    graph: Optional["KnowledgeGraph"] = None,
) -> "PurposeResult":
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

    evidence = _module_static_evidence(module, graph) if graph else []
    if not evidence:
        evidence = [SemanticEvidence(
            source_phase="phase3",
            file_path=path,
            line_start=None,
            line_end=None,
            extraction_method="heuristic",
            description="Heuristic summary derived from role, dbt refs, function names, and YAML keys.",
        )]

    return PurposeResult(
        file_path=path,
        purpose_statement=statement,
        business_logic_score=score,
        key_concepts=[role, parent] if parent else [role],
        evidence=evidence,
        confidence=0.3,
        model_used="heuristic",
        prompt_version=PURPOSE_EXTRACTION_PROMPT_VERSION,
        generation_timestamp=datetime.now(timezone.utc),
        is_fallback=True,
    )


def _normalize_llm_evidence(
    module: "ModuleNode",
    raw_evidence: Any,
) -> list[SemanticEvidence]:
    """Normalize LLM evidence payloads into structured evidence objects."""
    normalized: list[SemanticEvidence] = []
    candidates = raw_evidence if isinstance(raw_evidence, list) else [raw_evidence]
    for item in candidates:
        if isinstance(item, SemanticEvidence):
            evidence = item
        elif isinstance(item, dict):
            evidence = SemanticEvidence(
                source_phase=str(item.get("source_phase", "phase3")),
                file_path=str(item.get("file_path") or module.path),
                line_start=item.get("line_start"),
                line_end=item.get("line_end"),
                extraction_method=str(item.get("extraction_method", "llm_inference")),
                description=str(item.get("description", "")).strip(),
            )
        elif isinstance(item, str) and item.strip():
            evidence = SemanticEvidence(
                source_phase="phase3",
                file_path=module.path,
                line_start=None,
                line_end=None,
                extraction_method="llm_inference",
                description=item.strip(),
            )
        else:
            continue
        if evidence.description:
            normalized.append(evidence)
    return normalized


def _merge_evidence(
    module: "ModuleNode",
    graph: "KnowledgeGraph",
    raw_evidence: Any,
) -> list[SemanticEvidence]:
    """Merge static graph evidence with any structured LLM evidence."""
    merged: list[SemanticEvidence] = []
    seen: set[tuple[str, str, Optional[int], Optional[int], str]] = set()
    for item in _normalize_llm_evidence(module, raw_evidence) + _module_static_evidence(module, graph):
        key = (
            item.source_phase,
            item.file_path,
            item.line_start,
            item.line_end,
            item.description,
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:8]


def _purpose_result_from_payload(
    module: "ModuleNode",
    graph: "KnowledgeGraph",
    payload: dict[str, Any],
    model_used: str,
    is_fallback: bool,
) -> PurposeResult:
    """Build a validated purpose result from an LLM or heuristic payload."""
    return PurposeResult(
        file_path=module.path,
        purpose_statement=str(payload.get("purpose_statement", "")),
        business_logic_score=float(payload.get("business_logic_score", 0.0)),
        key_concepts=list(payload.get("key_concepts", [])),
        evidence=_merge_evidence(module, graph, payload.get("evidence")),
        confidence=float(payload.get("confidence", 0.5)),
        model_used=model_used,
        prompt_version=PURPOSE_EXTRACTION_PROMPT_VERSION,
        generation_timestamp=datetime.now(timezone.utc),
        is_fallback=is_fallback,
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
        return [_heuristic_purpose_statement(m, graph) for m in modules]

    parsed = resp.parse_json()
    if not isinstance(parsed, list):
        return [extract_purpose(m, graph, router, budget) for m in modules]

    result_map = {item.get("file_path"): item for item in parsed if isinstance(item, dict)}
    results: list[PurposeResult] = []
    for m in modules:
        item = result_map.get(m.path)
        if not item:
            results.append(_heuristic_purpose_statement(m, graph))
            continue
        results.append(_purpose_result_from_payload(
            m,
            graph,
            item,
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
            prompt_version=PURPOSE_EXTRACTION_PROMPT_VERSION,
            generation_timestamp=datetime.now(timezone.utc),
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
        prompt_version=PURPOSE_EXTRACTION_PROMPT_VERSION,
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
        fallback = _heuristic_purpose_statement(module, graph)
        fallback.model_used = resp.model or fallback.model_used
        fallback.error = resp.error
        return fallback

    parsed = resp.parse_json()
    if not isinstance(parsed, dict):
        fallback = _heuristic_purpose_statement(module, graph)
        fallback.model_used = resp.model or fallback.model_used
        fallback.error = "JSON parse failed"
        return fallback

    return _purpose_result_from_payload(
        module,
        graph,
        parsed,
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
        results = [_heuristic_purpose_statement(m, graph) for m in eligible]
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
