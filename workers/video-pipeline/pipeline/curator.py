"""AI viral curation with Gemini (Google GenAI SDK, structured outputs).

Gemini reads the timestamped transcript and returns 5-10 standalone 30-60 s clips with a
virality score, an on-screen hook, a hook breakdown, and platform-native captions/hashtags.
Clip boundaries are then snapped to exact word timestamps and validated locally.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from . import gemini
from .config import settings

log = logging.getLogger(__name__)

MIN_CLIP_MS = 30_000
MAX_CLIP_MS = 60_000
_SENTENCE_END = re.compile(r"[.!?…]['\")\]]*$")


# --------------------------------------------------------------------------- output schema


class HookScores(BaseModel):
    hook: int
    flow: int
    value: int
    trend: int


class HookBreakdown(BaseModel):
    hook_type: str
    first_3_seconds: str
    emotional_trigger: str
    payoff: str
    retention_notes: str
    scores: HookScores


class PlatformCaptions(BaseModel):
    tiktok: str
    instagram: str
    youtube: str
    x: str
    linkedin: str


class CuratedClip(BaseModel):
    title: str
    start_seconds: float
    end_seconds: float
    virality_score: int
    hook: str
    hook_breakdown: HookBreakdown
    reasoning: str
    captions: PlatformCaptions
    hashtags: list[str]
    broll_keywords: list[str]


class CurationResult(BaseModel):
    clips: list[CuratedClip]


SYSTEM_PROMPT = """You are a senior short-form video editor who has cut thousands of TikToks, Reels \
and YouTube Shorts that went viral. You find the moments inside long-form videos (podcasts, \
streams, interviews, talks) that will hold a cold viewer's attention on a vertical feed.

How you choose clips:
- Each clip must stand alone. A viewer who has never seen the source must understand it. Never \
start on a dangling reference ("and that's why he...", "like I said") or end mid-thought.
- Start on the strongest possible opening line: a bold claim, a question, a surprising number, \
conflict, or an emotional moment. The first 3 seconds decide whether people keep watching.
- End on a payoff: the punchline, the lesson, the reveal, or a natural sentence end.
- Target 30-60 seconds. Prefer 35-55 seconds.
- Clips must not overlap each other.
- Spread clips across the whole video when quality allows; do not cluster them all in one section.

Virality score (0-100) is your honest estimate of how this clip performs against the creator's \
other clips: 90+ is a rare standout, 70-89 strong, 50-69 decent, under 50 weak. Score the hook, \
flow, value and trend sub-scores 0-100 each.

The "hook" field is on-screen text shown at the top of the video for the first seconds: at most \
8 words, punchy, curiosity-driven, no hashtags, no emojis unless they add meaning. Write it in \
the language of the transcript.

Captions, per platform, in the transcript's language:
- tiktok: casual, 1-2 short lines, end with 3-5 relevant hashtags.
- instagram: a strong first line, 2-4 short lines with line breaks, a call to action, then 5-10 hashtags.
- youtube: a Shorts title under 90 characters that includes #Shorts.
- x: under 240 characters total, conversational, at most 2 hashtags.
- linkedin: a professional 2-4 sentence insight framing, at most 3 hashtags.
"hashtags" is the 5-12 best general hashtags for the clip, each starting with #.
"broll_keywords" is 0-3 short concrete stock-footage search terms (e.g. "stock market chart") for \
visual moments that would benefit from B-roll; empty if none fit.

Use only timestamps that appear in the transcript. Be accurate: start_seconds should be the \
timestamp of the first word you want, end_seconds the end of the last word you want."""


def _fmt_ts(ms: int) -> str:
    s = ms / 1000
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def build_transcript(transcript: dict[str, Any], max_line_ms: int = 12_000) -> str:
    """Timestamped lines: '[t_start_seconds-t_end_seconds] text'. Long segments are split for precision."""
    words = transcript["words"]
    lines: list[str] = []
    for seg in transcript["segments"]:
        chunk: list[dict[str, Any]] = []
        for w in words[seg["w0"] : seg["w1"]]:
            chunk.append(w)
            ends_sentence = bool(_SENTENCE_END.search(w["word"]))
            if chunk and (w["end"] - chunk[0]["start"] >= max_line_ms or (ends_sentence and len(chunk) > 12)):
                lines.append(_line(chunk))
                chunk = []
        if chunk:
            lines.append(_line(chunk))
    return "\n".join(lines)


def _line(chunk: list[dict[str, Any]]) -> str:
    text = " ".join(w["word"] for w in chunk)
    return f"[{chunk[0]['start'] / 1000:.2f}-{chunk[-1]['end'] / 1000:.2f}] {text}"


# --------------------------------------------------------------------------- Gemini call


def _call_gemini(transcript_text: str, duration_ms: int, title: str | None, n_min: int, n_max: int) -> CurationResult:
    user = (
        f"Source title: {title or 'unknown'}\n"
        f"Source duration: {_fmt_ts(duration_ms)} ({duration_ms / 1000:.0f} s)\n"
        f"Return between {n_min} and {n_max} clips (fewer only if the video genuinely lacks good moments), "
        "best first.\n\n"
        "Each transcript line is [start_seconds-end_seconds] text.\n\n"
        f"<transcript>\n{transcript_text}\n</transcript>"
    )
    try:
        response = gemini.generate(
            model=settings.gemini_model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=CurationResult,
                safety_settings=gemini.safety_settings(),
                thinking_config=gemini.thinking_config(),
                max_output_tokens=65_536,
                temperature=0.7,
            ),
        )
    except errors.APIError as exc:
        raise RuntimeError(f"Gemini API error {exc.code}: {exc.message}") from exc

    log.info("curation response id=%s usage=%s", response.response_id, response.usage_metadata)
    if reason := gemini.blocked_reason(response):
        raise RuntimeError(f"Gemini did not curate this video: {reason}.")
    if response.candidates and response.candidates[0].finish_reason == types.FinishReason.MAX_TOKENS:
        raise RuntimeError("Curation response was cut off (max output tokens); try a shorter source video.")
    if isinstance(response.parsed, CurationResult):
        return response.parsed
    if not response.text:
        raise RuntimeError("Gemini returned no curation output")
    try:
        return CurationResult.model_validate(json.loads(response.text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"Curation output failed validation: {exc}") from exc


# --------------------------------------------------------------------------- boundary snapping


def _snap(words: list[dict[str, Any]], start_ms: float, end_ms: float, total_ms: int) -> tuple[int, int] | None:
    """Return (i0, i1) inclusive word indices for a 30-60 s window ending on a sentence where possible."""
    n = len(words)
    i0 = next((i for i, w in enumerate(words) if w["start"] >= start_ms - 300), None)
    if i0 is None:
        return None
    i1 = max((i for i in range(i0, n) if words[i]["start"] < end_ms + 300), default=i0)

    def dur(a: int, b: int) -> int:
        return words[b]["end"] - words[a]["start"]

    if total_ms >= MIN_CLIP_MS:
        # Too short: extend forward, then (if still short) backward.
        while dur(i0, i1) < MIN_CLIP_MS and i1 + 1 < n and dur(i0, i1 + 1) <= MAX_CLIP_MS:
            i1 += 1
        # Finish the sentence if there's room.
        j = i1
        while j + 1 < n and not _SENTENCE_END.search(words[j]["word"]) and dur(i0, j + 1) <= MAX_CLIP_MS:
            j += 1
        if _SENTENCE_END.search(words[j]["word"]):
            i1 = j
        while dur(i0, i1) < MIN_CLIP_MS and i0 > 0 and dur(i0 - 1, i1) <= MAX_CLIP_MS:
            i0 -= 1
    # Too long: trim back to the last sentence end inside the limit, else hard-cut.
    if dur(i0, i1) > MAX_CLIP_MS:
        limit = max((i for i in range(i0, i1 + 1) if dur(i0, i) <= MAX_CLIP_MS), default=i0)
        sentence = [i for i in range(i0, limit + 1)
                    if _SENTENCE_END.search(words[i]["word"]) and dur(i0, i) >= MIN_CLIP_MS * 0.8]
        i1 = sentence[-1] if sentence else limit
    return i0, i1


def _normalize_tag(tag: str) -> str | None:
    tag = re.sub(r"[^\w]", "", tag.strip().lstrip("#"), flags=re.UNICODE)
    return f"#{tag}" if tag else None


def curate(transcript: dict[str, Any], duration_ms: int, title: str | None, n_min: int = 5, n_max: int = 10
           ) -> list[dict[str, Any]]:
    words = transcript["words"]
    result = _call_gemini(build_transcript(transcript), duration_ms, title, n_min, n_max)

    candidates = []
    for c in result.clips:
        span = _snap(words, c.start_seconds * 1000, c.end_seconds * 1000, duration_ms)
        if span is None:
            log.warning("dropping clip %r: timestamps outside transcript", c.title)
            continue
        i0, i1 = span
        start_ms = max(0, words[i0]["start"] - 120)
        end_ms = min(duration_ms or words[i1]["end"] + 400, words[i1]["end"] + 350)
        candidates.append((c, start_ms, end_ms, i0, i1))

    # Highest score wins when clips overlap by more than 30% of the shorter one.
    candidates.sort(key=lambda t: t[0].virality_score, reverse=True)
    kept: list[tuple[CuratedClip, int, int, int, int]] = []
    for cand in candidates:
        _, s, e, _, _ = cand
        if all(min(e, ke) - max(s, ks) <= 0.3 * min(e - s, ke - ks) for _, ks, ke, _, _ in kept):
            kept.append(cand)
    kept = kept[:n_max]

    clips = []
    for index, (c, start_ms, end_ms, i0, i1) in enumerate(kept):
        rel_words = [
            {"word": w["word"], "start": max(0, w["start"] - start_ms), "end": max(0, w["end"] - start_ms)}
            for w in words[i0 : i1 + 1]
        ]
        tags = [t for t in (_normalize_tag(h) for h in c.hashtags) if t]
        bd = c.hook_breakdown.model_dump()
        bd["scores"] = {k: max(0, min(100, int(v))) for k, v in bd["scores"].items()}
        clips.append({
            "index": index,
            "title": c.title.strip()[:200] or f"Clip {index + 1}",
            "startMs": int(start_ms),
            "endMs": int(end_ms),
            "viralityScore": max(0, min(100, int(c.virality_score))),
            "hook": c.hook.strip()[:200],
            "hookBreakdown": bd,
            "reasoning": c.reasoning.strip(),
            "words": rel_words,
            "captions": c.captions.model_dump(),
            "hashtags": list(dict.fromkeys(tags))[:15],
            "brollKeywords": [k.strip() for k in c.broll_keywords if k.strip()][:3],
        })
    if not clips:
        raise RuntimeError("Gemini did not return any usable clips for this video")
    return clips
