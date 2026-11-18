"""Active-speaker reframing: 16:9 -> smooth 1080x1920 (9:16).

1. MediaPipe Face Mesh finds up to 3 faces per analysed frame; faces are tracked by position.
2. Lip-gap variance (landmarks 13/14 normalised by face height) measures mouth motion per track.
3. WebRTC VAD gates speech, so mouth motion only counts while someone is actually talking.
4. The active speaker is chosen with hysteresis, then the crop centre is smoothed by a
   constant-velocity Kalman filter with a dead-zone, which removes jitter without lag.
5. When two well-separated people trade turns, a stacked split-screen layout is used.
Frames are cropped in OpenCV and piped straight into FFmpeg (NVENC when available).
"""

from __future__ import annotations

import logging
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .ffmpeg_utils import FFmpegError, audio_encoder_args, extract_wav, probe, video_encoder_args

log = logging.getLogger(__name__)

OUT_W, OUT_H = 1080, 1920
ANALYSIS_FPS = 10.0
ANALYSIS_WIDTH = 640

# Face Mesh landmark indices
LIP_UPPER, LIP_LOWER = 13, 14
FOREHEAD, CHIN = 10, 152


@dataclass
class Track:
    tid: int
    cx: float
    cy: float
    size: float
    last_seen: int
    hits: int = 0
    xs: dict[int, float] = field(default_factory=dict)  # sample index -> cx
    ys: dict[int, float] = field(default_factory=dict)
    sizes: dict[int, float] = field(default_factory=dict)
    mouth: dict[int, float] = field(default_factory=dict)


@dataclass
class ReframeResult:
    path: Path
    layout: str  # "single" | "split" | "center" | "vertical"


# --------------------------------------------------------------------------- smoothing


class Kalman1D:
    """Constant-velocity Kalman filter for a 1-D position."""

    def __init__(self, x0: float, q: float = 2e-4, r: float = 4e-3) -> None:
        self.x = np.array([x0, 0.0])
        self.P = np.eye(2)
        self.Q = q * np.array([[0.25, 0.5], [0.5, 1.0]])
        self.R = r

    def step(self, z: float | None, dt: float = 1.0) -> float:
        F = np.array([[1.0, dt], [0.0, 1.0]])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + self.Q
        if z is not None:
            H = np.array([1.0, 0.0])
            S = H @ self.P @ H + self.R
            K = self.P @ H / S
            self.x = self.x + K * (z - H @ self.x)
            self.P = (np.eye(2) - np.outer(K, H)) @ self.P
        return float(self.x[0])


def smooth_path(targets: list[float | None], default: float, dead_zone: float = 0.04) -> np.ndarray:
    """Kalman forward pass + dead-zone + centred moving average. Values are normalised 0..1."""
    first = next((t for t in targets if t is not None), default)
    kf = Kalman1D(first)
    held = first
    out = []
    for t in targets:
        # Dead-zone: ignore small target moves so the camera holds still on a steady speaker.
        if t is not None and abs(t - held) > dead_zone:
            held = t
        out.append(kf.step(held))
    arr = np.array(out, dtype=np.float64)
    k = 5
    if len(arr) > k:
        pad = np.pad(arr, (k // 2, k // 2), mode="edge")
        arr = np.convolve(pad, np.ones(k) / k, mode="valid")
    return arr


# --------------------------------------------------------------------------- VAD


def speech_mask(wav: Path, n_samples: int, sample_dt: float, aggressiveness: int = 2) -> np.ndarray:
    import webrtcvad

    vad = webrtcvad.Vad(aggressiveness)
    with wave.open(str(wav), "rb") as wf:
        rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    frame_ms = 30
    step = int(rate * frame_ms / 1000) * 2  # 16-bit mono
    flags = []
    for off in range(0, len(pcm) - step + 1, step):
        try:
            flags.append(vad.is_speech(pcm[off : off + step], rate))
        except Exception:  # noqa: BLE001
            flags.append(False)
    flags_arr = np.array(flags, dtype=bool)
    mask = np.zeros(n_samples, dtype=bool)
    for i in range(n_samples):
        t0 = int(i * sample_dt * 1000 / frame_ms)
        t1 = max(t0 + 1, int((i + 1) * sample_dt * 1000 / frame_ms))
        window = flags_arr[t0:t1]
        mask[i] = bool(window.any()) if window.size else False
    return mask


# --------------------------------------------------------------------------- analysis


def analyze(video: Path, fps: float, total_frames: int) -> tuple[list[Track], int, float]:
    import mediapipe as mp

    stride = max(1, round(fps / ANALYSIS_FPS))
    sample_dt = stride / fps
    tracks: list[Track] = []
    next_id = 0
    cap = cv2.VideoCapture(str(video))
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False, max_num_faces=3, refine_landmarks=False,
        min_detection_confidence=0.5, min_tracking_confidence=0.5,
    )
    sample = 0
    frame_idx = 0
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if frame_idx % stride == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                h, w = frame.shape[:2]
                scale = ANALYSIS_WIDTH / w
                small = cv2.resize(frame, (ANALYSIS_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)
                res = face_mesh.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
                for lm in res.multi_face_landmarks or []:
                    pts = lm.landmark
                    xs = [p.x for p in pts]
                    ys = [p.y for p in pts]
                    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                    face_h = max(1e-4, abs(pts[CHIN].y - pts[FOREHEAD].y))
                    mouth = abs(pts[LIP_LOWER].y - pts[LIP_UPPER].y) / face_h
                    size = max(xs) - min(xs)
                    if size < 0.03:  # ignore tiny background faces
                        continue
                    best = min(
                        (t for t in tracks if sample - t.last_seen <= 20 and sample not in t.xs),
                        key=lambda t: abs(t.cx - cx) + abs(t.cy - cy),
                        default=None,
                    )
                    if best is None or abs(best.cx - cx) + abs(best.cy - cy) > 0.18:
                        best = Track(next_id, cx, cy, size, sample)
                        next_id += 1
                        tracks.append(best)
                    best.cx, best.cy, best.size, best.last_seen = cx, cy, size, sample
                    best.hits += 1
                    best.xs[sample], best.ys[sample] = cx, cy
                    best.sizes[sample], best.mouth[sample] = size, mouth
                sample += 1
            frame_idx += 1
    finally:
        cap.release()
        face_mesh.close()
    n_samples = max(sample, 1)
    major = [t for t in tracks if t.hits >= max(3, 0.15 * n_samples)]
    major.sort(key=lambda t: t.hits, reverse=True)
    return major[:3], n_samples, sample_dt


def _mouth_activity(track: Track, i: int, window: int = 6) -> float:
    vals = [track.mouth[j] for j in range(i - window, i + 1) if j in track.mouth]
    return float(np.std(vals)) + 0.25 * float(np.mean(vals)) if len(vals) >= 2 else 0.0


def choose_speakers(tracks: list[Track], n: int, speech: np.ndarray, sample_dt: float) -> list[int | None]:
    """Active track id per analysis sample, with a minimum hold time to prevent flicker."""
    min_hold = max(1, int(1.2 / sample_dt))
    active: list[int | None] = []
    current: int | None = tracks[0].tid if tracks else None
    held = 0
    for i in range(n):
        held += 1
        present = [t for t in tracks if i in t.xs]
        if speech[i] and present:
            scores = {t.tid: _mouth_activity(t, i) for t in present}
            best = max(scores, key=lambda k: scores[k])
            cur_score = scores.get(current, 0.0) if current is not None else 0.0
            if best != current and held >= min_hold and scores[best] > cur_score * 1.35 + 0.004:
                current, held = best, 0
        elif current is not None and all(t.tid != current for t in present) and present and held >= min_hold:
            current, held = max(present, key=lambda t: t.size).tid, 0
        active.append(current)
    return active


# --------------------------------------------------------------------------- rendering


def _ffmpeg_writer(out: Path, fps: float, audio_src: Path, has_audio: bool) -> subprocess.Popen[bytes]:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}", "-r", f"{fps:.5f}", "-i", "-"]
    if has_audio:
        cmd += ["-i", str(audio_src), "-map", "0:v:0", "-map", "1:a:0", *audio_encoder_args()]
    cmd += [*video_encoder_args("intermediate"), "-r", f"{fps:.5f}", "-shortest", "-movflags", "+faststart", str(out)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


def _crop(frame: np.ndarray, cx: float, cy: float, cw: int, ch: int) -> np.ndarray:
    h, w = frame.shape[:2]
    x0 = int(round(cx * w - cw / 2))
    y0 = int(round(cy * h - ch / 2))
    x0 = max(0, min(w - cw, x0))
    y0 = max(0, min(h - ch, y0))
    return frame[y0 : y0 + ch, x0 : x0 + cw]


def _interp(path: np.ndarray, frame_idx: int, stride: int) -> float:
    pos = frame_idx / stride
    i = int(pos)
    if i >= len(path) - 1:
        return float(path[-1])
    f = pos - i
    return float(path[i] * (1 - f) + path[i + 1] * f)


def reframe(segment: Path, out: Path, workdir: Path) -> ReframeResult:
    info = probe(segment)
    fps = info.fps
    total_frames = int(info.duration_ms / 1000 * fps)
    w, h = info.width, info.height

    if w / h <= OUT_W / OUT_H + 0.02:
        # Already vertical: scale/pad only.
        from .ffmpeg_utils import run

        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(segment), "-vf",
             f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2,setsar=1",
             *video_encoder_args("intermediate"), *audio_encoder_args(), str(out)])
        return ReframeResult(out, "vertical")

    tracks, n, sample_dt = analyze(segment, fps, total_frames)
    stride = max(1, round(fps / ANALYSIS_FPS))
    wav = extract_wav(segment, workdir / f"{segment.stem}_vad.wav") if info.has_audio else None
    speech = speech_mask(wav, n, sample_dt) if wav else np.ones(n, dtype=bool)
    active = choose_speakers(tracks, n, speech, sample_dt)

    # ---- layout decision
    layout = "center" if not tracks else "single"
    single_cw = int(h * OUT_W / OUT_H) // 2 * 2
    if len(tracks) >= 2:
        a, b = sorted(tracks[:2], key=lambda t: t.cx)
        both_present = sum(1 for i in range(n) if i in a.xs and i in b.xs) / n
        separated = abs(a.cx - b.cx) * w > single_cw * 0.8
        switches = sum(1 for i in range(1, n) if active[i] != active[i - 1] and active[i] is not None)
        share = {t.tid: sum(1 for x in active if x == t.tid) / n for t in (a, b)}
        if both_present > 0.6 and separated and (switches >= 3 or min(share.values()) > 0.25):
            layout = "split"

    proc = _ffmpeg_writer(out, fps, segment, info.has_audio)
    assert proc.stdin is not None
    cap = cv2.VideoCapture(str(segment))
    try:
        if layout == "split":
            a, b = sorted(tracks[:2], key=lambda t: t.cx)
            paths = []
            for t in (a, b):
                xs = smooth_path([t.xs.get(i) for i in range(n)], t.cx)
                ys = smooth_path([t.ys.get(i) for i in range(n)], t.cy, dead_zone=0.06)
                paths.append((xs, ys))
            half_h = OUT_H // 2
            ch = int(h * 0.85) // 2 * 2
            cw = int(ch * OUT_W / half_h) // 2 * 2
            if cw > w:
                cw = w // 2 * 2
                ch = int(cw * half_h / OUT_W) // 2 * 2
        else:
            if layout == "single":
                by_id = {t.tid: t for t in tracks}
                targets = [
                    by_id[a_].xs.get(i) if a_ is not None and a_ in by_id else None for i, a_ in enumerate(active)
                ]
                xs = smooth_path(targets, 0.5)
            else:
                xs = np.full(max(n, 1), 0.5)
            cw = min(w, single_cw)

        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if layout == "split":
                tiles = []
                for xs_, ys_ in paths:
                    crop = _crop(frame, _interp(xs_, frame_idx, stride), _interp(ys_, frame_idx, stride), cw, ch)
                    tiles.append(cv2.resize(crop, (OUT_W, half_h), interpolation=cv2.INTER_LINEAR))
                composed = np.vstack(tiles)
                cv2.line(composed, (0, half_h), (OUT_W, half_h), (0, 0, 0), 6)
            else:
                crop = _crop(frame, _interp(xs, frame_idx, stride), 0.5, cw, h)
                composed = cv2.resize(crop, (OUT_W, OUT_H), interpolation=cv2.INTER_LANCZOS4)
            proc.stdin.write(composed.tobytes())
            frame_idx += 1
    except BrokenPipeError:
        pass
    finally:
        cap.release()
        proc.stdin.close()
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        code = proc.wait()
    if code != 0:
        raise FFmpegError(f"reframe encode failed: {stderr[-2000:]}")
    log.info("reframed %s: layout=%s tracks=%d", segment.name, layout, len(tracks))
    return ReframeResult(out, layout)
