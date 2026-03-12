"""
Model router — selects the best available Ollama model for each task type.

Routing strategy:
  - Code-focused tasks (purpose extraction, SQL explanation) → qwen3-coder:480b-cloud
  - Synthesis tasks (clustering, onboarding, drift analysis) → deepseek-v3.1:671b-cloud
  - Fallback: if the preferred model is unavailable, try the other one.
  - If both fail, return a structured "unavailable" response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.llm.ollama_client import OllamaClient, OllamaResponse

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """Semantic analysis task categories for model routing."""

    PURPOSE_EXTRACTION = "purpose_extraction"
    SQL_EXPLANATION = "sql_explanation"
    BUSINESS_LOGIC_SCORING = "business_logic_scoring"
    DOMAIN_CLUSTERING = "domain_clustering"
    DOC_DRIFT_DETECTION = "doc_drift_detection"
    ONBOARDING_SYNTHESIS = "onboarding_synthesis"


# Preferred model for each task type
_ROUTING_TABLE: dict[TaskType, str] = {
    TaskType.PURPOSE_EXTRACTION: "qwen3-coder:480b-cloud",
    TaskType.SQL_EXPLANATION: "qwen3-coder:480b-cloud",
    TaskType.BUSINESS_LOGIC_SCORING: "qwen3-coder:480b-cloud",
    TaskType.DOMAIN_CLUSTERING: "deepseek-v3.1:671b-cloud",
    TaskType.DOC_DRIFT_DETECTION: "deepseek-v3.1:671b-cloud",
    TaskType.ONBOARDING_SYNTHESIS: "deepseek-v3.1:671b-cloud",
}

# Fallback order when the preferred model is unavailable
_FALLBACK_ORDER = [
    "qwen3-coder:480b-cloud",
    "deepseek-v3.1:671b-cloud",
]


@dataclass
class ModelSelection:
    """Result of model routing — which model to use and why."""

    model: str
    task: TaskType
    is_fallback: bool = False
    reason: str = ""


class ModelRouter:
    """Routes semantic analysis tasks to the appropriate Ollama model."""

    def __init__(
        self,
        client: OllamaClient,
        override_model: Optional[str] = None,
    ) -> None:
        self._client = client
        self._override = override_model
        self._available_models: Optional[list[str]] = None

    def _get_available(self) -> list[str]:
        if self._available_models is None:
            self._available_models = self._client.list_models()
            logger.info("Available Ollama models: %s", self._available_models)
        return self._available_models

    def select_model(self, task: TaskType) -> Optional[ModelSelection]:
        """Pick the best available model for a given task.

        Returns None if no model is available.
        """
        if self._override:
            return ModelSelection(
                model=self._override, task=task,
                reason=f"user override: {self._override}",
            )

        available = self._get_available()
        preferred = _ROUTING_TABLE.get(task, _FALLBACK_ORDER[0])

        # Check preferred model
        if any(preferred in m for m in available):
            return ModelSelection(
                model=preferred, task=task,
                reason=f"preferred for {task.value}",
            )

        # Try fallbacks
        for fallback in _FALLBACK_ORDER:
            if fallback != preferred and any(fallback in m for m in available):
                logger.info(
                    "Preferred model %s unavailable for %s; falling back to %s",
                    preferred, task.value, fallback,
                )
                return ModelSelection(
                    model=fallback, task=task, is_fallback=True,
                    reason=f"fallback (preferred {preferred} unavailable)",
                )

        logger.warning("No Ollama model available for task %s", task.value)
        return None

    def generate(
        self,
        task: TaskType,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        format_json: bool = False,
    ) -> tuple[OllamaResponse, Optional[ModelSelection]]:
        """Select model for the task and run generation.

        Returns (response, selection).  If no model is available,
        returns a failed OllamaResponse and None.
        """
        selection = self.select_model(task)
        if selection is None:
            return (
                OllamaResponse(
                    text="", model="none", success=False,
                    error="No Ollama model available",
                ),
                None,
            )

        resp = self._client.generate(
            model=selection.model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            format_json=format_json,
        )
        return resp, selection
