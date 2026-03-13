"""
Ollama client — local LLM inference via the Ollama REST API.

Design:
  - Uses the /api/generate endpoint for text completion.
  - Supports timeout, retries, and structured JSON output parsing.
  - Fails gracefully if Ollama is not running or the model is unavailable.
  - Never calls cloud APIs — all inference is local.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_TIMEOUT = 120  # seconds per request
_DEFAULT_RETRIES = 2


@dataclass
class OllamaResponse:
    """Parsed result from a single Ollama generation call."""

    text: str
    """Raw text response from the model."""

    model: str
    """Model name that produced this response."""

    prompt_tokens: int = 0
    eval_tokens: int = 0
    elapsed_seconds: float = 0.0
    success: bool = True
    error: Optional[str] = None

    def parse_json(self) -> Optional[Any]:
        """Attempt to extract JSON data from the response text.

        Handles responses where JSON is wrapped in markdown code fences.
        Returns None if parsing fails.
        """
        text = self.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try object boundaries
        obj_start = text.find("{")
        obj_end = text.rfind("}")
        if obj_start != -1 and obj_end != -1 and obj_end > obj_start:
            try:
                return json.loads(text[obj_start : obj_end + 1])
            except json.JSONDecodeError:
                pass

        # Try array boundaries
        arr_start = text.find("[")
        arr_end = text.rfind("]")
        if arr_start != -1 and arr_end != -1 and arr_end > arr_start:
            try:
                return json.loads(text[arr_start : arr_end + 1])
            except json.JSONDecodeError:
                pass
        return None


@dataclass
class ContextWindowBudget:
    """Track cumulative token spend across all LLM calls in a run.

    The challenge doc requires estimating token count before calling any LLM
    and tracking cumulative spend.
    """

    max_prompt_tokens: int = 32_000
    """Per-call prompt token budget (conservative estimate)."""

    total_prompt_tokens: int = 0
    total_eval_tokens: int = 0
    total_calls: int = 0
    total_elapsed: float = 0.0
    failures: int = 0

    def record(self, resp: OllamaResponse) -> None:
        self.total_prompt_tokens += resp.prompt_tokens
        self.total_eval_tokens += resp.eval_tokens
        self.total_calls += 1
        self.total_elapsed += resp.elapsed_seconds
        if not resp.success:
            self.failures += 1

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token for English/code."""
        return max(1, len(text) // 4)

    def can_fit(self, text: str) -> bool:
        return self.estimate_tokens(text) <= self.max_prompt_tokens

    def summary(self) -> dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_eval_tokens": self.total_eval_tokens,
            "total_elapsed_seconds": round(self.total_elapsed, 2),
            "failures": self.failures,
        }


class OllamaClient:
    """Synchronous client for the local Ollama REST API."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT,
        retries: int = _DEFAULT_RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self._available: Optional[bool] = None

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if Ollama is reachable."""
        if self._available is not None:
            return self._available
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        if not self._available:
            logger.warning("Ollama is not reachable at %s", self.base_url)
        return self._available

    def list_models(self) -> list[str]:
        """Return names of locally available models."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    def has_model(self, model: str) -> bool:
        return any(model in m for m in self.list_models())

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        format_json: bool = False,
    ) -> OllamaResponse:
        """Send a generation request to Ollama.

        Args:
            model:       Model name (e.g., 'qwen3-coder:480b-cloud').
            prompt:      The user prompt.
            system:      Optional system prompt.
            temperature: Sampling temperature (low = deterministic).
            max_tokens:  Maximum tokens to generate.
            format_json: If True, request JSON output format.

        Returns an OllamaResponse (always — never raises).
        """
        if not self.is_available():
            return OllamaResponse(
                text="", model=model, success=False,
                error="Ollama not available",
            )

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system
        if format_json:
            payload["format"] = "json"

        last_error = ""
        for attempt in range(1, self.retries + 1):
            t0 = time.monotonic()
            try:
                r = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=self.timeout,
                )
                elapsed = time.monotonic() - t0

                if r.status_code != 200:
                    last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                    logger.warning(
                        "Ollama attempt %d/%d failed: %s",
                        attempt, self.retries, last_error,
                    )
                    continue

                data = r.json()
                return OllamaResponse(
                    text=data.get("response", ""),
                    model=model,
                    prompt_tokens=data.get("prompt_eval_count", 0),
                    eval_tokens=data.get("eval_count", 0),
                    elapsed_seconds=round(elapsed, 2),
                    success=True,
                )

            except requests.Timeout:
                elapsed = time.monotonic() - t0
                last_error = f"Timeout after {elapsed:.0f}s"
                logger.warning("Ollama attempt %d/%d: %s", attempt, self.retries, last_error)
            except requests.ConnectionError:
                elapsed = time.monotonic() - t0
                last_error = "Connection refused"
                logger.warning("Ollama attempt %d/%d: %s", attempt, self.retries, last_error)
                self._available = False
                break  # Don't retry connection errors
            except Exception as exc:
                elapsed = time.monotonic() - t0
                last_error = str(exc)
                logger.warning("Ollama attempt %d/%d: %s", attempt, self.retries, last_error)

        return OllamaResponse(
            text="", model=model, success=False,
            error=last_error, elapsed_seconds=0.0,
        )
