"""Shared Google GenAI (Gemini) setup for the curator and the publishing agent."""

from __future__ import annotations

import functools

from google import genai
from google.genai import types

from .config import settings

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
    return genai.Client()


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
