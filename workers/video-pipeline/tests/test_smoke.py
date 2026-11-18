"""Offline smoke tests: no network, no Claude calls. Run: python -m pytest tests (or python tests/test_smoke.py)."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("INTERNAL_API_TOKEN", "test-token-123456")

from pipeline import captions, curator, reframer  # noqa: E402


def _words(n: int, step: int = 400) -> list[dict]:
    out = []
    for i in range(n):
        w = f"word{i}" + ("." if i % 12 == 11 else "")
        out.append({"word": w, "start": i * step, "end": i * step + step - 50})
    return out


def test_schema_is_strict():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "$ref" not in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(curator.OUTPUT_SCHEMA)


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
