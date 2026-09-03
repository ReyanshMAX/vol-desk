"""Groq inference behind a model-agnostic OpenAI-compatible client (D-009).

Two call sites only: regime labeling and structure construction (D-004).
No streaming, no tool use, no conversation history (docs/PROMPTS.md).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from openai import APIError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from src import config as config_module

logger = logging.getLogger("vol_desk.llm")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

T = TypeVar("T", bound=BaseModel)


class InferenceUnavailable(RuntimeError):
    """Raised when GROQ_API_KEY is unset, a tier's model id is unresolved
    (Q-004), or the provider is unreachable after retries. Callers degrade
    per D-010 -- hold and manage, no new entries -- never fabricate a
    response."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class JSONCompletionResult(Generic[T]):
    """Superset of complete_json's plain T | None return, carrying what the
    call sites need to write a full decision_log row per docs/PROMPTS.md
    Logging section (inputs are the caller's; this carries the output
    side)."""
    parsed: T | None
    raw_text: str | None
    model: str | None
    latency_ms: int | None
    accepted: bool


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise InferenceUnavailable("GROQ_API_KEY is unset")
        _client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
    return _client


def _resolve_model(tier: Literal["fast", "reasoning"]) -> str:
    cfg = config_module.load()
    model = cfg.llm_tiers.fast if tier == "fast" else cfg.llm_tiers.reasoning
    if not model:
        raise InferenceUnavailable(
            f"llm.tiers.{tier} is unresolved in config/params.yaml (Q-004)"
        )
    return model


def complete(
    system: str,
    user: str,
    *,
    tier: Literal["fast", "reasoning"],
    max_tokens: int = 800,
    temperature: float = 0.2,
    timeout_s: int = 30,
) -> LLMResponse:
    """Single-shot completion. Raises InferenceUnavailable on any failure
    that should trigger the D-010 degradation path -- never returns a
    fabricated response."""
    client = _get_client()
    model = _resolve_model(tier)
    started = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout_s,
        )
    except (APIError, APITimeoutError, Exception) as e:  # noqa: BLE001 - single boundary
        raise InferenceUnavailable(f"Groq call failed: {e}") from e

    latency_ms = int((time.monotonic() - started) * 1000)
    choice = resp.choices[0]
    usage = resp.usage
    return LLMResponse(
        text=choice.message.content or "",
        model=resp.model,
        latency_ms=latency_ms,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def complete_json(system: str, user: str, schema: type[T], *,
                   tier: Literal["fast", "reasoning"],
                   max_tokens: int = 800, temperature: float = 0.2,
                   timeout_s: int = 30, max_retries: int = 1) -> T | None:
    """1. call complete(); 2. strip markdown fences; 3. json.loads + validate;
    4. on failure, retry once with the validation error appended; 5. on
    second failure, return None. None is not an error for the caller -- it
    means no trade / mechanical fallback this scan (never fabricate a
    default response, CLAUDE.md rule 3)."""
    result = complete_json_meta(system, user, schema, tier=tier, max_tokens=max_tokens,
                                 temperature=temperature, timeout_s=timeout_s,
                                 max_retries=max_retries)
    return result.parsed


def complete_json_meta(system: str, user: str, schema: type[T], *,
                        tier: Literal["fast", "reasoning"],
                        max_tokens: int = 800, temperature: float = 0.2,
                        timeout_s: int = 30, max_retries: int = 1) -> JSONCompletionResult[T]:
    attempt_user = user
    last_response: LLMResponse | None = None
    for attempt in range(max_retries + 1):
        response = complete(system, attempt_user, tier=tier, max_tokens=max_tokens,
                             temperature=temperature, timeout_s=timeout_s)
        last_response = response
        try:
            raw = _strip_fences(response.text)
            data = json.loads(raw)
            parsed = schema.model_validate(data)
            return JSONCompletionResult(parsed=parsed, raw_text=response.text,
                                         model=response.model, latency_ms=response.latency_ms,
                                         accepted=True)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.info("complete_json validation failed (attempt %d): %s", attempt, e)
            attempt_user = f"{user}\n\nYour previous response failed validation: {e}\nReturn only a corrected JSON object."
            continue

    return JSONCompletionResult(
        parsed=None,
        raw_text=last_response.text if last_response else None,
        model=last_response.model if last_response else None,
        latency_ms=last_response.latency_ms if last_response else None,
        accepted=False,
    )
