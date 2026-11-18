"""Dynamic word-by-word captions (.ass) + FFmpeg burn-in, with optional Pexels B-roll cutaways.

Three styles:
  HORMOZI     heavy uppercase, thick outline, active word pops (scale) in yellow/green.
  MINIMALIST  clean sentence case, inactive words dimmed, active word full white.
  NEON        glowing cyan outline, active word magenta with a blur glow.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .broll import BrollClip
from .ffmpeg_utils import audio_encoder_args, run, video_encoder_args

log = logging.getLogger(__name__)

PLAY_W, PLAY_H = 1080, 1920
HOOK_DURATION_MS = 3500


def _c(rgb: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS '&HAABBGGRR'."""
    r, g, b = rgb[1:3], rgb[3:5], rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


@dataclass(frozen=True)
class Style:
    font: str
    size: int
    primary: str
    outline: str
    outline_w: float
    shadow: float
    bold: bool
    uppercase: bool
    max_words: int
    max_chars: int
    highlight: tuple[str, ...]
    pop: bool
    dim_inactive: bool = False
    glow: bool = False


STYLES: dict[str, Style] = {
    "HORMOZI": Style("Montserrat Black", 92, "#FFFFFF", "#000000", 7, 3, True, True, 3, 18,
                     ("#FFE600", "#2BFF5C"), pop=True),
    "MINIMALIST": Style("Inter", 64, "#FFFFFF", "#000000", 2.5, 0, False, False, 5, 28,
                        ("#FFFFFF",), pop=False, dim_inactive=True),
    "NEON": Style("Montserrat ExtraBold", 84, "#FFFFFF", "#00F0FF", 4, 0, True, True, 3, 18,
                  ("#FF2BD6", "#B6FF00"), pop=True, glow=True),
}


def _ts(ms: int) -> str:
    ms = max(0, int(ms))
    cs = ms // 10
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "⧵").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[dict[str, Any]], style: Style) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        if not str(w.get("word", "")).strip():
            continue
        gap = w["start"] - cur[-1]["end"] if cur else 0
        length = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
        if cur and (len(cur) >= style.max_words or length > style.max_chars or gap > 700):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?,;:]$", w["word"]) and len(cur) >= 2:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict[str, Any]], style_name: str, hook: str | None, duration_ms: int,
              layout: str = "single") -> str:
    st = STYLES.get(style_name.upper(), STYLES["HORMOZI"])
    # Split-screen: captions sit on the seam; otherwise lower-third above platform UI.
    align, margin_v = (5, 0) if layout == "split" else (2, 560)
    bold = -1 if st.bold else 0
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{st.font},{st.size},{_c(st.primary)},{_c(st.primary)},{_c(st.outline)},{_c("#000000", 0x80)},{bold},0,0,0,100,100,1,0,1,{st.outline_w},{st.shadow},{align},80,80,{margin_v},1
Style: Hook,Montserrat Black,70,{_c("#111111")},{_c("#111111")},{_c("#FFFFFF")},{_c("#FFFFFF")},-1,0,0,0,100,100,0,0,3,18,0,8,90,90,230,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    if hook:
        text = _escape(hook.upper() if st.uppercase else hook)
        events.append(
            f"Dialogue: 2,{_ts(0)},{_ts(min(HOOK_DURATION_MS, duration_ms))},Hook,,0,0,0,,"
            f"{{\\fad(120,250)\\t(0,140,\\fscx104\\fscy104)\\t(140,260,\\fscx100\\fscy100)}}{text}"
        )

    chunks = chunk_words(words, st)
    for ci, chunk in enumerate(chunks):
        color = _c(st.highlight[ci % len(st.highlight)])
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else duration_ms
        chunk_end = min(next_start, chunk[-1]["end"] + 600)
        for wi, w in enumerate(chunk):
            start = chunk[0]["start"] if wi == 0 else w["start"]
            end = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else chunk_end
            if end <= start:
                end = start + 60
            parts = []
            for wj, other in enumerate(chunk):
                token = _escape(other["word"].upper() if st.uppercase else other["word"])
                if wj == wi:
                    tags = f"\\c{color}"
                    if st.dim_inactive:
                        tags += "\\alpha&H00&"
                    if st.pop:
                        tags += "\\t(0,70,\\fscx116\\fscy116)\\t(70,150,\\fscx106\\fscy106)"
                    if st.glow:
                        tags += f"\\3c{color}\\blur4"
                    parts.append(f"{{{tags}}}{token}{{\\r}}")
                elif st.dim_inactive:
                    parts.append(f"{{\\alpha&H70&}}{token}{{\\r}}")
                else:
                    parts.append(token)
            prefix = "{\\fad(60,0)}" if wi == 0 else ""
            if st.glow:
                prefix += "{\\blur2}"
            events.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{prefix}{' '.join(parts)}")
    return header + "\n".join(events) + "\n"


def _filter_path(p: Path) -> str:
    """Escape a path for use inside an FFmpeg filter argument."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render(video_in: Path, words: list[dict[str, Any]], style_name: str, hook: str | None, duration_ms: int,
           out: Path, layout: str = "single", broll: list[BrollClip] | None = None,
           fonts_dir: str = "/usr/share/fonts") -> Path:
    ass_path = out.with_suffix(".ass")
    ass_path.write_text(build_ass(words, style_name, hook, duration_ms, layout), encoding="utf-8")

    inputs = ["-i", str(video_in)]
    graph: list[str] = []
    last = "0:v"
    for i, b in enumerate(broll or []):
        inputs += ["-i", str(b.path)]
        t0, t1 = b.at_ms / 1000, (b.at_ms + b.duration_ms) / 1000
        graph.append(
            f"[{i + 1}:v]trim=0:{b.duration_ms / 1000:.3f},setpts=PTS-STARTPTS+{t0:.3f}/TB,"
            f"scale={PLAY_W}:{PLAY_H}:force_original_aspect_ratio=increase,crop={PLAY_W}:{PLAY_H},"
            f"fps=30,format=yuv420p,fade=t=in:st={t0:.3f}:d=0.2,fade=t=out:st={t1 - 0.2:.3f}:d=0.2[b{i}]"
        )
        graph.append(f"[{last}][b{i}]overlay=0:0:enable='between(t,{t0:.3f},{t1:.3f})':eof_action=pass[v{i}]")
        last = f"v{i}"
    graph.append(f"[{last}]ass='{_filter_path(ass_path)}':fontsdir='{_filter_path(Path(fonts_dir))}'[vout]")

    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(graph),
         "-map", "[vout]", "-map", "0:a:0?", *video_encoder_args("final"), *audio_encoder_args(),
         "-movflags", "+faststart", str(out)])
    return out
