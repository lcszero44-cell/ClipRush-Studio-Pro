"""FFmpeg video editor & renderer for ClipRush Studio Pro:
- Mode 1: Viral 9:16 Cuts (Viral Blur, Tela Cheia Pro, Clipe Limpo)
  * Open-Loop Curiosity Hook (max 6 words, 2.5s top, MarginL/R=60, MarginV=140)
  * Optional 0.3s Peak Preview Flash at second 0
  * Infinite Loop (Hard Climax Cut — zero dead tail at clip end)
  * Visual Comment Trigger CTA Badge in final 2 seconds (MarginL/R=60, MarginV=340)
  * Auto-Fit Whisper Subtitles (MarginL/R=60, MarginV=260) & 3 visual styles
  * Smart Impact Zoom (1.00x -> 1.18x -> 1.00x in 0.6s) + Micro-Shake + Contrast Boost
  * Synthesized Impact SFX (Whoosh + Sub-Bass via FFmpeg) with subtle audio ducking
  * Jump-Cuts / Smart Acceleration (1.25x) of internal dead spans (>2.5s)
  * 3px Neon Visual Retention Progress Bar (purple #6366F1 -> cyan #22D3EE in Safe Zone)
  * Professional Audio Normalization (-14 LUFS + Limiter) for mobile speakers
  * Smart Action Tracking (OpenCV Optical Flow Center-of-Mass per 1s window) in Tela Cheia Pro
- Mode 2: Long Gameplay 16:9 (1920x1080) with dead-stretch removal + 1080p Thumbnail capture
"""

from __future__ import annotations

import math
import re
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PIL import Image

from backend.config import TEMP_DIR, get_ffmpeg_bins, get_output_dir, load_settings
from backend.errors import XRayError

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_WHISPER_MODEL_CACHE: dict[str, Any] = {}

SUBTITLE_STYLES: dict[str, dict[str, Any]] = {
    "amarelo_viral": {
        "id": "amarelo_viral",
        "name": "Amarelo Viral com Borda Preta",
        "primary": "&H0000EAFF",
        "outline_col": "&H00000000",
        "back_col": "&H88000000",
        "outline": 6,
        "shadow": 3,
        "fontsize_sub": 58,
        "fontsize_hook": 60,
        "keyword_highlight": False,
    },
    "branco_clean": {
        "id": "branco_clean",
        "name": "Branco Clean Moderno",
        "primary": "&H00FFFFFF",
        "outline_col": "&H00181210",
        "back_col": "&H70000000",
        "outline": 4,
        "shadow": 2,
        "fontsize_sub": 54,
        "fontsize_hook": 56,
        "keyword_highlight": False,
    },
    "destaque_keyword": {
        "id": "destaque_keyword",
        "name": "Destaque Palavra-Chave",
        "primary": "&H00FFFFFF",
        "outline_col": "&H000B0B0F",
        "back_col": "&H88000000",
        "outline": 5,
        "shadow": 3,
        "fontsize_sub": 56,
        "fontsize_hook": 58,
        "keyword_highlight": True,
    },
}

_STOPWORDS_PT = {
    "DE", "DA", "DO", "DAS", "DOS", "EM", "NO", "NA", "NOS", "NAS", "UM", "UMA",
    "QUE", "COM", "POR", "PARA", "PRA", "MAS", "MAIS", "COMO", "SEU", "SUA",
    "ELE", "ELA", "ELES", "ELAS", "ISSO", "ESSA", "ESSE", "AQUI", "ALI", "FOI",
    "ERA", "ESTA", "ESTE", "TEM", "VAI", "VOU", "ATE", "SO", "NAO", "SIM", "JA",
    "THE", "AND", "FOR", "WITH", "THIS", "THAT", "YOU", "ARE", "WAS", "NOT",
}


def _detect_video_encoder(ffmpeg_bin: str, preference: str = "auto") -> list[str]:
    if preference == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if preference in ("auto", "h264_nvenc"):
        try:
            res = subprocess.run(
                [ffmpeg_bin, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=CREATE_NO_WINDOW,
            )
            if "h264_nvenc" in (res.stdout or ""):
                test = subprocess.run(
                    [
                        ffmpeg_bin, "-v", "error",
                        "-f", "lavfi", "-i", "color=c=black:s=128x128:d=0.1",
                        "-c:v", "h264_nvenc", "-f", "null", "-",
                    ],
                    capture_output=True,
                    timeout=5,
                    creationflags=CREATE_NO_WINDOW,
                )
                if test.returncode == 0:
                    return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22"]
        except Exception:
            pass
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _escape_ffmpeg_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    s = s.replace(":", r"\:")
    s = s.replace("'", r"\'")
    return s


def _ensure_neon_progress_bar_png() -> Path:
    """Create and cache a crisp 960x3 neon purple (#6366F1) -> cyan (#22D3EE) gradient strip."""
    bar_path = TEMP_DIR / "neon_progress_bar_960x3.png"
    if bar_path.exists():
        return bar_path
    bar_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 960, 3
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for x in range(w):
        t = x / max(1, w - 1)
        r = int(99 + (34 - 99) * t)
        g = int(102 + (211 - 102) * t)
        b = int(241 + (238 - 241) * t)
        for y in range(h):
            px[x, y] = (r, g, b, 245)
    img.save(bar_path, format="PNG")
    return bar_path


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").strip()


def _smart_wrap_lines(text: str, max_chars: int = 30, max_lines: int = 3) -> list[str]:
    clean = re.sub(r"\s+", " ", _ass_escape(text)).strip()
    if not clean:
        return []
    lines = textwrap.wrap(clean, width=max_chars, break_long_words=True, break_on_hyphens=True)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1 :])]
        if len(lines[-1]) > max_chars + 4:
            lines[-1] = lines[-1][: max_chars + 1].rstrip() + "..."
    return lines


def _apply_keyword_highlight(lines: list[str]) -> str:
    styled_lines = []
    for line in lines:
        words = line.split(" ")
        scored = []
        for idx, w in enumerate(words):
            bare = re.sub(r"[^A-Z0-9À-Ÿ]", "", w.upper())
            if len(bare) >= 4 and bare not in _STOPWORDS_PT:
                scored.append((len(bare) + (4 if any(ch.isdigit() for ch in bare) else 0), idx))
        scored.sort(reverse=True)
        chosen = {idx for _, idx in scored[:2]}
        if not chosen and words:
            chosen.add(len(words) - 1)

        out_words = []
        for idx, w in enumerate(words):
            if idx in chosen:
                out_words.append(r"{\c&H0000EAFF&\fscx108\fscy108}" + w + r"{\r}")
            else:
                out_words.append(w)
        styled_lines.append(" ".join(out_words))
    return r"\N".join(styled_lines)


def _format_ass_text(text: str, max_chars: int, highlight_keywords: bool) -> str:
    lines = _smart_wrap_lines(text.upper(), max_chars=max_chars, max_lines=3)
    if not lines:
        return ""
    if highlight_keywords:
        return _apply_keyword_highlight(lines)
    return r"\N".join(lines)


def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100)) % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _transcribe_whisper_segments(video_path: str, start: float, end: float) -> list[dict[str, Any]]:
    settings = load_settings()
    ffmpeg, _ = get_ffmpeg_bins()
    wav_path = TEMP_DIR / f"sub_{uuid.uuid4().hex[:8]}.wav"
    try:
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-ss", f"{start:.2f}",
            "-to", f"{end:.2f}",
            "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000",
            str(wav_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if res.returncode != 0 or not wav_path.exists():
            return []

        from faster_whisper import WhisperModel

        model_name = settings.get("whisper_model", "base")
        if model_name not in _WHISPER_MODEL_CACHE:
            _WHISPER_MODEL_CACHE[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
        model = _WHISPER_MODEL_CACHE[model_name]
        lang = settings.get("whisper_language") or "pt"
        segments, _ = model.transcribe(str(wav_path), language=lang if lang != "auto" else None, vad_filter=True)

        out = []
        for seg in segments:
            txt = (seg.text or "").strip()
            if txt:
                out.append({"start": float(seg.start), "end": float(seg.end), "text": txt})
        return out
    except Exception:
        return []
    finally:
        if wav_path.exists():
            try:
                wav_path.unlink()
            except OSError:
                pass


def _compute_smart_pacing_plan(
    video_path: str,
    start: float,
    end: float,
    impact_times: list[float],
    has_audio: bool,
) -> tuple[list[dict[str, float]], list[float], float]:
    """Detect internal intermediate dead stretches (>2.5s without relevant audio or motion)
    inside [start, end] and smoothly accelerate them at 1.25x to keep high retention pacing."""
    duration = max(0.5, end - start)
    if duration < 7.0:
        return [{"s": 0.0, "e": round(duration, 2), "speed": 1.0}], impact_times, duration

    step = 0.5
    bins = max(1, int(math.ceil(duration / step)))
    motion_arr = np.zeros(bins, dtype=np.float32)
    audio_arr = np.zeros(bins, dtype=np.float32)

    # 1. Fast OpenCV motion scan inside clip window
    cap = cv2.VideoCapture(video_path)
    if cap.isOpened():
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            stride = max(1, int(round(fps * step)))
            prev_g = None
            idx = 0
            fnum = 0
            while idx < bins:
                if not cap.grab():
                    break
                if fnum % stride == 0:
                    ok, frame = cap.retrieve()
                    if not ok or frame is None:
                        break
                    g = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
                    if prev_g is not None:
                        motion_arr[idx] = float(np.mean(cv2.absdiff(g, prev_g)))
                    prev_g = g
                    idx += 1
                fnum += 1
        finally:
            cap.release()

    # 2. Fast FFmpeg PCM RMS inside clip window
    ffmpeg, _ = get_ffmpeg_bins()
    if has_audio and ffmpeg:
        sr = 8000
        res = subprocess.run(
            [
                ffmpeg, "-v", "error",
                "-ss", f"{start:.2f}", "-t", f"{duration:.2f}",
                "-i", video_path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1",
            ],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout:
            samples = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            win = max(1, int(sr * step))
            for i in range(bins):
                seg = samples[i * win : (i + 1) * win]
                if seg.size:
                    audio_arr[i] = float(np.sqrt(np.mean(seg * seg)))

    m_norm = motion_arr / max(float(np.percentile(motion_arr, 90)) if bins > 2 else 1.0, 1e-5)
    a_norm = audio_arr / max(float(np.percentile(audio_arr, 90)) if bins > 2 else 1.0, 1e-5)

    # Mark low-activity bins (>2.5s continuous = >=5 bins), never accelerating first 1.5s or final 2.0s or near impacts
    dead = (m_norm < 0.35) & (a_norm < 0.32)
    dead[: min(bins, 3)] = False
    dead[max(0, bins - 4) :] = False
    for imp in impact_times:
        ib = int(imp / step)
        dead[max(0, ib - 2) : min(bins, ib + 3)] = False

    accel_mask = np.zeros(bins, dtype=np.bool_)
    run = 0
    for i in range(bins):
        if dead[i]:
            run += 1
        else:
            if run >= 5:  # >2.5s internal dead stretch
                accel_mask[i - run : i] = True
            run = 0
    if run >= 5:
        accel_mask[bins - run : bins] = True

    if not np.any(accel_mask):
        return [{"s": 0.0, "e": round(duration, 2), "speed": 1.0}], impact_times, duration

    # Convert mask into contiguous (s, e, speed) segments (cap to max 5 segments for clean fast concat)
    segments: list[dict[str, float]] = []
    cur_speed = 1.25 if accel_mask[0] else 1.0
    seg_s = 0.0
    for i in range(1, bins):
        sp = 1.25 if accel_mask[i] else 1.0
        if sp != cur_speed:
            seg_e = round(min(duration, i * step), 2)
            if seg_e - seg_s >= 0.4:
                segments.append({"s": round(seg_s, 2), "e": seg_e, "speed": cur_speed})
                seg_s = seg_e
                cur_speed = sp
    if duration - seg_s >= 0.25:
        segments.append({"s": round(seg_s, 2), "e": round(duration, 2), "speed": cur_speed})

    if len(segments) <= 1 or len(segments) > 7:
        return [{"s": 0.0, "e": round(duration, 2), "speed": 1.0}], impact_times, duration

    # Remap impact times & total paced duration
    def _remap(orig_t: float) -> float:
        acc = 0.0
        for seg in segments:
            if orig_t <= seg["e"]:
                return acc + max(0.0, orig_t - seg["s"]) / seg["speed"]
            acc += (seg["e"] - seg["s"]) / seg["speed"]
        return acc

    new_dur = round(sum((seg["e"] - seg["s"]) / seg["speed"] for seg in segments), 2)
    new_impacts = [round(_remap(t), 2) for t in impact_times]
    return segments, new_impacts, max(2.0, new_dur)


def _compute_action_tracking_expr(
    video_path: str,
    start: float,
    end: float,
    time_offset: float = 0.0,
) -> str:
    """Compute Optical Flow center-of-mass X per 1-second window via OpenCV for Tela Cheia Pro,
    returning a silky-smooth piecewise-linear FFmpeg crop-center ratio expression in [0.22..0.78]."""
    duration = max(1.0, end - start)
    n_windows = max(1, min(14, int(math.ceil(duration))))
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return "0.5"

    centers: list[float] = []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
        stride = max(1, int(round(fps * 0.5)))  # 2 samples per 1s window
        prev_g = None
        fnum = 0
        win_vals: list[float] = []
        total_samples = n_windows * 2
        sample_idx = 0

        while sample_idx < total_samples:
            if not cap.grab():
                break
            if fnum % stride == 0:
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    break
                g = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
                if prev_g is not None:
                    flow = cv2.calcOpticalFlowFarneback(prev_g, g, None, 0.5, 2, 9, 2, 5, 1.1, 0)
                    mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
                    # Focus on high-motion pixels (above 70th percentile) to lock onto gunfire/enemies/action
                    thresh = float(np.percentile(mag, 70))
                    weights = np.where(mag >= thresh, mag, 0.0)
                    total_w = float(np.sum(weights))
                    if total_w > 1e-3:
                        xs = np.linspace(0.0, 1.0, weights.shape[1], dtype=np.float32)
                        cx = float(np.sum(weights * xs[None, :]) / total_w)
                        win_vals.append(cx)
                prev_g = g
                sample_idx += 1
                if sample_idx % 2 == 0:
                    centers.append(float(np.mean(win_vals)) if win_vals else 0.5)
                    win_vals = []
            fnum += 1
        if win_vals:
            centers.append(float(np.mean(win_vals)))
    finally:
        cap.release()

    if len(centers) < 2:
        val = round(max(0.24, min(0.76, centers[0])) if centers else 0.5, 3)
        return str(val)

    # Exponential smoothing so camera pans smoothly without jitter
    smoothed = [max(0.24, min(0.76, centers[0]))]
    for c in centers[1:]:
        clamped = max(0.24, min(0.76, c))
        smoothed.append(round(smoothed[-1] * 0.55 + clamped * 0.45, 3))
    smoothed[0] = round(smoothed[0], 3)

    # Build piecewise-linear FFmpeg expression across 1s windows
    step_sec = duration / len(smoothed)
    terms: list[str] = []
    for i in range(len(smoothed)):
        t0 = round(time_offset + i * step_sec, 2)
        t1 = round(time_offset + (i + 1) * step_sec, 2)
        c0 = smoothed[i]
        c1 = smoothed[min(len(smoothed) - 1, i + 1)]
        seg_d = max(0.1, round(t1 - t0, 2))
        if i == 0:
            terms.append(f"if(lt(t,{t0}),{c0},0)")
        if i < len(smoothed) - 1:
            terms.append(f"if(between(t,{t0},{t1}),{c0}+({round(c1 - c0, 3)})*(t-{t0})/{seg_d},0)")
        else:
            terms.append(f"if(gte(t,{t0}),{c0},0)")
    return "(" + "+".join(terms) + ")"


def _build_autofit_ass(
    out_ass: Path,
    hook_text: str,
    show_hook: bool,
    whisper_segments: list[dict[str, Any]],
    subtitle_style_id: str = "amarelo_viral",
    cta_comment: str = "",
    show_cta: bool = False,
    total_duration: float = 12.0,
    time_offset: float = 0.0,
) -> Optional[Path]:
    if not show_hook and not whisper_segments and not (show_cta and cta_comment.strip()):
        return None

    st = SUBTITLE_STYLES.get(subtitle_style_id, SUBTITLE_STYLES["amarelo_viral"])
    kw_mode = bool(st["keyword_highlight"])

    hook_fs = st["fontsize_hook"]
    if len(hook_text) > 30:
        hook_fs = max(48, hook_fs - 8)
    elif len(hook_text) > 22:
        hook_fs = max(52, hook_fs - 4)

    events: list[str] = []
    if show_hook and hook_text.strip():
        formatted_hook = _format_ass_text(hook_text, max_chars=28, highlight_keywords=kw_mode)
        if formatted_hook:
            events.append(
                f"Dialogue: 2,0:00:00.00,0:00:02.50,HookTop,,0,0,0,,{{\\fad(100,180)}}{formatted_hook}"
            )

    for seg in whisper_segments:
        formatted_sub = _format_ass_text(seg["text"], max_chars=30, highlight_keywords=kw_mode)
        if not formatted_sub:
            continue
        s_ts = _ass_time(seg["start"] + time_offset)
        e_ts = _ass_time(seg["end"] + time_offset)
        events.append(f"Dialogue: 0,{s_ts},{e_ts},SubBottom,,0,0,0,,{formatted_sub}")

    if show_cta and cta_comment.strip() and total_duration >= 3.0:
        cta_start = max(0.5, total_duration - 2.10)
        cta_end = max(cta_start + 1.0, total_duration - 0.05)
        cta_wrapped = r"\N".join(_smart_wrap_lines(cta_comment.strip(), max_chars=28, max_lines=2))
        if cta_wrapped:
            events.append(
                f"Dialogue: 3,{_ass_time(cta_start)},{_ass_time(cta_end)},CTABadge,,0,0,0,,"
                f"{{\\fad(160,120)}}COMENTA AI:  {cta_wrapped.upper()}"
            )

    if not events:
        return None

    hook_primary = "&H0000EAFF" if subtitle_style_id == "amarelo_viral" else "&H00FFFFFF"
    ass_content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: HookTop,Arial,{hook_fs},{hook_primary},&H0000FFFF,{st['outline_col']},{st['back_col']},"
        f"-1,0,0,0,100,100,1,0,1,{st['outline']},{st['shadow']},8,60,60,140,1\n"
        f"Style: SubBottom,Arial,{st['fontsize_sub']},{st['primary']},&H0000FFFF,{st['outline_col']},{st['back_col']},"
        f"-1,0,0,0,100,100,1,0,1,{st['outline']},{st['shadow']},2,60,60,260,1\n"
        "Style: CTABadge,Arial,40,&H00F8EE22,&H00FFFFFF,&HAA12151E,&HAA12151E,"
        "-1,0,0,0,100,100,1,0,3,14,0,2,60,60,340,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events)
        + "\n"
    )
    out_ass.write_text(ass_content, encoding="utf-8")
    return out_ass


def _build_smart_zoom_and_shake(
    impact_times: list[float],
    enable_impact_zoom: bool,
    dialogue_segments: list[dict[str, Any]],
    subtitles_mode: str,
    clip_duration: float,
) -> tuple[str, str, str, str]:
    zoom_terms: list[str] = []
    shake_x_terms: list[str] = []
    shake_y_terms: list[str] = []
    impact_gate_terms: list[str] = []

    if enable_impact_zoom and impact_times:
        for imp in impact_times[:3]:
            t0 = max(0.15, round(float(imp) - 0.25, 2))
            t1 = round(t0 + 0.60, 2)
            pulse = f"sin(PI*(t-{t0})/0.60)"
            zoom_terms.append(f"if(between(t,{t0},{t1}),0.18*{pulse},0)")
            shake_x_terms.append(f"if(between(t,{t0},{t1}),10*{pulse}*sin(64*(t-{t0})),0)")
            shake_y_terms.append(f"if(between(t,{t0},{t1}),7*{pulse}*cos(52*(t-{t0})),0)")
            impact_gate_terms.append(f"if(between(t,{t0},{t1}),{pulse},0)")

    if subtitles_mode == "whisper":
        if dialogue_segments:
            for seg in dialogue_segments[:5]:
                s0 = round(float(seg["start"]), 2)
                s1 = round(float(seg["end"]), 2)
                dur = max(0.6, round(s1 - s0, 2))
                zoom_terms.append(f"if(between(t,{s0},{s1}),0.06*(t-{s0})/{dur},0)")
        else:
            dur = max(2.0, round(clip_duration, 2))
            zoom_terms.append(f"0.06*min(1,t/{dur})")

    zoom_expr = "1+" + "+".join(zoom_terms) if zoom_terms else "1"
    shake_x = "+".join(shake_x_terms) if shake_x_terms else "0"
    shake_y = "+".join(shake_y_terms) if shake_y_terms else "0"
    impact_pulse_expr = "+".join(impact_gate_terms) if impact_gate_terms else "0"
    return zoom_expr, shake_x, shake_y, impact_pulse_expr


def _has_audio_stream(video_path: str) -> bool:
    _, ffprobe = get_ffmpeg_bins()
    if not ffprobe:
        return True
    try:
        res = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        return bool((res.stdout or "").strip())
    except Exception:
        return True


def _build_pro_audio_chain(
    base_audio_label: str,
    impact_times: list[float],
    enable_impact_sfx: bool,
    enable_audio_norm: bool,
    has_audio: bool,
) -> tuple[str, str]:
    """Build professional audio processing chain:
    - -14 LUFS loudness normalization (loudnorm) + protective limiter for mobile speakers
    - Subtle gameplay ducking right during Impact SFX peaks
    - Synthesized Impact SFX (Whoosh + Sub-Bass) mixed cleanly with protective final limiter
    """
    if not has_audio:
        return "", ""

    parts: list[str] = []
    cur = base_audio_label

    if enable_audio_norm:
        parts.append(f"[{cur}]loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.95:attack=5:release=50[anorm]")
        cur = "anorm"

    if enable_impact_sfx and impact_times:
        peaks = [max(0.2, float(t)) for t in impact_times[:3]]
        # Subtle ducking of gameplay audio (-22%) right on the 0.36s impact SFX peaks
        duck_terms = [f"between(t,{round(t, 2)},{round(t + 0.36, 2)})" for t in peaks]
        duck_cond = "+".join(duck_terms)
        parts.append(f"[{cur}]volume='if(gt({duck_cond},0),0.78,1.0)':eval=frame[aduck]")
        cur = "aduck"

        mix_inputs = [f"[{cur}]"]
        for idx, t_sec in enumerate(peaks):
            delay_ms = max(10, int(round(t_sec * 1000)))
            lbl = f"sfx{idx}"
            parts.append(
                f"aevalsrc=exprs='0.24*exp(-7.5*t)*sin(2*PI*(95-55*t)*t)+0.06*exp(-11*t)*sin(2*PI*220*t*t)':"
                f"s=48000:d=0.36:c=stereo,"
                f"lowpass=f=320,afade=t=in:st=0:d=0.02,afade=t=out:st=0.22:d=0.14,"
                f"adelay={delay_ms}|{delay_ms}[{lbl}]"
            )
            mix_inputs.append(f"[{lbl}]")

        parts.append(
            f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.96:attack=4:release=40[aout]"
        )
        cur = "aout"

    return ";".join(parts), cur


def extract_clip_frame(video_path: str, out_path: Path, at_ratio: float = 0.45) -> Optional[str]:
    ffmpeg, ffprobe = get_ffmpeg_bins()
    if not ffmpeg or not Path(video_path).exists():
        return None
    sec = 2.0
    try:
        res = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", video_path],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout.strip():
            sec = max(0.5, float(res.stdout.strip()) * at_ratio)
    except Exception:
        pass

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{sec:.2f}",
        "-i", video_path,
        "-frames:v", "1",
        "-vf", "scale=640:-2",
        "-q:v", "3",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
    return str(out_path) if res.returncode == 0 and out_path.exists() else None


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", name).strip("_")
    return cleaned[:48] or "clipe"


def render_viral_clip(
    video_path: str,
    moment: dict[str, Any],
    style: str = "viral_blur",
    subtitles_mode: str = "off",
    subtitle_style: str = "amarelo_viral",
    enable_hook: bool = True,
    enable_impact_zoom: bool = True,
    enable_preview_flash: bool = True,
    enable_infinite_loop: bool = True,
    enable_cta_comment: bool = True,
    enable_impact_sfx: bool = True,
    enable_smart_pacing: bool = True,
    enable_progress_bar: bool = True,
    enable_audio_norm: bool = True,
    enable_action_tracking: bool = True,
    project_name: str = "gameplay",
) -> dict[str, Any]:
    """Render a 9:16 viral clip (1080x1920) with full algorithmic retention mechanics."""
    ffmpeg, _ = get_ffmpeg_bins()
    if not ffmpeg:
        raise XRayError("FFmpeg nao encontrado para renderizar o clipe.", category="FFMPEG", status_code=500)

    settings = load_settings()
    out_dir = get_output_dir()
    start = float(moment["start"])
    end = float(moment["end"])
    peak = float(moment.get("peak", (start + end) / 2.0))
    is_death = bool(moment.get("is_player_death", False))

    # 1. Infinite Loop: Hard climax cut right after peak/execution leaving zero dead tail
    if enable_infinite_loop and (end - start) >= 7.0:
        tail_allowance = 2.4 if is_death else 1.25
        climax_end = max(start + 6.0, peak + tail_allowance)
        if climax_end < end:
            end = round(climax_end, 2)

    raw_duration = max(2.5, end - start)
    rank = int(moment.get("rank", 1))
    hook_text = (moment.get("hook_2s") or "OLHA ESSE FINAL...").strip().upper()
    cta_text = (moment.get("cta_comment") or "Voce escaparia dessa?").strip()
    raw_impacts = [float(t) for t in (moment.get("impact_times") or [min(raw_duration * 0.55, 3.8)]) if float(t) < raw_duration - 0.3]
    if not raw_impacts:
        raw_impacts = [round(min(raw_duration * 0.55, max(1.0, peak - start)), 2)]

    has_audio = _has_audio_stream(video_path)

    # 2. Smart Jump-Cuts / 1.25x Acceleration of internal dead stretches (>2.5s)
    if enable_smart_pacing and subtitles_mode != "whisper":
        pacing_segs, paced_impacts, paced_duration = _compute_smart_pacing_plan(
            video_path, start, end, raw_impacts, has_audio
        )
    else:
        pacing_segs = [{"s": 0.0, "e": round(raw_duration, 2), "speed": 1.0}]
        paced_impacts = raw_impacts
        paced_duration = raw_duration
    used_smart_pacing = any(seg["speed"] > 1.01 for seg in pacing_segs)

    # 3. Peak Preview Flash (0.30s at second 0)
    peak_rel = max(0.3, min(paced_duration - 0.4, paced_impacts[0]))
    use_flash = bool(enable_preview_flash and paced_duration >= 4.0 and peak_rel >= 1.2)
    flash_dur = 0.30 if use_flash else 0.0
    final_duration = round(paced_duration + flash_dur, 2)
    shifted_impacts = [round(t + flash_dur, 2) for t in paced_impacts]

    slug = _safe_filename(f"{project_name}_top{rank}_{style}")
    out_file = out_dir / f"{slug}_{uuid.uuid4().hex[:5]}.mp4"

    show_hook = enable_hook and style != "clipe_limpo" and bool(hook_text)
    show_cta = enable_cta_comment and style != "clipe_limpo" and bool(cta_text)
    use_bar = bool(enable_progress_bar and style != "clipe_limpo")
    ass_file: Optional[Path] = None

    try:
        whisper_segments: list[dict[str, Any]] = []
        if subtitles_mode == "whisper":
            whisper_segments = _transcribe_whisper_segments(video_path, start, end)

        candidate_ass = TEMP_DIR / f"overlay_{uuid.uuid4().hex[:8]}.ass"
        ass_file = _build_autofit_ass(
            out_ass=candidate_ass,
            hook_text=hook_text,
            show_hook=show_hook,
            whisper_segments=whisper_segments,
            subtitle_style_id=subtitle_style,
            cta_comment=cta_text,
            show_cta=show_cta,
            total_duration=final_duration,
            time_offset=flash_dur,
        )

        shifted_dialogues = [
            {"start": s["start"] + flash_dur, "end": s["end"] + flash_dur, "text": s["text"]}
            for s in whisper_segments
        ]
        zoom_expr, shake_x, shake_y, impact_pulse = _build_smart_zoom_and_shake(
            impact_times=shifted_impacts,
            enable_impact_zoom=enable_impact_zoom,
            dialogue_segments=shifted_dialogues,
            subtitles_mode=subtitles_mode,
            clip_duration=final_duration,
        )

        # Smart Action Tracking (OpenCV Optical Flow center-of-mass X per 1s) for Tela Cheia Pro
        used_tracking = bool(style == "tela_cheia" and enable_action_tracking)
        track_cx_expr = (
            _compute_action_tracking_expr(video_path, start, end, time_offset=flash_dur)
            if used_tracking
            else "0.5"
        )

        filter_parts: list[str] = []
        v_cur = "0:v"
        a_cur = "0:a" if has_audio else ""

        # Apply internal 1.25x smart pacing if dead stretches (>2.5s) were detected
        if used_smart_pacing:
            n_seg = len(pacing_segs)
            v_splits = "".join(f"[vs{i}]" for i in range(n_seg))
            filter_parts.append(f"[0:v]split={n_seg}{v_splits}")
            if has_audio:
                a_splits = "".join(f"[as{i}]" for i in range(n_seg))
                filter_parts.append(f"[0:a]asplit={n_seg}{a_splits}")

            concat_in = []
            for i, seg in enumerate(pacing_segs):
                sp = seg["speed"]
                pts_expr = f"(PTS-STARTPTS)/{sp}" if sp > 1.01 else "PTS-STARTPTS"
                filter_parts.append(f"[vs{i}]trim=start={seg['s']}:end={seg['e']},setpts={pts_expr}[vp{i}]")
                concat_in.append(f"[vp{i}]")
                if has_audio:
                    atempo = f",atempo={sp}" if sp > 1.01 else ""
                    filter_parts.append(f"[as{i}]atrim=start={seg['s']}:end={seg['e']},asetpts=PTS-STARTPTS{atempo}[ap{i}]")
                    concat_in.append(f"[ap{i}]")

            if has_audio:
                filter_parts.append(f"{''.join(concat_in)}concat=n={n_seg}:v=1:a=1[vpaced][apaced]")
                v_cur, a_cur = "vpaced", "apaced"
            else:
                filter_parts.append(f"{''.join(concat_in)}concat=n={n_seg}:v=1:a=0[vpaced]")
                v_cur = "vpaced"

        # Apply 0.30s Peak Preview Flash prepended at t=0.00..0.30
        if use_flash:
            fl_s = round(max(0.0, peak_rel - 0.15), 2)
            fl_e = round(min(paced_duration, fl_s + 0.30), 2)
            if has_audio:
                filter_parts.append(
                    f"[{v_cur}]split=2[v_fl_src][v_main_src];"
                    f"[v_fl_src]trim=start={fl_s}:end={fl_e},setpts=PTS-STARTPTS,eq=contrast=1.15:brightness=0.05[v_fl];"
                    f"[v_main_src]setpts=PTS-STARTPTS[v_main];"
                    f"[{a_cur}]asplit=2[a_fl_src][a_main_src];"
                    f"[a_fl_src]atrim=start={fl_s}:end={fl_e},asetpts=PTS-STARTPTS[a_fl];"
                    f"[a_main_src]asetpts=PTS-STARTPTS[a_main];"
                    f"[v_fl][a_fl][v_main][a_main]concat=n=2:v=1:a=1[vin][ain]"
                )
                v_cur, a_cur = "vin", "ain"
            else:
                filter_parts.append(
                    f"[{v_cur}]split=2[v_fl_src][v_main_src];"
                    f"[v_fl_src]trim=start={fl_s}:end={fl_e},setpts=PTS-STARTPTS,eq=contrast=1.15:brightness=0.05[v_fl];"
                    f"[v_main_src]setpts=PTS-STARTPTS[v_main];"
                    f"[v_fl][v_main]concat=n=2:v=1:a=0[vin]"
                )
                v_cur = "vin"

        base_contrast = 1.07 if style == "clipe_limpo" else 1.05
        base_sat = 1.14 if style == "clipe_limpo" else 1.10
        if enable_impact_zoom and impact_pulse != "0":
            eq_filter = (
                f"eq=eval=frame:"
                f"contrast='{base_contrast}+0.11*({impact_pulse})':"
                f"saturation='{base_sat}+0.12*({impact_pulse})'"
            )
        else:
            eq_filter = f"eq=contrast={base_contrast}:saturation={base_sat}"

        if style == "tela_cheia":
            # Smart Action Tracking pans smoothly toward Optical Flow center-of-mass X + Impact Zoom + Micro-Shake
            crop_x = f"max(0,min(iw-ow,iw*{track_cx_expr}-ow/2+({shake_x})))"
            crop_y = f"max(0,min(ih-oh,(ih-oh)/2+({shake_y})))"
            filter_parts.append(
                f"[{v_cur}]scale=-2:2400:flags=lanczos,"
                f"crop=w='min(iw,trunc(1350/({zoom_expr})/2)*2)':"
                f"h='min(ih,trunc(2400/({zoom_expr})/2)*2)':"
                f"x='{crop_x}':y='{crop_y}',"
                f"scale=1080:1920:flags=lanczos,{eq_filter}[base]"
            )
        else:
            crop_x = f"max(0,min(iw-ow,(iw-ow)/2+({shake_x})))"
            crop_y = f"max(0,min(ih-oh,(ih-oh)/2+({shake_y})))"
            filter_parts.append(
                f"[{v_cur}]split=2[bg_src][fg_src];"
                f"[bg_src]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,boxblur=26:4,eq=brightness=-0.10:saturation=1.15[bg];"
                f"[fg_src]scale=1344:-2:flags=lanczos,"
                f"crop=w='min(iw,trunc(iw/({zoom_expr})/2)*2)':"
                f"h='min(ih,trunc(ih/({zoom_expr})/2)*2)':"
                f"x='{crop_x}':y='{crop_y}',"
                f"scale=1080:-2:flags=lanczos,{eq_filter}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )

        v_stage = "base"

        # 4. 3px Neon Retention Progress Bar in Safe Zone (y=122, x=60..1020)
        extra_inputs: list[str] = []
        if use_bar:
            bar_png = _ensure_neon_progress_bar_png()
            extra_inputs = ["-loop", "1", "-t", f"{final_duration + 0.5:.2f}", "-i", str(bar_png)]
            filter_parts.append(
                f"[{v_stage}]drawbox=x=60:y=122:w=960:h=3:color=0x1E2436@0.75:t=fill[vtrack];"
                f"[1:v]format=rgba,pad=1920:3:960:0:color=black@0,"
                f"crop=960:3:x='max(0,min(960,960*(1-t/{max(1.0, final_duration):.2f})))':y=0[bar_anim];"
                f"[vtrack][bar_anim]overlay=60:122:shortest=1[vbar]"
            )
            v_stage = "vbar"

        if ass_file and ass_file.exists():
            ass_escaped = _escape_ffmpeg_path(ass_file)
            filter_parts.append(f"[{v_stage}]ass='{ass_escaped}',setsar=1[vout]")
        else:
            filter_parts.append(f"[{v_stage}]setsar=1[vout]")

        # 5. Professional Audio (-14 LUFS + Limiter + Ducking + Impact SFX)
        audio_chain, final_a_label = _build_pro_audio_chain(
            base_audio_label=a_cur,
            impact_times=shifted_impacts,
            enable_impact_sfx=enable_impact_sfx,
            enable_audio_norm=enable_audio_norm,
            has_audio=has_audio,
        )
        if audio_chain:
            filter_parts.append(audio_chain)

        filter_complex = ";".join(filter_parts)
        enc_args = _detect_video_encoder(ffmpeg, settings.get("hardware_encoder", "auto"))

        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-ss", f"{start:.2f}",
            "-t", f"{raw_duration:.2f}",
            "-i", video_path,
            *extra_inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
        ]
        if has_audio:
            if final_a_label == "0:a":
                cmd += ["-map", "0:a?"]
            else:
                cmd += ["-map", f"[{final_a_label}]"]
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]

        cmd += [
            *enc_args,
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-movflags", "+faststart",
            str(out_file),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if res.returncode != 0 or not out_file.exists():
            raise XRayError(
                f"Falha ao renderizar o clipe #{rank} no estilo '{style}'. Verifique detalhes tecnicos do FFmpeg.",
                category="RENDER",
                technical_detail=(res.stderr or "Erro desconhecido no FFmpeg").strip()[-1200:],
                status_code=500,
                context={"moment_id": moment.get("id"), "style": style, "output": str(out_file)},
            )

        size_mb = round(out_file.stat().st_size / (1024 * 1024), 2)
        return {
            "file_path": str(out_file),
            "file_name": out_file.name,
            "media_url": f"/media/saida/{out_file.name}",
            "size_mb": size_mb,
            "duration": final_duration,
            "style": style,
            "subtitles_mode": subtitles_mode,
            "subtitle_style": subtitle_style,
            "retention": {
                "preview_flash": use_flash,
                "infinite_loop": enable_infinite_loop,
                "cta_comment": show_cta,
                "impact_sfx": bool(enable_impact_sfx and has_audio),
                "smart_pacing": used_smart_pacing,
                "progress_bar": use_bar,
                "audio_norm_lufs": bool(enable_audio_norm and has_audio),
                "action_tracking": used_tracking,
            },
            "mode": "viral_9_16",
        }
    finally:
        if ass_file and ass_file.exists():
            try:
                ass_file.unlink()
            except OSError:
                pass


def render_long_gameplay(
    video_path: str,
    keep_segments: list[dict[str, float]],
    best_thumb_sec: float,
    project_name: str = "gameplay",
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> dict[str, Any]:
    ffmpeg, _ = get_ffmpeg_bins()
    if not ffmpeg:
        raise XRayError("FFmpeg nao encontrado para exportar a gameplay longa.", category="FFMPEG", status_code=500)

    settings = load_settings()
    out_dir = get_output_dir()
    uid = uuid.uuid4().hex[:6]
    slug = _safe_filename(f"{project_name}_YouTube_1080p")
    out_file = out_dir / f"{slug}_{uid}.mp4"
    thumb_out = out_dir / f"{slug}_{uid}_THUMBNAIL.jpg"

    enc_args = _detect_video_encoder(ffmpeg, settings.get("hardware_encoder", "auto"))

    if progress_cb:
        progress_cb(15, "Capturando melhor frame para Thumbnail 1080p...")

    thumb_cmd = [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{max(0.0, best_thumb_sec):.2f}",
        "-i", video_path,
        "-frames:v", "1",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,eq=contrast=1.08:saturation=1.18",
        "-q:v", "2",
        str(thumb_out),
    ]
    subprocess.run(thumb_cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)

    if progress_cb:
        progress_cb(30, f"Unindo {len(keep_segments)} blocos de acao e historia em 1920x1080...")

    part_files: list[Path] = []
    concat_list = TEMP_DIR / f"concat_{uid}.txt"
    try:
        total_segs = max(1, len(keep_segments))
        for idx, seg in enumerate(keep_segments):
            seg_start = float(seg["start"])
            seg_dur = max(0.5, float(seg["end"]) - seg_start)
            part = TEMP_DIR / f"part_{uid}_{idx:03d}.mp4"
            cmd_part = [
                ffmpeg, "-y", "-v", "error",
                "-ss", f"{seg_start:.2f}",
                "-t", f"{seg_dur:.2f}",
                "-i", video_path,
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1",
                *enc_args,
                "-pix_fmt", "yuv420p",
                "-r", "30",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                str(part),
            ]
            res = subprocess.run(cmd_part, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
            if res.returncode != 0 or not part.exists():
                raise XRayError(
                    f"Falha ao processar o segmento #{idx + 1} da gameplay longa 16:9.",
                    category="RENDER",
                    technical_detail=(res.stderr or "")[-1000:],
                    status_code=500,
                )
            part_files.append(part)
            if progress_cb:
                pct = 30 + int(((idx + 1) / total_segs) * 58)
                progress_cb(min(88, pct), f"Renderizando bloco {idx + 1}/{total_segs} em 1080p...")

        if len(part_files) == 1:
            part_files[0].replace(out_file)
            part_files.clear()
        else:
            lines = [f"file '{str(p.resolve()).replace(chr(39), '')}'" for p in part_files]
            concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
            if progress_cb:
                progress_cb(92, "Finalizando arquivo MP4 16:9 com faststart...")
            cmd_concat = [
                ffmpeg, "-y", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                "-movflags", "+faststart",
                str(out_file),
            ]
            res = subprocess.run(cmd_concat, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
            if res.returncode != 0 or not out_file.exists():
                raise XRayError(
                    "Falha ao concatenar os blocos finais da gameplay 16:9.",
                    category="RENDER",
                    technical_detail=(res.stderr or "")[-1000:],
                    status_code=500,
                )

        if progress_cb:
            progress_cb(100, "Gameplay Longa 16:9 pronta para o YouTube!")

        return {
            "file_path": str(out_file),
            "file_name": out_file.name,
            "media_url": f"/media/saida/{out_file.name}",
            "size_mb": round(out_file.stat().st_size / (1024 * 1024), 2),
            "thumb_path": str(thumb_out) if thumb_out.exists() else "",
            "thumb_url": f"/media/saida/{thumb_out.name}" if thumb_out.exists() else "",
            "mode": "long_16_9",
        }
    finally:
        for p in part_files:
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        if concat_list.exists():
            try:
                concat_list.unlink()
            except OSError:
                pass
