"""
Navigator agent - Phase 4 repository question answering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.agents.archivist import Archivist, RetrievedContext
from src.llm.model_router import ModelRouter, TaskType
from src.llm.ollama_client import ContextWindowBudget, OllamaClient
from src.llm.prompt_builder import (
    NAVIGATOR_REASONING_PROMPT,
    NAVIGATOR_REASONING_PROMPT_VERSION,
    NAVIGATOR_SYNTHESIS_PROMPT,
    NAVIGATOR_SYNTHESIS_PROMPT_VERSION,
    SYSTEM_NAVIGATOR_REASONING,
    SYSTEM_NAVIGATOR_SYNTHESIS,
)
from src.models.nodes import DayOneCitation

logger = logging.getLogger(__name__)


class QueryAnswer(BaseModel):
    question: str
    answer: str
    confidence: float = 0.0
    citations: list[DayOneCitation] = Field(default_factory=list)


@dataclass
class NavigatorResult:
    response: QueryAnswer
    query_type: str
    log_path: Optional[Path] = None
    models_used: dict[str, str] = field(default_factory=dict)
    reasoning_summary: str = ""


class Navigator:
    """Interprets questions, retrieves evidence, and returns grounded answers."""

    def __init__(
        self,
        artifact_root: Path,
        ollama_url: str = "http://localhost:11434",
        override_model: Optional[str] = None,
        timeout: int = 120,
    ) -> None:
        self.archivist = Archivist(artifact_root)
        self._budget = ContextWindowBudget()
        self._client = OllamaClient(base_url=ollama_url, timeout=timeout)
        self._router = ModelRouter(self._client, override_model=override_model)
        self._ollama_available = self._client.is_available()

    def answer_question(self, question: str, log_query: bool = True) -> NavigatorResult:
        query_type, entity = self._classify_question(question)
        context = self._retrieve_context(query_type, entity or question, question)
        reasoning_summary = ""
        models_used: dict[str, str] = {}

        if self._ollama_available and query_type in {
            "find_implementation", "trace_lineage", "blast_radius", "explain_module"
        }:
            reasoning_summary, reasoning_model, reasoning_confidence = self._reason_about_context(question, context)
            if reasoning_model:
                models_used["reasoning"] = reasoning_model
                context.confidence = max(context.confidence, reasoning_confidence * 0.9)

        response = self._synthesize_answer(question, context, reasoning_summary, models_used)
        log_path = None
        if log_query:
            log_path = self.archivist.write_query_log(
                question=question,
                answer_payload=response.model_dump(mode="json"),
                query_type=query_type,
                models_used=models_used,
            )

        return NavigatorResult(
            response=response,
            query_type=query_type,
            log_path=log_path,
            models_used=models_used,
            reasoning_summary=reasoning_summary,
        )

    def _classify_question(self, question: str) -> tuple[str, Optional[str]]:
        lower = question.lower().strip()

        if re.search(r"\bwhat does (this )?(repository|repo|codebase) do\b|\bhigh level\b", lower):
            return "repository_overview", None
        if re.search(r"\bmain data (pipelines|flows)\b|\bwhere does data come from\b", lower):
            return "main_pipelines", None
        if re.search(r"\bmost business logic\b|\bbusiness logic\b|\bhotspots\b", lower):
            return "business_logic_hotspots", None
        if re.search(r"\bwhat breaks if\b|\bblast radius\b", lower):
            entity = self.archivist.resolve_dataset_name(question) or self.archivist.resolve_module_path(question)
            if entity is None:
                match = re.search(r"(?:if|radius)\s+(.+?)(?:\s+changes?)?$", question, re.IGNORECASE)
                entity = match.group(1).strip(" ?") if match else None
            return "blast_radius", entity
        if re.search(r"\bupstream\b|\bdownstream\b|\bfeed\b|\bproduces\b|\bconsumes\b", lower):
            entity = self.archivist.resolve_dataset_name(question)
            return "trace_lineage", entity or question
        if re.search(r"\bexplain\b", lower):
            return "explain_module", self.archivist.resolve_module_path(question) or question
        if re.search(r"\bwhere is\b|\bimplementation\b", lower):
            concept = re.sub(r"^(where is|find|show me|implementation of)\s+", "", question, flags=re.IGNORECASE)
            return "find_implementation", concept.strip(" ?")
        return "repository_overview", None

    def _retrieve_context(self, query_type: str, entity: str, question: str) -> RetrievedContext:
        if query_type == "repository_overview":
            return self.archivist.repository_overview_context()
        if query_type == "main_pipelines":
            return self.archivist.main_pipelines_context()
        if query_type == "business_logic_hotspots":
            return self.archivist.business_logic_context()
        if query_type == "trace_lineage":
            direction = "upstream" if re.search(r"\bupstream|feed|produce", question, re.IGNORECASE) else "both"
            if re.search(r"\bdownstream|impact|break", question, re.IGNORECASE):
                direction = "downstream"
            return self.archivist.trace_lineage_context(entity, direction=direction)
        if query_type == "blast_radius":
            return self.archivist.blast_radius_context(entity)
        if query_type == "explain_module":
            return self.archivist.explain_module_context(entity)
        if query_type == "find_implementation":
            return self.archivist.find_implementation_context(entity)
        return self.archivist.repository_overview_context()

    def _reason_about_context(self, question: str, context: RetrievedContext) -> tuple[str, str, float]:
        prompt = NAVIGATOR_REASONING_PROMPT.format(
            question=question,
            query_type=context.query_type,
            prompt_version=NAVIGATOR_REASONING_PROMPT_VERSION,
            retrieved_summary=context.summary,
            facts_json=json.dumps(context.facts, indent=2, default=str),
            citations_json=json.dumps(
                [citation.model_dump(mode="json") for citation in context.citations],
                indent=2,
                default=str,
            ),
        )
        response, selection = self._router.generate(
            task=TaskType.QUERY_REASONING,
            prompt=prompt,
            system=SYSTEM_NAVIGATOR_REASONING,
            temperature=0.1,
            max_tokens=800,
            format_json=True,
        )
        self._budget.record(response)
        if not response.success:
            return "", "", 0.0
        payload = response.parse_json() or {}
        summary = str(payload.get("analysis_summary", "")).strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        return summary, selection.model if selection else "", confidence

    def _synthesize_answer(
        self,
        question: str,
        context: RetrievedContext,
        reasoning_summary: str,
        models_used: dict[str, str],
    ) -> QueryAnswer:
        citations = self._grounded_citations(context.citations)
        if self._ollama_available:
            prompt = NAVIGATOR_SYNTHESIS_PROMPT.format(
                question=question,
                query_type=context.query_type,
                prompt_version=NAVIGATOR_SYNTHESIS_PROMPT_VERSION,
                retrieved_summary=(reasoning_summary + "\n\n" + context.summary).strip(),
                facts_json=json.dumps(context.facts, indent=2, default=str),
                citations_json=json.dumps(
                    [citation.model_dump(mode="json") for citation in citations],
                    indent=2,
                    default=str,
                ),
            )
            response, selection = self._router.generate(
                task=TaskType.QUERY_SYNTHESIS,
                prompt=prompt,
                system=SYSTEM_NAVIGATOR_SYNTHESIS,
                temperature=0.1,
                max_tokens=1200,
                format_json=True,
            )
            self._budget.record(response)
            if response.success:
                payload = response.parse_json() or {}
                answer_text = str(payload.get("answer", "")).strip()
                confidence = float(payload.get("confidence", context.confidence or 0.5) or 0.5)
                if selection:
                    models_used["synthesis"] = selection.model
                if answer_text:
                    return QueryAnswer(
                        question=question,
                        answer=answer_text,
                        confidence=max(0.0, min(1.0, (confidence + context.confidence) / 2)),
                        citations=citations,
                    )

        return QueryAnswer(
            question=question,
            answer=context.summary,
            confidence=max(0.0, min(1.0, context.confidence)),
            citations=citations,
        )

    def _grounded_citations(self, citations: list[DayOneCitation]) -> list[DayOneCitation]:
        grounded: list[DayOneCitation] = []
        seen: set[tuple[str, Optional[int], Optional[int], str, str]] = set()
        for citation in citations:
            key = (
                citation.file_path,
                citation.line_start,
                citation.line_end,
                citation.evidence_type,
                citation.description,
            )
            if key in seen:
                continue
            seen.add(key)
            grounded.append(citation)
        return grounded[:8]
