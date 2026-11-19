"""Offline smoke tests: no network, no Gemini calls. Run: python -m pytest tests (or python tests/test_smoke.py)."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("INTERNAL_API_TOKEN", "test-token-123456")
os.environ["PUBLISH_DRY_RUN"] = "1"

from google.genai import types  # noqa: E402

from pipeline import captions, curator, gemini, ingest, publisher, reframer  # noqa: E402


def _words(n: int, step: int = 400) -> list[dict]:
    out = []
    for i in range(n):
        w = f"word{i}" + ("." if i % 12 == 11 else "")
        out.append({"word": w, "start": i * step, "end": i * step + step - 50})
    return out


def test_response_schema_round_trip():
    # Every field is required (no defaults), which keeps Gemini's response_schema strict.
    schema = curator.CurationResult.model_json_schema()
    for name, model in schema["$defs"].items():
        assert set(model["required"]) == set(model["properties"]), name
    sample = {"clips": [{
        "title": "t", "start_seconds": 1.0, "end_seconds": 40.0, "virality_score": 80, "hook": "h",
        "hook_breakdown": {"hook_type": "q", "first_3_seconds": "f", "emotional_trigger": "e", "payoff": "p",
                           "retention_notes": "r", "scores": {"hook": 1, "flow": 2, "value": 3, "trend": 4}},
        "reasoning": "r", "captions": {"tiktok": "a", "instagram": "b", "youtube": "c", "x": "d", "linkedin": "e"},
        "hashtags": ["#a"], "broll_keywords": [],
    }]}
    assert curator.CurationResult.model_validate(sample).clips[0].virality_score == 80
    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=curator.CurationResult,
                                      safety_settings=gemini.safety_settings())
    assert all(s.threshold == types.HarmBlockThreshold.BLOCK_NONE for s in cfg.safety_settings)


def _resp(*parts: types.Part) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)), finish_reason=types.FinishReason.STOP)])


def test_publisher_function_calling_loop(tmp_path=None):
    scripted = [
        _resp(types.Part(function_call=types.FunctionCall(id="1", name="get_publishing_history", args={}))),
        _resp(types.Part(function_call=types.FunctionCall(id="2", name="post_x_video", args={"text": "hi #ai"}))),
        _resp(types.Part.from_text(text="Posted to X.")),
    ]
    seen: list[list[types.Content]] = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen.append(list(contents))
            names = {d.name for d in config.tools[0].function_declarations}
            assert names == {"get_publishing_history", "post_x_video"}
            return scripted[len(seen) - 1]

    class FakeClient:
        models = FakeModels()

    video = Path(tempfile.mkdtemp()) / "v.mp4"
    video.write_bytes(b"0")
    orig_client, orig_dl = gemini.client, publisher.storage.download
    gemini.client = lambda: FakeClient()
    publisher.storage.download = lambda key, path: video
    try:
        ctx = {
            "post": {"platform": "X", "source": "MANUAL", "caption": None, "attempts": 1},
            "clip": {"title": "t", "hook": "h", "viralityScore": 90, "durationMs": 40000, "videoKey": "k"},
            "account": {"id": "a", "platform": "X", "accessToken": "tok"},
            "history": [],
        }
        outcome = publisher.publish(ctx, video.parent / "work")
    finally:
        gemini.client, publisher.storage.download = orig_client, orig_dl
    assert outcome.status == "PUBLISHED", outcome
    assert outcome.external_id.startswith("dryrun-x-")
    # Third request carries: user, model call, tool result, model call, tool result.
    roles = [c.role for c in seen[2]]
    assert roles == ["user", "model", "tool", "model", "tool"], roles
    assert seen[2][-1].parts[0].function_response.id == "2"


def test_caption_suffix_is_enforced():
    f = publisher.with_suffix
    assert f("Great clip", None, 100) == "Great clip"
    assert f("Great clip #fyp", "#fyp @brand", 100) == "Great clip #fyp\n\n@brand"  # no duplicate tags
    assert f("Great clip #FYP @Brand", "#fyp @brand", 100) == "Great clip #FYP @Brand"
    # Over the limit: the agent's text is shortened, the required suffix never is.
    out = f("x" * 300, "#ad @brand", 280)
    assert len(out) <= 280 and out.endswith("\n\n#ad @brand"), out
    # Disclosures with plain words must appear verbatim.
    assert f("Nice #ad", "#ad Paid partnership with @brand", 200).endswith("#ad Paid partnership with @brand")
    try:
        f("hi", "#" + "a" * 300, 280)
    except publisher.PublishError:
        pass
    else:
        raise AssertionError("an over-long suffix must fail instead of being cut")


def test_ytdlp_duration_filter():
    rejected: list[str] = []
    f = ingest.duration_filter(3600, rejected)
    assert f({"duration": 120}, incomplete=False) is None
    assert f({"duration": 120.5}) is None
    assert f({"duration": None}) is None          # unknown duration: allowed, checked by ffprobe later
    assert f({"duration": "n/a"}) is None         # non-numeric must not raise TypeError
    assert f({}) is None
    assert f({"duration": 7200}) is not None
    assert f({"duration": 60, "is_live": True}) is not None
    assert len(rejected) == 2


def test_gemini_retries_transient_errors():
    from google.genai import errors
    calls = []

    class Flaky:
        def generate_content(self, model, contents, config):
            calls.append(1)
            if len(calls) < 3:
                body = {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}
                raise errors.ServerError(503, body)
            return _resp(types.Part.from_text(text="ok"))

    class C:
        models = Flaky()

    orig = gemini.client
    gemini.client = lambda: C()
    try:
        r = gemini.generate(model="m", contents="x", config=types.GenerateContentConfig(), base_delay=0,
                            attempts_per_model=3)
    finally:
        gemini.client = orig
    assert len(calls) == 3 and r.text == "ok"


def test_gemini_falls_back_to_next_model():
    from google.genai import errors
    seen = []

    class Busy:
        def generate_content(self, model, contents, config):
            seen.append(model)
            if model == "primary":
                raise errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
            if model == "slow":
                raise TimeoutError("read timed out")
            if model == "gone":
                raise errors.ClientError(404, {"error": {"code": 404, "message": "nope", "status": "NOT_FOUND"}})
            return _resp(types.Part.from_text(text=model))

    class C:
        models = Busy()

    orig = gemini.client
    gemini.client = lambda: C()
    try:
        r, used = gemini.generate_with_model(models=["primary", "slow", "gone", "backup"], contents="x",
                                             config=types.GenerateContentConfig(), base_delay=0)
    finally:
        gemini.client = orig
    assert used == "backup" and r.text == "backup"
    assert seen == ["primary"] * 3 + ["slow", "gone", "backup"]  # timeouts fall through immediately


def test_snap_enforces_duration():
    words = _words(600)  # 240 s
    total = words[-1]["end"]
    for start, end in [(10_000, 15_000), (20_000, 140_000), (50_000, 95_000)]:
        i0, i1 = curator._snap(words, start, end, total)
        dur = words[i1]["end"] - words[i0]["start"]
        assert curator.MIN_CLIP_MS <= dur <= curator.MAX_CLIP_MS, (start, end, dur)


def test_ass_styles():
    text = "this is a test of captions.".split()
    words = [{"word": w, "start": i * 300, "end": i * 300 + 250} for i, w in enumerate(text)]
    for style in captions.STYLES:
        ass = captions.build_ass(words, style, "Big hook here", 3000)
        assert "[Events]" in ass and ass.count("Dialogue:") == len(words) + 1


def test_kalman_smoothing_reduces_jitter():
    import numpy as np
    rng = np.random.default_rng(0)
    noisy = list(0.5 + rng.normal(0, 0.02, 200))
    smooth = reframer.smooth_path(noisy, 0.5)
    assert np.std(np.diff(smooth)) < np.std(np.diff(noisy)) / 4


def test_ffmpeg_reframe_and_burn():
    if not shutil.which("ffmpeg"):
        return
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "src.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3",
                        "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(src)], check=True)
        res = reframer.reframe(src, td / "v.mp4", td)
        from pipeline.ffmpeg_utils import probe
        info = probe(res.path)
        assert (info.width, info.height) == (1080, 1920), info
        text = "hello there from freeclip".split()
        words = [{"word": w, "start": i * 400, "end": i * 400 + 350} for i, w in enumerate(text)]
        out = captions.render(res.path, words, "HORMOZI", "Test hook", 3000, td / "final.mp4", layout=res.layout,
                              fonts_dir=str(td))
        assert probe(out).duration_ms > 2500


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
