r"""FFmpeg video editor & renderer for ClipRush Studio Pro (Scale Production Engine):
- Mode 1: Viral 9:16 Cuts (Viral Blur, Tela Cheia Pro, Clipe Limpo)
  * Whisper Auto Subtitles ON by default: extracts exact post-edit audio (including 0.3s Preview Flash,
    1.25x Jump-Cuts & 0.75x Slow-Mo), transcribes via faster-whisper (auto PT/EN + vad_filter=True),
    burns styled .ass via FFmpeg subtitles='D\:/...' (Arial Black 54px, #FFD700/White, Outline=4,
    max 2 lines x 22 chars, Safe Zone MarginV=290)
  * REAL Visible Impact Zoom (1.00x -> 1.24x -> 1.00x in 0.85s) via scale(eval=frame) + crop directly
    on the main game layer BEFORE Viral Blur / Tela Cheia composition + Micro-Shake + Impact Contrast
  * Dramatic Slow-Motion (0.75x) during the exact 1.2s climax / final hit / player death synced with Zoom & SFX
  * Readable Top Hook for 3.8s (t=0.2s..4.0s) via drawtext (fontsize=48, box=1:boxcolor=0x0B0D12@0.82:boxborderw=20)
  * Optional 0.3s Peak Preview Flash at second 0 + Final 2s CTA Comment Badge (MarginV=375)
  * 3px Neon Progress Bar + -14 LUFS Audio Normalization + Limiter + SFX Ducking + Smart Action Tracking
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
from PIL import Image, ImageDraw, ImageFont

from backend.config import TEMP_DIR, get_ffmpeg_bins, get_output_dir, load_settings
from backend.errors import XRayError
from backend.storage import load_overlay_config

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
FFMPEG_FLAGS = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
_WHISPER_MODEL_CACHE: dict[str, Any] = {}

SUBTITLE_STYLES: dict[str, dict[str, Any]] = {
    "amarelo_viral": {
        "id": "amarelo_viral",
        "name": "Amarelo Viral com Borda Preta",
        "primary": "&H0000D7FF",      # #FFD700 in ASS BGR
        "outline_col": "&H00000000",
        "back_col": "&H90000000",
        "outline": 4,
        "shadow": 2,
        "fontsize_sub": 54,
        "hook_color": "0xFFD700",
        "keyword_highlight": False,
    },
    "branco_clean": {
        "id": "branco_clean",
        "name": "Branco Clean Moderno",
        "primary": "&H00FFFFFF",
        "outline_col": "&H00000000",
        "back_col": "&H80000000",
        "outline": 4,
        "shadow": 2,
        "fontsize_sub": 54,
        "hook_color": "white",
        "keyword_highlight": False,
    },
    "destaque_keyword": {
        "id": "destaque_keyword",
        "name": "Destaque Palavra-Chave",
        "primary": "&H00FFFFFF",
        "outline_col": "&H00000000",
        "back_col": "&H90000000",
        "outline": 4,
        "shadow": 2,
        "fontsize_sub": 54,
        "hook_color": "white",
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


def escape_ffmpeg_windows_path(p: Path) -> str:
    """Bulletproof Windows path escaping for FFmpeg subtitles=/drawtext filters:
    D:\\ClipRush_Pro_V2\\... -> D\\:/ClipRush_Pro_V2/..."""
    s = str(p.resolve()).replace("\\", "/")
    s = s.replace(":", r"\:")
    s = s.replace("'", r"\'")
    s = s.replace("[", r"\[").replace("]", r"\]")
    return s


def _find_windows_bold_font() -> str:
    candidates = [
        Path(r"C:\Windows\Fonts\ariblk.ttf"),
        Path(r"C:\Windows\Fonts\impact.ttf"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
    ]
    for c in candidates:
        if c.exists():
            return escape_ffmpeg_windows_path(c)
    return ""


def _ensure_neon_progress_bar_png() -> Path:
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


def _smart_wrap_lines(text: str, max_chars: int = 22, max_lines: int = 2) -> list[str]:
    clean = re.sub(r"\s+", " ", _ass_escape(text)).strip()
    if not clean:
        return []
    lines = textwrap.wrap(clean, width=max_chars, break_long_words=True, break_on_hyphens=True)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1 :])]
        if len(lines[-1]) > max_chars + 3:
            lines[-1] = lines[-1][: max_chars].rstrip() + "..."
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
                out_words.append(r"{\c&H0000D7FF&\fscx108\fscy108}" + w + r"{\r}")
            else:
                out_words.append(w)
        styled_lines.append(" ".join(out_words))
    return r"\N".join(styled_lines)


def _format_ass_text(text: str, max_chars: int = 22, max_lines: int = 2, highlight_keywords: bool = False) -> str:
    lines = _smart_wrap_lines(text.upper(), max_chars=max_chars, max_lines=max_lines)
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


def _transcribe_wav_with_whisper(wav_path: Path) -> list[dict[str, Any]]:
    """Run faster-whisper on an exact pre-timed WAV file (PT/EN auto-detect + vad_filter=True)."""
    if not wav_path.exists() or wav_path.stat().st_size < 1000:
        return []
    settings = load_settings()
    try:
        from faster_whisper import WhisperModel

        model_name = settings.get("whisper_model", "base")
        if model_name not in _WHISPER_MODEL_CACHE:
            _WHISPER_MODEL_CACHE[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
        model = _WHISPER_MODEL_CACHE[model_name]
        lang_cfg = (settings.get("whisper_language") or "auto").strip().lower()
        lang_arg = None if lang_cfg in ("auto", "") else lang_cfg

        segments, _ = model.transcribe(
            str(wav_path),
            language=lang_arg,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 280, "threshold": 0.32},
            word_timestamps=False,
        )
        out: list[dict[str, Any]] = []
        for seg in segments:
            txt = (seg.text or "").strip()
            if not txt:
                continue
            # Split long sentences into tight 2-line punchy viral captions (<= 44 chars per caption chunk)
            words = txt.split()
            if len(txt) <= 44 or len(words) <= 5:
                out.append({"start": float(seg.start), "end": float(seg.end), "text": txt})
            else:
                mid = len(words) // 2
                dur = max(0.4, float(seg.end) - float(seg.start))
                split_t = float(seg.start) + dur * (mid / len(words))
                out.append({"start": float(seg.start), "end": split_t, "text": " ".join(words[:mid])})
                out.append({"start": split_t, "end": float(seg.end), "text": " ".join(words[mid:])})
        return out
    except Exception:
        return []


def _hex_to_ass_bgr(hex_color: str, default: str = "&H0000D7FF") -> str:
    """Convert #RRGGBB into ASS &H00BBGGRR color string."""
    s = (hex_color or "").strip().lstrip("#")
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        return default
    rr, gg, bb = s[0:2].upper(), s[2:4].upper(), s[4:6].upper()
    return f"&H00{bb}{gg}{rr}"


def build_anti_factory_variation(
    clip_index: int,
    base_style: str,
    base_overlay: dict[str, Any],
    category: str = "combate_intenso",
) -> tuple[str, dict[str, Any]]:
    """P2 Anti-Factory Variation: alternates framing layout, hook color, animation, and Safe Zone Y
    across consecutive clips so batches never look repetitive or algorithmic."""
    idx = max(0, int(clip_index))
    ov = dict(base_overlay)
    if idx == 0:
        return base_style, ov

    layout_cycle = ["fit", "big", "fill"]
    chosen_style = base_style if base_style == "clipe_limpo" else layout_cycle[idx % len(layout_cycle)]
    color_cycle = ["#FFD700", "#FFFFFF", "#38BDF8", "#FDE047"]
    anim_cycle = ["fade", "pop", "slide_down"]
    safe_y_cycle = [230, 290, 345, 205]

    ov["color"] = color_cycle[idx % len(color_cycle)]
    ov["animation"] = anim_cycle[idx % len(anim_cycle)]
    ov["y"] = safe_y_cycle[idx % len(safe_y_cycle)]
    return chosen_style, ov


def resolve_framing_layout(style: str, category: str = "combate_intenso") -> str:
    """Resolve framing layout into one of: 'fit' (blur), 'big' (~4:3 HUD-safe), 'fill' (full 9:16)."""
    s = (style or "fit").strip().lower()
    if s in ("fill", "tela_cheia"):
        return "fill"
    if s == "big":
        return "big"
    if s in ("fit", "viral_blur", "clipe_limpo"):
        return "fit"
    if s == "auto":
        if category == "acrobacia_cinema":
            return "fill"
        if category == "dialogo_historia":
            return "fit"
        return "big"
    return "fit"


def generate_short_cover_thumbnail(
    mp4_path: Path,
    title_text: str,
    out_jpg: Path,
    seek_sec: float = 2.4,
) -> Optional[str]:
    """Generate an individual 1080x1920 Shorts Cover JPEG with bold wrapped title overlay."""
    ffmpeg, _ = get_ffmpeg_bins()
    if not ffmpeg or not mp4_path.exists():
        return None
    raw_frame = TEMP_DIR / f"cov_raw_{uuid.uuid4().hex[:8]}.jpg"
    try:
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-ss", f"{max(0.2, seek_sec):.2f}",
            "-i", str(mp4_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(raw_frame),
        ]
        res = subprocess.run(cmd, capture_output=True, creationflags=FFMPEG_FLAGS)
        if res.returncode != 0 or not raw_frame.exists():
            return None

        img = Image.open(raw_frame).convert("RGBA")
        if img.size != (1080, 1920):
            img = img.resize((1080, 1920), Image.Resampling.LANCZOS)

        # Subtle dark top & bottom vignette bands for unmistakable cover readability
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)
        for y in range(0, 520):
            alpha = int(170 * (1.0 - y / 520.0))
            draw_ov.line([(0, y), (1080, y)], fill=(8, 10, 18, alpha))
        for y in range(1480, 1920):
            alpha = int(190 * ((y - 1480) / 440.0))
            draw_ov.line([(0, y), (1080, y)], fill=(8, 10, 18, alpha))
        img = Image.alpha_composite(img, overlay)

        draw = ImageDraw.Draw(img)
        font_path = None
        for cand in (
            r"C:\Windows\Fonts\ariblk.ttf",
            r"C:\Windows\Fonts\impact.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ):
            if Path(cand).exists():
                font_path = cand
                break
        font = ImageFont.truetype(font_path, 62) if font_path else ImageFont.load_default()
        lines = _smart_wrap_lines((title_text or "CLIPE EPICO").upper(), max_chars=22, max_lines=3)
        y_cursor = 230
        for ln in lines:
            bbox = draw.textbbox((0, 0), ln, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = max(60, (1080 - tw) // 2)
            # Thick black stroke + bright gold fill
            for dx in (-4, -2, 0, 2, 4):
                for dy in (-4, -2, 0, 2, 4):
                    if dx or dy:
                        draw.text((tx + dx, y_cursor + dy), ln, font=font, fill=(0, 0, 0, 240))
            draw.text((tx, y_cursor), ln, font=font, fill=(255, 215, 0, 255))
            y_cursor += th + 20

        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(out_jpg, format="JPEG", quality=90)
        return str(out_jpg)
    except Exception:
        return None
    finally:
        if raw_frame.exists():
            try:
                raw_frame.unlink()
            except OSError:
                pass


def build_viral_ass_file(
    out_ass: Path,
    whisper_segments: list[dict[str, Any]],
    subtitle_style_id: str = "amarelo_viral",
    cta_comment: str = "",
    show_cta: bool = False,
    total_duration: float = 25.0,
    hook_overlay: Optional[dict[str, Any]] = None,
    show_hook: bool = False,
) -> Optional[Path]:
    """Generate styled 1080x1920 .ass subtitles & pixel-exact interactive Hook Title overlay:
    - HookTitle: PlayResX 1080 x PlayResY 1920, exact \\an5\\pos(x,y) matching overlay.json preview 100%!
    - ViralSub: Whisper dialogue captions (Safe Zone MarginV=290)
    - CTABadge: Final 2s Comment CTA Badge (Safe Zone MarginV=375)
    """
    has_hook = bool(show_hook and hook_overlay and str(hook_overlay.get("text") or "").strip())
    if not whisper_segments and not (show_cta and cta_comment.strip()) and not has_hook:
        return None

    st = SUBTITLE_STYLES.get(subtitle_style_id, SUBTITLE_STYLES["amarelo_viral"])
    kw_mode = bool(st["keyword_highlight"])
    events: list[str] = []

    # Custom Hook Title Style (from overlay.json)
    ov = hook_overlay or {}
    ov_font = str(ov.get("font_family") or "Arial Black").replace(",", "")
    ov_size = max(28, min(90, int(round(float(ov.get("font_size", 52))))))
    ov_color = _hex_to_ass_bgr(str(ov.get("color") or "#FFD700"), default="&H0000D7FF")
    ov_outline_col = _hex_to_ass_bgr(str(ov.get("outline_color") or "#000000"), default="&H00000000")
    ov_outline_w = max(0, min(10, int(round(float(ov.get("outline_width", 4))))))
    ov_x = max(60, min(1020, int(round(float(ov.get("x", 540))))))
    ov_y = max(60, min(1860, int(round(float(ov.get("y", 250))))))
    ov_start = round(max(0.0, float(ov.get("start_sec", 0.15))), 2)
    ov_dur = round(max(1.0, min(5.0, float(ov.get("duration_sec", 2.3)))), 2)
    ov_end = round(min(total_duration - 0.05, ov_start + ov_dur), 2)
    ov_anim = str(ov.get("animation") or "fade")

    if has_hook and ov_end > ov_start + 0.2:
        hook_wrapped = r"\N".join(_smart_wrap_lines(str(ov.get("text") or "").upper(), max_chars=24, max_lines=2))
        if hook_wrapped:
            if ov_anim == "slide_down":
                pos_tag = f"\\an5\\move({ov_x},{max(20, ov_y - 48)},{ov_x},{ov_y},0,220)\\fad(120,160)"
            elif ov_anim == "pop":
                pos_tag = f"\\an5\\pos({ov_x},{ov_y})\\fscx85\\fscy85\\t(0,160,\\fscx108\\fscy108)\\t(160,300,\\fscx100\\fscy100)\\fad(80,150)"
            elif ov_anim == "none":
                pos_tag = f"\\an5\\pos({ov_x},{ov_y})"
            else:
                pos_tag = f"\\an5\\pos({ov_x},{ov_y})\\fad(180,180)"
            events.append(
                f"Dialogue: 3,{_ass_time(ov_start)},{_ass_time(ov_end)},HookTitle,,0,0,0,,{{{pos_tag}}}{hook_wrapped}"
            )

    for seg in whisper_segments:
        formatted = _format_ass_text(seg["text"], max_chars=22, max_lines=2, highlight_keywords=kw_mode)
        if not formatted:
            continue
        s_ts = _ass_time(float(seg["start"]))
        e_ts = _ass_time(float(seg["end"]))
        events.append(f"Dialogue: 1,{s_ts},{e_ts},ViralSub,,0,0,0,,{formatted}")

    if show_cta and cta_comment.strip() and total_duration >= 3.5:
        cta_start = max(0.5, total_duration - 2.15)
        cta_end = max(cta_start + 1.0, total_duration - 0.05)
        cta_wrapped = r"\N".join(_smart_wrap_lines(cta_comment.strip(), max_chars=26, max_lines=2))
        if cta_wrapped:
            events.append(
                f"Dialogue: 2,{_ass_time(cta_start)},{_ass_time(cta_end)},CTABadge,,0,0,0,,"
                f"{{\\fad(160,120)}}COMENTA AI:  {cta_wrapped.upper()}"
            )

    if not events:
        return None

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
        f"Style: HookTitle,{ov_font},{ov_size},{ov_color},&H00FFFFFF,{ov_outline_col},&HAA0B0D12,"
        f"-1,0,0,0,100,100,0.5,0,1,{ov_outline_w},2,5,120,120,150,1\n"
        f"Style: ViralSub,Arial Black,{st['fontsize_sub']},{st['primary']},&H0000FFFF,{st['outline_col']},{st['back_col']},"
        f"-1,0,0,0,100,100,0.5,0,1,{st['outline']},{st['shadow']},2,60,60,290,1\n"
        "Style: CTABadge,Arial,40,&H00F8EE22,&H00FFFFFF,&HAA12151E,&HAA12151E,"
        "-1,0,0,0,100,100,1,0,3,14,0,2,60,60,375,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events)
        + "\n"
    )
    out_ass.write_text(ass_content, encoding="utf-8")
    return out_ass


def _build_time_segments(
    video_path: str,
    start: float,
    end: float,
    impact_times: list[float],
    climax_rel: float,
    enable_smart_pacing: bool,
    enable_slowmo: bool,
    has_audio: bool,
) -> tuple[list[dict[str, float]], list[float], float, bool, bool]:
    """Build contiguous clip speed map combining:
    - Normal gameplay: 1.0x
    - Internal dead stretches (>2.5s): 1.25x (if enable_smart_pacing)
    - Dramatic Slow-Motion (1.2s climax / final hit / death): 0.75x (if enable_slowmo)
    Returns (segments, remapped_impacts, new_duration, used_smart_pacing, used_slowmo).
    """
    duration = max(1.0, end - start)
    step = 0.5
    bins = max(1, int(math.ceil(duration / step)))
    speeds = np.ones(bins, dtype=np.float32)

    # 1. Detect internal dead stretches (>2.5s) for 1.25x acceleration
    if enable_smart_pacing and duration >= 9.0:
        motion_arr = np.zeros(bins, dtype=np.float32)
        audio_arr = np.zeros(bins, dtype=np.float32)

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
        dead = (m_norm < 0.35) & (a_norm < 0.32)
        dead[: min(bins, 4)] = False
        dead[max(0, bins - 5) :] = False
        for imp in impact_times:
            ib = int(imp / step)
            dead[max(0, ib - 3) : min(bins, ib + 4)] = False

        run = 0
        for i in range(bins):
            if dead[i]:
                run += 1
            else:
                if run >= 5:
                    speeds[i - run : i] = 1.25
                run = 0
        if run >= 5:
            speeds[bins - run : bins] = 1.25

    # 2. Dramatic Slow-Motion (0.75x) on exact 1.2s climax / final hit / death window
    slowmo_s = -1.0
    slowmo_e = -1.0
    if enable_slowmo and duration >= 4.0:
        slowmo_s = round(max(1.0, min(duration - 1.5, climax_rel - 0.30)), 2)
        slowmo_e = round(min(duration - 0.2, slowmo_s + 1.20), 2)
        if slowmo_e - slowmo_s < 0.8:
            slowmo_s = -1.0

    # Build clean boundary points from speed transitions + slowmo window
    raw_segs: list[dict[str, float]] = []
    cur_sp = float(speeds[0])
    seg_s = 0.0
    for i in range(1, bins):
        sp = float(speeds[i])
        if abs(sp - cur_sp) > 0.01:
            seg_e = round(min(duration, i * step), 2)
            if seg_e - seg_s >= 0.35:
                raw_segs.append({"s": round(seg_s, 2), "e": seg_e, "speed": cur_sp})
                seg_s = seg_e
                cur_sp = sp
    if duration - seg_s >= 0.2:
        raw_segs.append({"s": round(seg_s, 2), "e": round(duration, 2), "speed": cur_sp})

    # Overlay the exact 1.2s slow-motion (0.75x) interval cleanly onto raw_segs
    final_segs: list[dict[str, float]] = []
    if slowmo_s >= 0:
        for seg in raw_segs:
            s, e, sp = seg["s"], seg["e"], seg["speed"]
            if e <= slowmo_s or s >= slowmo_e:
                final_segs.append(seg)
            else:
                if s < slowmo_s and (slowmo_s - s) >= 0.25:
                    final_segs.append({"s": s, "e": slowmo_s, "speed": sp})
                ov_s = max(s, slowmo_s)
                ov_e = min(e, slowmo_e)
                if ov_e - ov_s >= 0.2:
                    if final_segs and abs(final_segs[-1]["speed"] - 0.75) < 0.01:
                        final_segs[-1]["e"] = ov_e
                    else:
                        final_segs.append({"s": ov_s, "e": ov_e, "speed": 0.75})
                if e > slowmo_e and (e - slowmo_e) >= 0.25:
                    final_segs.append({"s": slowmo_e, "e": e, "speed": sp})
    else:
        final_segs = raw_segs

    # Safety cap on segment count (keep filtergraph ultra-fast)
    if len(final_segs) > 9:
        if slowmo_s >= 0:
            final_segs = []
            if slowmo_s >= 0.25:
                final_segs.append({"s": 0.0, "e": slowmo_s, "speed": 1.0})
            final_segs.append({"s": slowmo_s, "e": slowmo_e, "speed": 0.75})
            if duration - slowmo_e >= 0.25:
                final_segs.append({"s": slowmo_e, "e": round(duration, 2), "speed": 1.0})
        else:
            final_segs = [{"s": 0.0, "e": round(duration, 2), "speed": 1.0}]

    if not final_segs:
        final_segs = [{"s": 0.0, "e": round(duration, 2), "speed": 1.0}]

    def _remap(orig_t: float) -> float:
        acc = 0.0
        for seg in final_segs:
            if orig_t <= seg["e"]:
                return acc + max(0.0, orig_t - seg["s"]) / seg["speed"]
            acc += (seg["e"] - seg["s"]) / seg["speed"]
        return acc

    new_dur = round(sum((seg["e"] - seg["s"]) / seg["speed"] for seg in final_segs), 2)
    new_impacts = [round(_remap(t), 2) for t in impact_times]
    used_pacing = any(seg["speed"] > 1.05 for seg in final_segs)
    used_slowmo = any(seg["speed"] < 0.90 for seg in final_segs)
    return final_segs, new_impacts, max(2.0, new_dur), used_pacing, used_slowmo


def _compute_action_tracking_expr(
    video_path: str,
    start: float,
    end: float,
    time_offset: float = 0.0,
) -> str:
    duration = max(1.0, end - start)
    n_windows = max(1, min(14, int(math.ceil(duration))))
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return "0.5"

    centers: list[float] = []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
        stride = max(1, int(round(fps * 0.5)))
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

    smoothed = [max(0.24, min(0.76, centers[0]))]
    for c in centers[1:]:
        clamped = max(0.24, min(0.76, c))
        smoothed.append(round(smoothed[-1] * 0.55 + clamped * 0.45, 3))
    smoothed[0] = round(smoothed[0], 3)

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


def filter_and_merge_zoom_impacts(
    impact_times: list[float],
    min_interval_sec: float = 6.0,
    max_zooms: int = 3,
    burst_merge_sec: float = 2.5,
) -> list[float]:
    """Strict P0 Zoom Frequency Guard:
    1. Merges rapid nearby gunfire/bursts (< 2.5s apart) into a single zoom centroid.
    2. Enforces minimum 6.0s interval between consecutive zooms.
    3. Hard caps at maximum 3 zooms per clip.
    """
    valid = sorted(float(t) for t in (impact_times or []) if float(t) >= 0.4)
    if not valid:
        return []

    # Step 1: Cluster & merge rapid nearby bursts into 1 unified impact per burst
    clusters: list[list[float]] = [[valid[0]]]
    for t in valid[1:]:
        if (t - clusters[-1][-1]) <= burst_merge_sec:
            clusters[-1].append(t)
        else:
            clusters.append([t])
    merged = [round(sum(c) / len(c), 2) for c in clusters]

    # Step 2: Enforce minimum interval (default 6.0s) and hard cap (default 3 zooms)
    eff_min_gap = max(6.0, float(min_interval_sec))
    eff_max = max(1, min(3, int(max_zooms)))
    selected: list[float] = []
    for t in merged:
        if not selected or (t - selected[-1]) >= eff_min_gap:
            selected.append(round(t, 2))
            if len(selected) >= eff_max:
                break
    return selected


def _build_impact_zoom_124_and_shake(
    impact_times: list[float],
    enable_impact_zoom: bool,
    dialogue_segments: list[dict[str, Any]],
    subtitles_mode: str,
    clip_duration: float,
    zoom_intensity: float = 1.14,
    min_interval_sec: float = 6.0,
    max_zooms: int = 3,
) -> tuple[str, str, str, str, list[float]]:
    """Precision Smooth Ease-In/Ease-Out Impact Zoom (default 1.12x-1.15x, max 3 zooms, >=6s interval).
    Zero black borders & preserves top/bottom HUD by keeping micro-shake strictly inside zoom margin."""
    zoom_terms: list[str] = []
    shake_x_terms: list[str] = []
    shake_y_terms: list[str] = []
    impact_gate_terms: list[str] = []

    zoom_delta = round(max(0.06, min(0.25, float(zoom_intensity) - 1.0)), 3)
    applied_zooms: list[float] = []

    if enable_impact_zoom and impact_times:
        applied_zooms = filter_and_merge_zoom_impacts(
            impact_times,
            min_interval_sec=min_interval_sec,
            max_zooms=max_zooms,
            burst_merge_sec=2.5,
        )
        # Keep shake strictly well inside the zoom overscan margin so black borders NEVER appear
        max_sx = max(2, min(8, int(round(zoom_delta * 54))))
        max_sy = max(1, min(5, int(round(zoom_delta * 32))))
        for imp in applied_zooms:
            t0 = max(0.20, round(float(imp) - 0.30, 2))
            t1 = round(t0 + 0.85, 2)
            # Raised-cosine ease-in / ease-out curve (smooth 0 -> 1 -> 0, zero sudden jump)
            pulse = f"(0.5*(1-cos(2*PI*(t-{t0})/0.85)))"
            zoom_terms.append(f"if(between(t,{t0},{t1}),{zoom_delta}*{pulse},0)")
            shake_x_terms.append(f"if(between(t,{t0},{t1}),{max_sx}*{pulse}*sin(52*(t-{t0})),0)")
            shake_y_terms.append(f"if(between(t,{t0},{t1}),{max_sy}*{pulse}*cos(42*(t-{t0})),0)")
            impact_gate_terms.append(f"if(between(t,{t0},{t1}),{pulse},0)")

    zoom_expr = "1+" + "+".join(zoom_terms) if zoom_terms else "1"
    shake_x = "+".join(shake_x_terms) if shake_x_terms else "0"
    shake_y = "+".join(shake_y_terms) if shake_y_terms else "0"
    impact_pulse_expr = "+".join(impact_gate_terms) if impact_gate_terms else "0"
    return zoom_expr, shake_x, shake_y, impact_pulse_expr, applied_zooms


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
    if not has_audio:
        return "", ""

    parts: list[str] = []
    cur = base_audio_label

    if enable_audio_norm:
        parts.append(f"[{cur}]loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.95:attack=5:release=50[anorm]")
        cur = "anorm"

    if enable_impact_sfx and impact_times:
        peaks = [max(0.2, float(t)) for t in impact_times[:3]]
        duck_terms = [f"between(t,{round(t, 2)},{round(t + 0.42, 2)})" for t in peaks]
        duck_cond = "+".join(duck_terms)
        parts.append(f"[{cur}]volume='if(gt({duck_cond},0),0.76,1.0)':eval=frame[aduck]")
        cur = "aduck"

        mix_inputs = [f"[{cur}]"]
        for idx, t_sec in enumerate(peaks):
            delay_ms = max(10, int(round(t_sec * 1000)))
            lbl = f"sfx{idx}"
            parts.append(
                f"aevalsrc=exprs='0.28*exp(-6.5*t)*sin(2*PI*(95-55*t)*t)+0.07*exp(-10*t)*sin(2*PI*220*t*t)':"
                f"s=48000:d=0.42:c=stereo,"
                f"lowpass=f=320,afade=t=in:st=0:d=0.02,afade=t=out:st=0.26:d=0.16,"
                f"adelay={delay_ms}|{delay_ms}[{lbl}]"
            )
            mix_inputs.append(f"[{lbl}]")

        parts.append(
            f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.96:attack=4:release=40[aout]"
        )
        cur = "aout"

    return ";".join(parts), cur


def _extract_timed_audio_for_whisper(
    ffmpeg: str,
    video_path: str,
    start: float,
    raw_duration: float,
    time_segs: list[dict[str, float]],
    use_flash: bool,
    fl_s: float,
    fl_e: float,
    micro_s: float,
    micro_e: float,
    out_wav: Path,
) -> bool:
    """Fast audio-only FFmpeg render replicating Real Peak Hook (1.5-2.0s) + 0.10s Micro-Flash
    + Jump-Cuts 1.25x + Slow-Mo 0.75x so Whisper produces millisecond-accurate timestamps."""
    parts: list[str] = []
    a_cur = "0:a"
    if len(time_segs) > 1 or abs(time_segs[0]["speed"] - 1.0) > 0.01:
        n_seg = len(time_segs)
        splits = "".join(f"[as{i}]" for i in range(n_seg))
        parts.append(f"[0:a]asplit={n_seg}{splits}")
        cin = []
        for i, seg in enumerate(time_segs):
            sp = seg["speed"]
            atempo = f",atempo={sp}" if abs(sp - 1.0) > 0.01 else ""
            parts.append(f"[as{i}]atrim=start={seg['s']}:end={seg['e']},asetpts=PTS-STARTPTS{atempo}[ap{i}]")
            cin.append(f"[ap{i}]")
        parts.append(f"{''.join(cin)}concat=n={n_seg}:v=0:a=1[apaced]")
        a_cur = "apaced"

    if use_flash:
        parts.append(
            f"[{a_cur}]asplit=3[afl_s][amicro_s][am_s];"
            f"[afl_s]atrim=start={fl_s}:end={fl_e},asetpts=PTS-STARTPTS[afl];"
            f"[amicro_s]atrim=start={micro_s}:end={micro_e},asetpts=PTS-STARTPTS,volume=0.15[amicro];"
            f"[am_s]asetpts=PTS-STARTPTS[am];"
            f"[afl][amicro][am]concat=n=3:v=0:a=1[afinal]"
        )
        a_cur = "afinal"

    cmd = [ffmpeg, "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{raw_duration:.2f}", "-i", video_path]
    if parts:
        cmd += ["-filter_complex", ";".join(parts), "-map", f"[{a_cur}]"]
    else:
        cmd += ["-vn"]
    cmd += ["-ac", "1", "-ar", "16000", str(out_wav)]
    res = subprocess.run(cmd, capture_output=True, creationflags=FFMPEG_FLAGS)
    return res.returncode == 0 and out_wav.exists()


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
    subtitles_mode: str = "whisper",
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
    enable_slowmo: bool = True,
    forced_subtitles: Optional[list[dict[str, Any]]] = None,
    project_name: str = "gameplay",
    max_duration_override: Optional[float] = None,
    overlay_config: Optional[dict[str, Any]] = None,
    enable_anti_factory: bool = False,
    clip_index: int = 0,
    enable_seamless_loop: bool = False,
) -> dict[str, Any]:
    """Render a 9:16 viral clip (1080x1920) with P0 Precision Engine:
    - Real Peak Hook (1.5s-2.0s, default 1.8s: 0.5s before -> ~1.3s after climax) + 0.10s micro-flash
      followed by normal clip context in a single synchronized video+audio concat filter_complex.
    - Top Hook Text configurable 2.0s-2.5s (default 2.3s), auto-shifting subtitles/zooms/SFX by hook offset.
    - Precision Smooth Impact Zoom (1.12x-1.15x, ease-in/ease-out, zero black borders, >=6s interval, max 3/clip).
    - Controlled Slow-Motion (0.75x) applied exclusively to final hit / death climax with toggle.
    """
    ffmpeg, _ = get_ffmpeg_bins()
    if not ffmpeg:
        raise XRayError("FFmpeg nao encontrado para renderizar o clipe.", category="FFMPEG", status_code=500)

    settings = load_settings()
    out_dir = get_output_dir()
    start = float(moment["start"])
    end = float(moment["end"])
    peak = float(moment.get("peak", (start + end) / 2.0))
    is_death = bool(moment.get("is_player_death", False))
    category = str(moment.get("category") or "combate_intenso")
    platform_target = str(moment.get("platform_target") or "")

    # Respect TikTok Long (61-90s) or configured max duration cleanly
    if max_duration_override is not None:
        max_clip_sec = float(max_duration_override)
    elif platform_target == "tiktok_long":
        max_clip_sec = float(settings.get("tiktok_long_max_sec", 90))
    else:
        max_clip_sec = float(settings.get("default_clip_max_sec", 55))
    if (end - start) > max_clip_sec:
        end = round(start + max_clip_sec, 2)

    raw_duration = max(2.5, end - start)
    rank = int(moment.get("rank", 1))
    hook_text = (moment.get("hook_2s") or "OLHA ESSE FINAL...").strip().upper()
    cta_text = (moment.get("cta_comment") or "Voce escaparia dessa?").strip()
    climax_rel = float(moment.get("climax_rel") or max(1.5, min(raw_duration - 1.5, peak - start)))
    raw_impacts = [float(t) for t in (moment.get("impact_times") or [climax_rel]) if 0.5 < float(t) < raw_duration - 0.3]
    if not raw_impacts:
        raw_impacts = [round(max(1.2, min(raw_duration - 1.0, climax_rel)), 2)]

    # Controlled Slow-Motion (0.75x): strictly on the final hit / death climax (never on pure dialogue)
    final_hit_climax = round(max(climax_rel, raw_impacts[-1]), 2)
    allow_slowmo = bool(enable_slowmo and (is_death or category != "dialogo_historia"))

    has_audio = _has_audio_stream(video_path)

    # 1. Build combined speed map: Normal (1.0x) + Internal Dead Jump-Cuts (1.25x) + Final-Hit/Death Slow-Mo (0.75x)
    time_segs, paced_impacts, paced_duration, used_pacing, used_slowmo = _build_time_segments(
        video_path=video_path,
        start=start,
        end=end,
        impact_times=raw_impacts,
        climax_rel=final_hit_climax,
        enable_smart_pacing=enable_smart_pacing,
        enable_slowmo=allow_slowmo,
        has_audio=has_audio,
    )
    has_speed_edits = len(time_segs) > 1 or abs(time_segs[0]["speed"] - 1.0) > 0.01

    # 2. P0 Real Peak Hook (1.5s-2.0s, default 1.8s: 0.5s before climax -> ~1.3s after) + 0.10s Micro-Flash
    cfg_peak_hook = round(max(1.5, min(2.0, float(settings.get("peak_hook_duration_sec", 1.8)))), 2)
    cfg_hook_text_dur = round(max(2.0, min(2.5, float(settings.get("hook_text_duration_sec", 2.3)))), 2)
    cfg_zoom_intensity = round(max(1.06, min(1.25, float(settings.get("impact_zoom_intensity", 1.14)))), 2)
    cfg_zoom_min_gap = round(max(6.0, float(settings.get("zoom_min_interval_sec", 6.0))), 2)
    cfg_zoom_max = max(1, min(3, int(settings.get("zoom_max_per_clip", 3))))

    peak_rel = max(0.5, min(paced_duration - 0.5, paced_impacts[0]))
    use_flash = bool(enable_preview_flash and paced_duration >= 4.5)
    if use_flash:
        peak_hook_dur = min(cfg_peak_hook, max(1.5, paced_duration - 1.0))
        fl_s = round(max(0.0, min(paced_duration - peak_hook_dur - 0.15, peak_rel - 0.50)), 2)
        fl_e = round(min(paced_duration - 0.12, fl_s + peak_hook_dur), 2)
        actual_peak_hook_dur = round(max(0.0, fl_e - fl_s), 2)
        micro_flash_dur = 0.10
        micro_s = fl_e
        micro_e = round(min(paced_duration, micro_s + micro_flash_dur), 2)
        actual_micro_dur = round(max(0.05, micro_e - micro_s), 2)
        intro_offset = round(actual_peak_hook_dur + actual_micro_dur, 2)
    else:
        fl_s = fl_e = micro_s = micro_e = 0.0
        actual_peak_hook_dur = 0.0
        actual_micro_dur = 0.0
        intro_offset = 0.0

    final_duration = round(paced_duration + intro_offset, 2)
    shifted_impacts = [round(t + intro_offset, 2) for t in paced_impacts]

    slug = _safe_filename(f"{project_name}_top{rank}_{style}")
    out_file = out_dir / f"{slug}_{uuid.uuid4().hex[:5]}.mp4"

    show_hook = enable_hook and style != "clipe_limpo" and bool(hook_text)
    # When seamless loop is active and user prefers clean loop, visual end CTA is optional (CTA goes to pinned comment!)
    show_cta = enable_cta_comment and (not enable_seamless_loop) and style != "clipe_limpo" and bool(cta_text)
    pinned_comment_cta = f"{cta_text} Comenta ai que eu respondo todo mundo!" if cta_text else "Qual nota de 0 a 10 para essa cena? Comenta ai!"
    use_bar = bool(enable_progress_bar and style != "clipe_limpo")

    ass_file: Optional[Path] = None
    hook_txt_file: Optional[Path] = None
    timed_wav: Optional[Path] = None

    try:
        # 3. Extract EXACT post-hook/post-slowmo audio WAV & transcribe with faster-whisper
        whisper_segments: list[dict[str, Any]] = []
        if forced_subtitles is not None:
            # Shift pre-hook forced subtitles by exact inserted intro_offset
            whisper_segments = [
                {
                    "start": round(float(s["start"]) + intro_offset, 2),
                    "end": round(float(s["end"]) + intro_offset, 2),
                    "text": s["text"],
                }
                for s in forced_subtitles
            ]
        elif subtitles_mode == "whisper" and has_audio:
            timed_wav = TEMP_DIR / f"wsub_{uuid.uuid4().hex[:8]}.wav"
            if _extract_timed_audio_for_whisper(
                ffmpeg, video_path, start, raw_duration, time_segs, use_flash, fl_s, fl_e, micro_s, micro_e, timed_wav
            ):
                whisper_segments = _transcribe_wav_with_whisper(timed_wav)

        # Merge saved overlay.json with moment's hook_2s text + optional P2 Anti-Factory Variation
        eff_overlay = dict(overlay_config or load_overlay_config())
        eff_style = style
        if enable_anti_factory:
            eff_style, eff_overlay = build_anti_factory_variation(clip_index, style, eff_overlay, category)
        if hook_text and not (overlay_config and overlay_config.get("force_text")):
            eff_overlay["text"] = hook_text
        if not eff_overlay.get("duration_sec"):
            eff_overlay["duration_sec"] = cfg_hook_text_dur

        candidate_ass = TEMP_DIR / f"subs_{uuid.uuid4().hex[:8]}.ass"
        ass_file = build_viral_ass_file(
            out_ass=candidate_ass,
            whisper_segments=whisper_segments,
            subtitle_style_id=subtitle_style,
            cta_comment=cta_text,
            show_cta=show_cta,
            total_duration=final_duration,
            hook_overlay=eff_overlay,
            show_hook=show_hook,
        )

        zoom_expr, shake_x, shake_y, impact_pulse, applied_zooms = _build_impact_zoom_124_and_shake(
            impact_times=shifted_impacts,
            enable_impact_zoom=enable_impact_zoom,
            dialogue_segments=whisper_segments,
            subtitles_mode=subtitles_mode,
            clip_duration=final_duration,
            zoom_intensity=cfg_zoom_intensity,
            min_interval_sec=cfg_zoom_min_gap,
            max_zooms=cfg_zoom_max,
        )

        resolved_layout = resolve_framing_layout(eff_style, category)
        used_tracking = bool(resolved_layout == "fill" and enable_action_tracking)
        track_cx_expr = (
            _compute_action_tracking_expr(video_path, start, end, time_offset=intro_offset)
            if used_tracking
            else "0.5"
        )

        filter_parts: list[str] = []
        v_cur = "0:v"
        a_cur = "0:a" if has_audio else ""

        # Apply speed segments (1.25x dead pacing + 0.75x final-hit/death slow-motion)
        if has_speed_edits:
            n_seg = len(time_segs)
            v_splits = "".join(f"[vs{i}]" for i in range(n_seg))
            filter_parts.append(f"[0:v]split={n_seg}{v_splits}")
            if has_audio:
                a_splits = "".join(f"[as{i}]" for i in range(n_seg))
                filter_parts.append(f"[0:a]asplit={n_seg}{a_splits}")

            concat_in = []
            for i, seg in enumerate(time_segs):
                sp = seg["speed"]
                pts_expr = f"(PTS-STARTPTS)/{sp}" if abs(sp - 1.0) > 0.01 else "PTS-STARTPTS"
                filter_parts.append(f"[vs{i}]trim=start={seg['s']}:end={seg['e']},setpts={pts_expr}[vp{i}]")
                concat_in.append(f"[vp{i}]")
                if has_audio:
                    atempo = f",atempo={sp}" if abs(sp - 1.0) > 0.01 else ""
                    filter_parts.append(f"[as{i}]atrim=start={seg['s']}:end={seg['e']},asetpts=PTS-STARTPTS{atempo}[ap{i}]")
                    concat_in.append(f"[ap{i}]")

            if has_audio:
                filter_parts.append(f"{''.join(concat_in)}concat=n={n_seg}:v=1:a=1[vpaced][apaced]")
                v_cur, a_cur = "vpaced", "apaced"
            else:
                filter_parts.append(f"{''.join(concat_in)}concat=n={n_seg}:v=1:a=0[vpaced]")
                v_cur = "vpaced"

        # Prepend Real Peak Hook (1.5s-2.0s) + 0.10s Micro-Flash + Full Context in ONE synchronized concat
        if use_flash:
            if has_audio:
                filter_parts.append(
                    f"[{v_cur}]split=3[v_fl_src][v_mf_src][v_main_src];"
                    f"[v_fl_src]trim=start={fl_s}:end={fl_e},setpts=PTS-STARTPTS,eq=contrast=1.08:saturation=1.10[v_fl];"
                    f"[v_mf_src]trim=start={micro_s}:end={micro_e},setpts=PTS-STARTPTS,eq=brightness=0.52:contrast=1.28[v_mf];"
                    f"[v_main_src]setpts=PTS-STARTPTS[v_main];"
                    f"[{a_cur}]asplit=3[a_fl_src][a_mf_src][a_main_src];"
                    f"[a_fl_src]atrim=start={fl_s}:end={fl_e},asetpts=PTS-STARTPTS[a_fl];"
                    f"[a_mf_src]atrim=start={micro_s}:end={micro_e},asetpts=PTS-STARTPTS,volume=0.15[a_mf];"
                    f"[a_main_src]asetpts=PTS-STARTPTS[a_main];"
                    f"[v_fl][a_fl][v_mf][a_mf][v_main][a_main]concat=n=3:v=1:a=1[vin][ain]"
                )
                v_cur, a_cur = "vin", "ain"
            else:
                filter_parts.append(
                    f"[{v_cur}]split=3[v_fl_src][v_mf_src][v_main_src];"
                    f"[v_fl_src]trim=start={fl_s}:end={fl_e},setpts=PTS-STARTPTS,eq=contrast=1.08:saturation=1.10[v_fl];"
                    f"[v_mf_src]trim=start={micro_s}:end={micro_e},setpts=PTS-STARTPTS,eq=brightness=0.52:contrast=1.28[v_mf];"
                    f"[v_main_src]setpts=PTS-STARTPTS[v_main];"
                    f"[v_fl][v_mf][v_main]concat=n=3:v=1:a=0[vin]"
                )
                v_cur = "vin"

        base_contrast = 1.07 if style == "clipe_limpo" else 1.05
        base_sat = 1.14 if style == "clipe_limpo" else 1.10
        if enable_impact_zoom and impact_pulse != "0":
            eq_filter = (
                f"eq=eval=frame:"
                f"contrast='{base_contrast}+0.12*({impact_pulse})':"
                f"saturation='{base_sat}+0.13*({impact_pulse})'"
            )
        else:
            eq_filter = f"eq=contrast={base_contrast}:saturation={base_sat}"

        # 4. 4 Framing Layouts (Fit, Big ~4:3 HUD-Safe, Fill 9:16, Auto) with High-Quality Lanczos scaling for 720p PS4
        if resolved_layout == "fill":
            crop_x = f"max(0,min(iw-1080,iw*{track_cx_expr}-540+({shake_x})))"
            crop_y = f"max(0,min(ih-1920,(ih-1920)/2+({shake_y})))"
            filter_parts.append(
                f"[{v_cur}]scale=w='trunc(max(1080,1920*dar)*({zoom_expr})/2)*2':"
                f"h='trunc(1920*({zoom_expr})/2)*2':eval=frame:flags=lanczos,"
                f"crop=1080:1920:x='{crop_x}':y='{crop_y}',{eq_filter}[base]"
            )
        elif resolved_layout == "big":
            # Big (~4:3 expanded crop 1080x810 Lanczos): fills more vertical area while preserving top/bottom HUD!
            fg_crop_x = f"max(0,min(iw-1080,(iw-1080)/2+({shake_x})))"
            fg_crop_y = f"max(0,min(ih-810,(ih-810)/2+({shake_y})))"
            filter_parts.append(
                f"[{v_cur}]split=2[bg_src][fg_src];"
                f"[bg_src]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop=1080:1920,boxblur=24:4,eq=brightness=-0.12:saturation=1.14[bg];"
                f"[fg_src]crop=w='min(iw,ih*4/3)':h=ih:x='(iw-ow)/2':y=0,"
                f"scale=w='trunc(1080*({zoom_expr})/2)*2':"
                f"h='trunc(810*({zoom_expr})/2)*2':eval=frame:flags=lanczos,"
                f"crop=1080:810:x='{fg_crop_x}':y='{fg_crop_y}',{eq_filter}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )
        else:
            # Fit (Viral Blur / Clipe Limpo): 16:9 1080x608 Lanczos foreground over blurred 9:16 background
            fg_crop_x = f"max(0,min(iw-1080,(iw-1080)/2+({shake_x})))"
            fg_crop_y = f"max(0,min(ih-608,(ih-608)/2+({shake_y})))"
            filter_parts.append(
                f"[{v_cur}]split=2[bg_src][fg_src];"
                f"[bg_src]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop=1080:1920,boxblur=26:4,eq=brightness=-0.10:saturation=1.15[bg];"
                f"[fg_src]scale=w='trunc(1080*({zoom_expr})/2)*2':"
                f"h='trunc(608*({zoom_expr})/2)*2':eval=frame:flags=lanczos,"
                f"crop=1080:608:x='{fg_crop_x}':y='{fg_crop_y}',{eq_filter}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )

        v_stage = "base"

        # 5. 3px Neon Retention Progress Bar in Safe Zone
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

        # 6. Post-compose overlays: Pixel-Exact .ass (HookTitle from overlay.json + Whisper + CTA)
        post_filters: list[str] = []
        if ass_file and ass_file.exists():
            ass_escaped = escape_ffmpeg_windows_path(ass_file)
            post_filters.append(f"subtitles='{ass_escaped}':charenc=UTF-8")

        if post_filters:
            filter_parts.append(f"[{v_stage}]{','.join(post_filters)},setsar=1[vout]")
        else:
            filter_parts.append(f"[{v_stage}]setsar=1[vout]")

        # 7. Professional Audio (-14 LUFS + Limiter + Ducking + Impact SFX synced strictly to applied zooms)
        audio_chain, final_a_label = _build_pro_audio_chain(
            base_audio_label=a_cur,
            impact_times=applied_zooms if applied_zooms else shifted_impacts[:3],
            enable_impact_sfx=enable_impact_sfx,
            enable_audio_norm=enable_audio_norm,
            has_audio=has_audio,
        )
        if audio_chain:
            filter_parts.append(audio_chain)

        # Optional Seamless Loop Audio Bridge (smooth tail fade reconnecting cleanly to t=0)
        if enable_seamless_loop and has_audio and final_duration >= 4.0:
            fade_st = round(max(0.5, final_duration - 0.28), 2)
            a_src = final_a_label if final_a_label != "0:a" else "0:a"
            filter_parts.append(
                f"[{a_src}]afade=t=in:st=0:d=0.10,afade=t=out:st={fade_st}:d=0.28[aloop]"
            )
            final_a_label = "aloop"

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

        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=FFMPEG_FLAGS)
        if res.returncode != 0 or not out_file.exists():
            raise XRayError(
                f"Falha ao renderizar o clipe #{rank} no estilo '{style}'. Verifique detalhes tecnicos do FFmpeg.",
                category="RENDER",
                technical_detail=(res.stderr or "Erro desconhecido no FFmpeg").strip()[-1400:],
                status_code=500,
                context={"moment_id": moment.get("id"), "style": style, "output": str(out_file)},
            )

        size_mb = round(out_file.stat().st_size / (1024 * 1024), 2)
        cover_file = out_file.with_name(f"{out_file.stem}_CAPA.jpg")
        cover_saved = generate_short_cover_thumbnail(
            mp4_path=out_file,
            title_text=str(moment.get("title") or hook_text or "CLIPE EPICO"),
            out_jpg=cover_file,
            seek_sec=min(max(0.4, intro_offset + 1.2), max(0.5, final_duration * 0.45)),
        )
        return {
            "file_path": str(out_file),
            "file_name": out_file.name,
            "media_url": f"/media/saida/{out_file.name}",
            "cover_path": cover_saved or "",
            "cover_url": f"/media/saida/{cover_file.name}" if cover_saved else "",
            "layout_used": resolved_layout,
            "overlay_applied": eff_overlay if show_hook else None,
            "pinned_comment_cta": pinned_comment_cta,
            "anti_factory_applied": bool(enable_anti_factory),
            "seamless_loop": bool(enable_seamless_loop),
            "size_mb": size_mb,
            "duration": final_duration,
            "start": round(start, 2),
            "end": round(end, 2),
            "style": style,
            "subtitles_mode": subtitles_mode,
            "subtitle_style": subtitle_style,
            "subtitles_burned_count": len(whisper_segments),
            "retention": {
                "preview_flash": use_flash,
                "peak_hook_duration": actual_peak_hook_dur,
                "micro_flash_duration": actual_micro_dur,
                "intro_offset": intro_offset,
                "hook_text_duration": round(float(eff_overlay.get("duration_sec", cfg_hook_text_dur)), 2) if show_hook else 0.0,
                "infinite_loop": enable_infinite_loop,
                "cta_comment": show_cta,
                "impact_sfx": bool(enable_impact_sfx and has_audio),
                "smart_pacing": used_pacing,
                "slowmo_075": used_slowmo,
                "impact_zoom_124": bool(enable_impact_zoom and applied_zooms),
                "zoom_intensity": cfg_zoom_intensity,
                "zoom_count": len(applied_zooms),
                "zoom_times": applied_zooms,
                "progress_bar": use_bar,
                "audio_norm_lufs": bool(enable_audio_norm and has_audio),
                "action_tracking": used_tracking,
            },
            "source_video_path": str(video_path),
            "peak": round(peak, 2),
            "mode": "viral_9_16",
        }
    finally:
        for tmp_f in (ass_file, hook_txt_file, timed_wav):
            if tmp_f and tmp_f.exists():
                try:
                    tmp_f.unlink()
                except OSError:
                    pass


def create_tiktok_short_version(
    clip_path: str,
    project_name: str = "clipe",
) -> dict[str, Any]:
    """Create a punchy 25-second TikTok/Reels cut directly from an ALREADY-RENDERED clip MP4
    (>35s) in seconds without re-processing the original huge PS4 video:
    - Preserves first 3.5s (Visual Hook)
    - Concatenates the 21.5s most intense climax window from [3.5s..end]
    - Outputs 1080x1920 faststart MP4 (~25.0s total)
    """
    ffmpeg, ffprobe = get_ffmpeg_bins()
    src = Path(clip_path)
    if not ffmpeg or not src.exists():
        raise XRayError(
            "Arquivo MP4 original do clipe nao encontrado para criar a versao curta TikTok (25s).",
            category="RENDER",
            status_code=404,
            context={"clip_path": clip_path},
        )

    # 1. Read exact duration of already-rendered clip
    dur = 0.0
    try:
        pr = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        dur = float((pr.stdout or "0").strip() or 0.0)
    except Exception:
        dur = 0.0

    if dur <= 26.0:
        raise XRayError(
            f"Este clipe ja tem {dur:.1f}s (curto o suficiente para TikTok/Reels).",
            category="RENDER",
            status_code=400,
        )

    # 2. Fast audio RMS scan across [3.5s .. dur] to find the highest-energy climax center
    hook_len = 3.5
    body_len = 21.5
    climax_center = max(hook_len + body_len * 0.5, dur * 0.62)
    has_audio = _has_audio_stream(str(src))
    if has_audio:
        try:
            sr = 8000
            ar = subprocess.run(
                [ffmpeg, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"],
                capture_output=True, creationflags=FFMPEG_FLAGS,
            )
            if ar.returncode == 0 and ar.stdout:
                samples = np.frombuffer(ar.stdout, dtype=np.int16).astype(np.float32) / 32768.0
                win = sr  # 1s bins
                n_bins = max(1, len(samples) // win)
                rms = np.array([float(np.sqrt(np.mean(samples[i * win : (i + 1) * win] ** 2))) for i in range(n_bins)])
                start_bin = min(n_bins - 1, 4)
                if n_bins > start_bin + 2:
                    best_bin = start_bin + int(np.argmax(rms[start_bin:]))
                    climax_center = float(best_bin) + 0.5
        except Exception:
            pass

    # Center 21.5s climax window cleanly inside [3.5s .. dur]
    body_start = max(hook_len, min(dur - body_len, climax_center - body_len * 0.55))
    body_end = min(dur, body_start + body_len)
    if body_end - body_start < body_len and body_start > hook_len:
        body_start = max(hook_len, body_end - body_len)
    expected_dur = round(hook_len + (body_end - body_start), 2)

    settings = load_settings()
    out_dir = get_output_dir()
    slug = _safe_filename(f"{project_name}_TikTok_25s")
    out_file = out_dir / f"{slug}_{uuid.uuid4().hex[:5]}.mp4"
    enc_args = _detect_video_encoder(ffmpeg, settings.get("hardware_encoder", "auto"))

    if has_audio:
        fc = (
            f"[0:v]split=2[v1s][v2s];"
            f"[v1s]trim=start=0:end={hook_len:.2f},setpts=PTS-STARTPTS[v1];"
            f"[v2s]trim=start={body_start:.2f}:end={body_end:.2f},setpts=PTS-STARTPTS[v2];"
            f"[0:a]asplit=2[a1s][a2s];"
            f"[a1s]atrim=start=0:end={hook_len:.2f},asetpts=PTS-STARTPTS[a1];"
            f"[a2s]atrim=start={body_start:.2f}:end={body_end:.2f},asetpts=PTS-STARTPTS[a2];"
            f"[v1][a1][v2][a2]concat=n=2:v=1:a=1[vout][aout]"
        )
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-i", str(src),
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "[aout]",
            *enc_args,
            "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(out_file),
        ]
    else:
        fc = (
            f"[0:v]split=2[v1s][v2s];"
            f"[v1s]trim=start=0:end={hook_len:.2f},setpts=PTS-STARTPTS[v1];"
            f"[v2s]trim=start={body_start:.2f}:end={body_end:.2f},setpts=PTS-STARTPTS[v2];"
            f"[v1][v2]concat=n=2:v=1:a=0[vout]"
        )
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-i", str(src),
            "-filter_complex", fc,
            "-map", "[vout]",
            *enc_args,
            "-pix_fmt", "yuv420p", "-r", "30",
            "-movflags", "+faststart",
            str(out_file),
        ]

    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=FFMPEG_FLAGS)
    if res.returncode != 0 or not out_file.exists():
        raise XRayError(
            "Falha ao criar a Versao Curta TikTok (25s) via FFmpeg.",
            category="RENDER",
            technical_detail=(res.stderr or "")[-1000:],
            status_code=500,
        )

    return {
        "file_path": str(out_file),
        "file_name": out_file.name,
        "media_url": f"/media/saida/{out_file.name}",
        "size_mb": round(out_file.stat().st_size / (1024 * 1024), 2),
        "duration": expected_dur,
        "mode": "viral_9_16",
    }


def create_tiktok_long_version(
    source_video_path: str,
    clip_meta: dict[str, Any],
    project_name: str = "clipe",
) -> dict[str, Any]:
    """Generate a Monetizable TikTok (61s-90s) expanded version of an existing clip moment
    by expanding gameplay context smoothly before and after the original action peak."""
    ffmpeg, ffprobe = get_ffmpeg_bins()
    src = Path(source_video_path or "")
    if not ffmpeg or not src.exists() or not src.is_file():
        raise XRayError(
            "Video de gameplay original nao encontrado no disco para expandir a Versao TikTok Monetizavel (61-90s).",
            category="RENDER",
            status_code=404,
            context={"source_video_path": source_video_path},
        )

    # Probe full source duration
    src_dur = 0.0
    try:
        pr = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        src_dur = float((pr.stdout or "0").strip() or 0.0)
    except Exception:
        src_dur = 0.0

    settings = load_settings()
    tk_min = float(settings.get("tiktok_long_min_sec", 61))
    tk_max = float(settings.get("tiktok_long_max_sec", 90))
    tk_target = max(tk_min + 2.0, min(tk_max - 3.0, float(settings.get("tiktok_long_target_sec", 75))))

    if src_dur < tk_min:
        raise XRayError(
            f"O video original tem apenas {src_dur:.1f}s (minimo {int(tk_min)}s para Versao TikTok Monetizavel).",
            category="RENDER",
            status_code=400,
        )

    orig_start = float(clip_meta.get("start", 0.0))
    orig_end = float(clip_meta.get("end", orig_start + 25.0))
    peak = float(clip_meta.get("peak") or ((orig_start + orig_end) / 2.0))

    # Expand context ~45% before peak and ~55% after peak to land cleanly inside [61s, 90s]
    desired_raw = min(src_dur, max(tk_min, min(tk_max - 2.5, tk_target)))
    exp_start = max(0.0, peak - desired_raw * 0.45)
    exp_end = min(src_dur, exp_start + desired_raw)
    if (exp_end - exp_start) < desired_raw:
        exp_start = max(0.0, exp_end - desired_raw)
    exp_start = round(exp_start, 2)
    exp_end = round(exp_end, 2)

    rel_peak = round(max(2.0, min((exp_end - exp_start) - 2.0, peak - exp_start)), 2)
    expanded_moment = {
        "id": clip_meta.get("moment_id") or f"tk61_{uuid.uuid4().hex[:6]}",
        "rank": clip_meta.get("rank", 1),
        "start": exp_start,
        "end": exp_end,
        "peak": peak,
        "climax_rel": rel_peak,
        "impact_times": [rel_peak],
        "category": clip_meta.get("category", "combate_intenso"),
        "platform_target": "tiktok_long",
        "is_player_death": bool(clip_meta.get("is_player_death", False)),
        "hook_2s": clip_meta.get("hook_2s") or "ESPERA ATE O FINAL...",
        "cta_comment": clip_meta.get("cta_comment") or "Valeu esperar ate o fim?",
    }

    res = render_viral_clip(
        video_path=str(src),
        moment=expanded_moment,
        style=clip_meta.get("style") or "viral_blur",
        subtitles_mode=clip_meta.get("subtitles_mode") or "off",
        subtitle_style=clip_meta.get("subtitle_style") or "amarelo_viral",
        enable_hook=True,
        enable_impact_zoom=True,
        enable_preview_flash=True,
        enable_infinite_loop=True,
        enable_cta_comment=True,
        enable_impact_sfx=True,
        enable_smart_pacing=False,  # preserve exact 61-90s monetizable duration cleanly
        enable_progress_bar=True,
        enable_audio_norm=True,
        enable_action_tracking=True,
        enable_slowmo=bool(settings.get("enable_slowmo", True)),
        project_name=f"{project_name}_TikTok_Monetizavel",
        max_duration_override=tk_max,
    )
    return res


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
