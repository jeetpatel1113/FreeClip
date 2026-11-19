"""Shared Google GenAI (Gemini) setup for the curator and the publishing agent."""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from google import genai
from google.genai import errors, types

from .config import settings

log = logging.getLogger(__name__)

# Overloaded (503), rate limited (429) or transient server errors are retried; anything else is final.
RETRYABLE_CODES = {429, 500, 502, 503, 504}

# Transcripts of podcasts and streams routinely contain profanity and heated language. Relax the
# configurable safety filters so curation and captioning are not blocked by ordinary speech.
# (Google's non-configurable filters, e.g. PROHIBITED_CONTENT, still apply.)
_TEXT_CATEGORIES = (
    types.HarmCategory.HARM_CATEGORY_HARASSMENT,
    types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
    types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
    types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
)


def safety_settings() -> list[types.SafetySetting]:
    threshold = types.HarmBlockThreshold(settings.gemini_safety_threshold)
    return [types.SafetySetting(category=c, threshold=threshold) for c in _TEXT_CATEGORIES]


def thinking_config() -> types.ThinkingConfig | None:
    # -1 = dynamic thinking (the model decides); 0 disables it on Flash; >0 is a token budget.
    if settings.gemini_thinking_budget is None:
        return None
    return types.ThinkingConfig(thinking_budget=settings.gemini_thinking_budget)


@functools.lru_cache(maxsize=1)
def client() -> genai.Client:
    """Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment."""
    # Without a timeout a stalled connection would block the worker thread indefinitely.
    return genai.Client(http_options=types.HttpOptions(timeout=settings.gemini_timeout_seconds * 1000))


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__


def blocked_reason(response: types.GenerateContentResponse) -> str | None:
    """Human-readable reason when Gemini returned no usable candidate."""
    feedback = response.prompt_feedback
    if feedback and feedback.block_reason:
        return f"prompt blocked ({feedback.block_reason.value})"
    if not response.candidates:
        return "no candidates returned"
    reason = response.candidates[0].finish_reason
    if reason in (
        types.FinishReason.SAFETY,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.SPII,
        types.FinishReason.RECITATION,
    ):
        return f"response blocked ({reason.value})"
    return None


def candidate_models(primary: str) -> list[str]:
    """The configured model first, then GEMINI_FALLBACK_MODELS (deduplicated, order kept)."""
    return list(dict.fromkeys([primary, *settings.gemini_fallback_models]))


def generate_with_model(*, models: list[str], contents: Any, config: types.GenerateContentConfig,
                        attempts_per_model: int = 3, base_delay: float = 5.0
                        ) -> tuple[types.GenerateContentResponse, str]:
    """generate_content with backoff on 429/5xx, falling through to the next model when one stays
    overloaded (e.g. "model is experiencing high demand") or is unavailable to this key (404).
    Returns the response and the model that produced it."""
    last: errors.APIError | None = None
    for i, model in enumerate(models):
        for attempt in range(1, attempts_per_model + 1):
            try:
                return client().models.generate_content(model=model, contents=contents, config=config), model
            except Exception as exc:  # noqa: BLE001 - narrowed below
                if _is_timeout(exc):
                    exc = errors.APIError(504, {"error": {"code": 504, "message": f"request timed out: {exc}",
                                                          "status": "DEADLINE_EXCEEDED"}})
                elif not isinstance(exc, errors.APIError):
                    raise
                last = exc
                if exc.code == 404:
                    log.warning("Gemini model %s unavailable (404); trying next model", model)
                    break
                if exc.code == 504 and i + 1 < len(models):
                    # A deadline already cost minutes; another try on the same overloaded model rarely helps.
                    log.warning("Gemini %s timed out (504); falling back to %s", model, models[i + 1])
                    break
                if exc.code not in RETRYABLE_CODES:
                    raise
                if attempt == attempts_per_model:
                    if i + 1 < len(models):
                        log.warning("Gemini %s still returning %s; falling back to %s", model, exc.code, models[i + 1])
                    break
                delay = min(60.0, base_delay * 2 ** (attempt - 1))
                log.warning("Gemini %s %s (%s); retry %d/%d in %.0fs", model, exc.code, (exc.message or "")[:100],
                            attempt, attempts_per_model - 1, delay)
                time.sleep(delay)
    assert last is not None
    raise last


def generate(*, model: str, contents: Any, config: types.GenerateContentConfig, **kw: Any
             ) -> types.GenerateContentResponse:
    return generate_with_model(models=candidate_models(model), contents=contents, config=config, **kw)[0]
