"""Gemini autonomous social agent (Google GenAI function calling).

For each scheduled post, Gemini reviews the clip metadata and the recent posting history,
rewrites the caption/hook for the target platform, decides whether to post now or defer
(topic fatigue), and executes the upload through the platform tool. The worker hosts the
loop; Gemini can only post to the one platform this post targets, and at most once.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from google.genai import errors, types

from . import gemini, social_apis, storage
from .config import settings
from .social_apis import Account, PublishError, PublishResult

log = logging.getLogger(__name__)

MAX_TURNS = 8


def _schema(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


TOOL_DEFS: dict[str, dict[str, Any]] = {
    "get_publishing_history": {
        "description": "Posts published or scheduled in the last 14 days across all platforms (title, hook, "
                       "hashtags, platform, time). Use it to avoid topic fatigue and repetitive hooks.",
        "input_schema": _schema({}),
    },
    "post_instagram_reel": {
        "description": "Publish the clip as an Instagram Reel. Caption max 2200 chars; first line is the hook; "
                       "include 5-10 hashtags at the end.",
        "input_schema": _schema({
            "caption": {"type": "string", "description": "Full Reel caption including hashtags."},
            "share_to_feed": {"type": "boolean", "description": "Also show the Reel in the main feed grid."},
        }),
    },
    "post_tiktok_video": {
        "description": "Publish the clip to TikTok. The title is the caption shown on the video (max 2200 chars, "
                       "keep it short), with 3-5 hashtags.",
        "input_schema": _schema({"title": {"type": "string", "description": "TikTok caption with hashtags."}}),
    },
    "post_youtube_short": {
        "description": "Upload the clip as a YouTube Short. Title max 100 chars and should include #Shorts.",
        "input_schema": _schema({
            "title": {"type": "string"},
            "description": {"type": "string", "description": "Video description, may include hashtags."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Search tags without #."},
        }),
    },
    "post_x_video": {
        "description": "Post the clip to X (Twitter). Text max 280 characters including hashtags (max 2).",
        "input_schema": _schema({"text": {"type": "string"}}),
    },
    "post_linkedin_video": {
        "description": "Post the clip to LinkedIn. Professional framing, 2-4 sentences, max 3 hashtags.",
        "input_schema": _schema({
            "commentary": {"type": "string", "description": "Post text."},
            "title": {"type": "string", "description": "Short video title."},
        }),
    },
    "reschedule_post": {
        "description": "Defer this Auto-Pilot post instead of publishing now, e.g. because a very similar topic "
                       "or hook was posted to this platform in the last 48 hours.",
        "input_schema": _schema({
            "delay_hours": {"type": "integer", "description": "Hours to wait (6-168)."},
            "reason": {"type": "string"},
        }),
    },
}

def _declaration(name: str) -> types.FunctionDeclaration:
    spec = TOOL_DEFS[name]
    schema = {k: v for k, v in spec["input_schema"].items() if k != "additionalProperties"}
    return types.FunctionDeclaration(
        name=name,
        description=spec["description"],
        # Gemini rejects an object schema with no properties, so parameterless tools omit it.
        parameters_json_schema=schema if schema.get("properties") else None,
    )


PLATFORM_TOOL = {
    "INSTAGRAM": "post_instagram_reel",
    "TIKTOK": "post_tiktok_video",
    "YOUTUBE": "post_youtube_short",
    "X": "post_x_video",
    "LINKEDIN": "post_linkedin_video",
}

SYSTEM_PROMPT = """You are the social media manager for a creator. You publish short vertical \
video clips to one platform at a time through the tools you are given.

For each post:
1. Call get_publishing_history first.
2. Write a platform-native caption from the clip's metadata: lead with a scroll-stopping first \
line, keep the creator's voice, follow the platform's length and hashtag conventions, and never \
invent facts, numbers, or claims that are not in the transcript. If the creator supplied a \
caption, keep its intent and only tighten it for the platform.
3. Publish exactly once with the platform tool. If the tool returns an error that a different \
caption could fix (for example, too long), you may fix it and call the tool again; otherwise stop.

Topic fatigue: if reschedule_post is available (Auto-Pilot posts only) and this platform got a \
post with a clearly overlapping topic or near-identical hook in the last 48 hours, reschedule \
instead of publishing. Otherwise publish now.

When you are done, reply with one short sentence summarising what you did."""


@dataclass
class AgentOutcome:
    status: str  # PUBLISHED | FAILED | RESCHEDULED
    external_id: str | None = None
    url: str | None = None
    caption: str | None = None
    error: str | None = None
    reschedule_to: datetime | None = None
    log: list[dict[str, Any]] = field(default_factory=list)


class PublisherAgent:
    def __init__(self, ctx: dict[str, Any], workdir: Path) -> None:
        self.ctx = ctx
        self.post = ctx["post"]
        self.clip = ctx["clip"]
        self.platform: str = self.post["platform"]
        self.account = Account.from_api(ctx["account"])
        self.workdir = workdir
        self.outcome = AgentOutcome(status="FAILED")
        self._published: PublishResult | None = None
        self._video: Path | None = None
        names = ["get_publishing_history", PLATFORM_TOOL[self.platform]]
        if self.post.get("source") == "AUTOPILOT" and int(self.post.get("attempts", 1)) <= 3:
            names.append("reschedule_post")
        self.tools = [_declaration(n) for n in names]

    # ------------------------------------------------------------------ tool implementations

    def _video_file(self) -> Path:
        if self._video is None:
            if not self.clip.get("videoKey"):
                raise PublishError("Clip has no rendered video")
            self._video = storage.download(self.clip["videoKey"], self.workdir / "publish.mp4")
        return self._video

    def _publish(self, fn_name: str, args: dict[str, Any]) -> PublishResult:
        if self._published is not None:
            raise PublishError("Already published this post; do not publish again.")
        acct = self.account
        if fn_name == "post_instagram_reel":
            if not self.ctx.get("videoUrl"):
                raise PublishError("No public video URL available for Instagram")
            caption = args["caption"]
            res = social_apis.post_instagram_reel(acct, self.ctx["videoUrl"], caption, bool(args["share_to_feed"]))
        elif fn_name == "post_tiktok_video":
            caption = args["title"]
            res = social_apis.post_tiktok_video(acct, self._video_file(), caption)
        elif fn_name == "post_youtube_short":
            caption = f"{args['title']}\n\n{args['description']}"
            res = social_apis.post_youtube_short(acct, self._video_file(), args["title"], args["description"],
                                                 list(args["tags"]))
        elif fn_name == "post_x_video":
            if len(args["text"]) > 280:
                raise PublishError(f"Text is {len(args['text'])} characters; X allows 280.")
            caption = args["text"]
            res = social_apis.post_x_video(acct, self._video_file(), caption)
        elif fn_name == "post_linkedin_video":
            caption = args["commentary"]
            res = social_apis.post_linkedin_video(acct, self._video_file(), caption, args["title"])
        else:
            raise PublishError(f"Unknown tool {fn_name}")
        self._published = res
        self.outcome = AgentOutcome("PUBLISHED", res.external_id, res.url, caption, log=self.outcome.log)
        return res

    def _execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Returns (result_text, is_error)."""
        try:
            if name == "get_publishing_history":
                return json.dumps(self.ctx.get("history", []), default=str), False
            if name == "reschedule_post":
                if self._published is not None:
                    return "Already published; cannot reschedule.", True
                hours = max(6, min(168, int(args["delay_hours"])))
                when = datetime.now(UTC) + timedelta(hours=hours)
                self.outcome = AgentOutcome("RESCHEDULED", reschedule_to=when, error=args["reason"][:500],
                                            log=self.outcome.log)
                return f"Rescheduled to {when.isoformat()}.", False
            if name in PLATFORM_TOOL.values():
                if name != PLATFORM_TOOL[self.platform]:
                    return f"This post targets {self.platform}; use {PLATFORM_TOOL[self.platform]}.", True
                res = self._publish(name, args)
                return json.dumps({"ok": True, "id": res.external_id, "url": res.url, "detail": res.detail}), False
            return f"Unknown tool: {name}", True
        except PublishError as exc:
            self.outcome.error = str(exc)
            return f"Error: {exc}", True
        except (KeyError, TypeError, ValueError) as exc:
            return f"Invalid tool input: {exc}", True

    # ------------------------------------------------------------------ loop

    def _brief(self) -> str:
        c = self.clip
        return json.dumps({
            "platform": self.platform,
            "account": self.ctx["account"].get("accountName"),
            "post_source": self.post.get("source"),
            "creator_caption": self.post.get("caption"),
            "clip": {
                "title": c["title"], "on_screen_hook": c["hook"], "virality_score": c["viralityScore"],
                "duration_seconds": round(c["durationMs"] / 1000), "hook_breakdown": c.get("hookBreakdown"),
                "suggested_captions": c.get("captions"), "hashtags": c.get("hashtags"),
                "transcript": c.get("transcript", "")[:6000],
            },
            "now_utc": datetime.now(UTC).isoformat(timespec="minutes"),
        }, ensure_ascii=False, indent=2)

    def _config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=self.tools)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.AUTO)
            ),
            # We run the loop ourselves so every call is gated, logged, and limited to one upload.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            safety_settings=gemini.safety_settings(),
            thinking_config=gemini.thinking_config(),
            max_output_tokens=16_000,
        )

    def run(self) -> AgentOutcome:
        prompt = f"Publish this clip to {self.platform}.\n\n<post>\n{self._brief()}\n</post>"
        contents: list[types.Content] = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        config = self._config()
        steps = self.outcome.log
        for _ in range(MAX_TURNS):
            response = gemini.client().models.generate_content(
                model=settings.gemini_model, contents=contents, config=config
            )
            log.info("publisher turn id=%s", response.response_id)
            if reason := gemini.blocked_reason(response):
                self.outcome.error = f"Gemini did not publish this clip: {reason}."
                break
            candidate = response.candidates[0]
            if candidate.finish_reason == types.FinishReason.MAX_TOKENS:
                self.outcome.error = "Agent response was truncated (max output tokens)."
                break
            if candidate.content:
                # Append the model turn unchanged: it carries the thought signatures Gemini needs next turn.
                contents.append(candidate.content)
            parts = (candidate.content.parts if candidate.content else None) or []
            text = " ".join(p.text for p in parts if p.text and not p.thought).strip()
            if text:
                steps.append({"type": "message", "text": text[:2000]})
            calls = response.function_calls or []
            if not calls:
                if self.outcome.status == "FAILED" and not self.outcome.error:
                    self.outcome.error = text or "Agent finished without publishing."
                break

            replies = []
            for call in calls:
                name = call.name or ""
                args = dict(call.args or {})
                result, is_error = self._execute(name, args)
                steps.append({"type": "tool", "name": name, "input": args, "result": result[:2000],
                              "is_error": is_error})
                replies.append(types.Part(function_response=types.FunctionResponse(
                    id=call.id, name=name, response={"error": result} if is_error else {"result": result},
                )))
            # All results for one turn go back together, in a single tool turn.
            contents.append(types.Content(role="tool", parts=replies))
        else:
            if self.outcome.status == "FAILED" and not self.outcome.error:
                self.outcome.error = f"Agent did not finish within {MAX_TURNS} turns."
        self.outcome.log = steps
        return self.outcome


def publish(ctx: dict[str, Any], workdir: Path) -> AgentOutcome:
    if not ctx.get("account") or not ctx["account"].get("accessToken"):
        return AgentOutcome("FAILED", error=f"No connected {ctx['post']['platform']} account with an access token.")
    if not ctx["clip"].get("videoKey"):
        return AgentOutcome("FAILED", error="Clip has not been rendered yet.")
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        return PublisherAgent(ctx, workdir).run()
    except errors.APIError as exc:
        return AgentOutcome("FAILED", error=f"Gemini API error {exc.code}: {exc.message}")
    except OSError as exc:
        return AgentOutcome("FAILED", error=f"Could not reach the Gemini API: {exc}")
