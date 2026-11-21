"""Multimodal PS4/PS5 gameplay analyzer:
- FFmpeg audio RMS & transient peak detection (works on pure gameplay without microphone)
- OpenCV optical flow / motion, scene cut, red/blood flash, and Game Over / death fade detection
- Intelligent Anti-Boredom filter (skips PS4 XMB blue menus, static pause/inventory, black loading screens)
- Calibrated game profiles + Qwen 3.5 9B Vision classification & viral copy generation
- Automatic pre/post context expansion for brutal deaths & executions
- Long-form 16:9 dead-stretch removal & YouTube chapters builder
"""

from __future__ import annotations

import json
import math
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from backend.config import GAME_PROFILES, TEMP_DIR, THUMBS_DIR, get_ffmpeg_bins, load_settings
from backend.errors import XRayError
from backend.ollama_client import analyze_moment_with_qwen

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def format_timestamp(seconds: float) -> str:
    secs = max(0, int(round(seconds)))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def probe_video(video_path: str) -> dict[str, Any]:
    """Inspect video metadata using ffprobe."""
    _, ffprobe = get_ffmpeg_bins()
    if not ffprobe:
        raise XRayError(
            "FFprobe nao encontrado no sistema. Configure o caminho do FFmpeg em Configuracoes.",
            category="FFMPEG",
            status_code=500,
        )
    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if res.returncode != 0:
        raise XRayError(
            "Nao foi possivel ler os metadados do video. O arquivo pode estar corrompido ou em formato nao suportado.",
            category="FFMPEG",
            technical_detail=res.stderr.strip()[:600],
            status_code=400,
            context={"video_path": video_path},
        )
    data = json.loads(res.stdout or "{}")
    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    astream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not vstream:
        raise XRayError(
            "O arquivo selecionado nao contem trilha de video valida.",
            category="UPLOAD",
            status_code=400,
            context={"video_path": video_path},
        )
    duration = float(data.get("format", {}).get("duration") or vstream.get("duration") or 0.0)
    width = int(vstream.get("width", 1920))
    height = int(vstream.get("height", 1080))
    fps_str = vstream.get("r_frame_rate", "30/1")
    try:
        num, den = fps_str.split("/")
        fps = round(float(num) / max(1.0, float(den)), 2)
    except Exception:
        fps = 30.0
    size_mb = round(Path(video_path).stat().st_size / (1024 * 1024), 2) if Path(video_path).exists() else 0.0
    return {
        "duration": round(duration, 2),
        "duration_str": format_timestamp(duration),
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": astream is not None,
        "size_mb": size_mb,
        "codec": vstream.get("codec_name", "unknown"),
    }


def _extract_audio_timeline(video_path: str, duration: float, step: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Extract mono 16kHz PCM audio via FFmpeg and compute per-window RMS and transient peak arrays."""
    bins = max(1, int(math.ceil(duration / step)))
    ffmpeg, _ = get_ffmpeg_bins()
    if not ffmpeg:
        return np.zeros(bins, dtype=np.float32), np.zeros(bins, dtype=np.float32)

    sr = 16000
    cmd = [
        ffmpeg,
        "-v", "error",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", str(sr),
        "-f", "s16le",
        "pipe:1",
    ]
    res = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
    if res.returncode != 0 or not res.stdout:
        return np.zeros(bins, dtype=np.float32), np.zeros(bins, dtype=np.float32)

    samples = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    win = max(1, int(sr * step))
    rms = np.zeros(bins, dtype=np.float32)
    peak = np.zeros(bins, dtype=np.float32)

    for i in range(bins):
        seg = samples[i * win : (i + 1) * win]
        if seg.size == 0:
            break
        rms[i] = float(np.sqrt(np.mean(seg * seg)))
        peak[i] = float(np.max(np.abs(seg)))

    # Emphasize sudden spikes relative to local rolling median (explosions, gunshots, jump scares)
    if bins >= 5:
        kernel = np.ones(min(15, bins), dtype=np.float32) / min(15, bins)
        baseline = np.convolve(rms, kernel, mode="same")
        transient = np.maximum(0.0, peak - baseline * 1.4)
    else:
        transient = peak

    return _norm01(rms), _norm01(transient)


def _norm01(arr: np.ndarray) -> np.ndarray:
    if arr.size == 0:
        return arr
    p95 = float(np.percentile(arr, 95))
    mx = max(p95, float(np.max(arr)) * 0.85, 1e-6)
    return np.clip(arr / mx, 0.0, 1.0)


def _extract_visual_timeline(
    video_path: str,
    duration: float,
    step: float = 0.5,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> dict[str, np.ndarray]:
    """Sample frames efficiently using OpenCV to compute optical flow/motion, scene cuts,
    red/blood flash, death/game-over fade signals, and PS4 XMB/pause/loading anti-boredom masks."""
    bins = max(1, int(math.ceil(duration / step)))
    motion = np.zeros(bins, dtype=np.float32)
    scene_cut = np.zeros(bins, dtype=np.float32)
    red_flash = np.zeros(bins, dtype=np.float32)
    death_sig = np.zeros(bins, dtype=np.float32)
    is_boring = np.zeros(bins, dtype=np.bool_)
    sharpness = np.zeros(bins, dtype=np.float32)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise XRayError(
            "OpenCV nao conseguiu abrir o arquivo de video para analise visual.",
            category="ANALISE",
            status_code=400,
            context={"video_path": video_path},
        )

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_stride = max(1, int(round(native_fps * step)))
    prev_gray: Optional[np.ndarray] = None
    prev_hist: Optional[np.ndarray] = None
    prev_luma = 128.0
    idx = 0
    frame_num = 0

    try:
        while idx < bins:
            ok = cap.grab()
            if not ok:
                break
            if frame_num % frame_stride == 0:
                ret, frame = cap.retrieve()
                if not ret or frame is None:
                    break
                small = cv2.resize(frame, (240, 135), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

                # Luminance & contrast
                luma = float(np.mean(gray))
                luma_std = float(np.std(gray))
                sharpness[idx] = float(cv2.Laplacian(gray, cv2.CV_64F).var())

                # Histogram for scene cut detection
                hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
                cv2.normalize(hist, hist)

                # Red/blood/explosion/rage intensity (edges + overall)
                b_ch, g_ch, r_ch = cv2.split(small)
                red_excess = np.maximum(0, r_ch.astype(np.int16) - np.maximum(g_ch, b_ch).astype(np.int16))
                red_score = float(np.mean(red_excess)) / 65.0
                # Border vignette red (classic PS4 low-HP / death blood overlay)
                border_mask = np.ones_like(gray, dtype=bool)
                border_mask[22:-22, 36:-36] = False
                border_red = float(np.mean(red_excess[border_mask])) / 55.0
                red_flash[idx] = min(1.5, red_score * 0.55 + border_red * 0.75)

                # PS4 XMB blue screen heuristic (dominant blue hue H in [100..130], high sat, low variance)
                blue_pixels = np.mean((hsv[:, :, 0] >= 98) & (hsv[:, :, 0] <= 132) & (hsv[:, :, 1] > 95))
                is_ps4_xmb = blue_pixels > 0.52 and luma_std < 48.0
                is_loading_black = luma < 14.0 and luma_std < 12.0

                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    mean_diff = float(np.mean(diff))
                    # Fast dense optical flow on tiny 120x68 grid for camera/swing/action velocity
                    tiny_prev = cv2.resize(prev_gray, (120, 68))
                    tiny_curr = cv2.resize(gray, (120, 68))
                    flow = cv2.calcOpticalFlowFarneback(
                        tiny_prev, tiny_curr, None, 0.5, 2, 9, 2, 5, 1.1, 0
                    )
                    mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
                    flow_score = float(np.percentile(mag, 85))
                    motion[idx] = mean_diff * 0.45 + flow_score * 4.5

                    if prev_hist is not None:
                        corr = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
                        scene_cut[idx] = max(0.0, 1.0 - corr)

                    # Death / Game Over / Execution signature:
                    # 1) High border blood red OR
                    # 2) Sharp transition from active color frame to desaturated/dark Game Over screen
                    sat_mean = float(np.mean(hsv[:, :, 1]))
                    sudden_darken = (prev_luma - luma) > 28.0 and luma < 70.0
                    desaturated_dramatic = sat_mean < 28.0 and luma < 85.0 and luma > 12.0 and scene_cut[idx] > 0.25
                    death_sig[idx] = (
                        (0.65 if border_red > 0.42 else 0.0)
                        + (0.55 if sudden_darken else 0.0)
                        + (0.45 if desaturated_dramatic else 0.0)
                    )

                    # Static pause / inventory detection: very low frame difference
                    is_static_pause = mean_diff < 2.2 and flow_score < 0.18
                    is_boring[idx] = bool(is_ps4_xmb or is_loading_black or is_static_pause)
                else:
                    is_boring[idx] = bool(is_ps4_xmb or is_loading_black)

                prev_gray = gray
                prev_hist = hist
                prev_luma = luma
                idx += 1

                if progress_cb and idx % max(1, bins // 12) == 0:
                    pct = 18 + int((idx / bins) * 44)
                    progress_cb(min(62, pct), f"Analisando movimento, combate e cenas ({format_timestamp(idx * step)})...")

            frame_num += 1
    finally:
        cap.release()

    return {
        "motion": _norm01(motion),
        "scene_cut": _norm01(scene_cut),
        "red_flash": _norm01(red_flash),
        "death_sig": _norm01(death_sig),
        "is_boring": is_boring,
        "sharpness": _norm01(sharpness),
    }


def _save_frame_jpeg(video_path: str, sec: float, out_path: Path, width: int = 640) -> Optional[str]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, sec * 1000.0))
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        h, w = frame.shape[:2]
        if w > width:
            nh = int(h * (width / w))
            frame = cv2.resize(frame, (width, nh), interpolation=cv2.INTER_AREA)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
        return str(out_path)
    finally:
        cap.release()


def analyze_gameplay(
    video_path: str,
    profile_id: str = "generico",
    project_id: str = "",
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> dict[str, Any]:
    """Run full multimodal analysis and return ranked moments + Mode 2 keep-segments & chapters."""
    settings = load_settings()
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    step = 0.5

    if progress_cb:
        progress_cb(5, "Inspecionando metadados do video (FFprobe)...")
    meta = probe_video(video_path)
    duration = meta["duration"]
    if duration < 2.0:
        raise XRayError(
            "O video e muito curto (menos de 2 segundos) para analise de gameplay.",
            category="ANALISE",
            status_code=400,
        )

    if progress_cb:
        progress_cb(12, "Analisando picos de audio, explosoes, tiros e tensao (FFmpeg PCM)...")
    audio_rms, audio_peak = _extract_audio_timeline(video_path, duration, step=step)

    if progress_cb:
        progress_cb(18, "Calculando Optical Flow, mudancas de cena e filtro anti-tedio (OpenCV)...")
    vis = _extract_visual_timeline(video_path, duration, step=step, progress_cb=progress_cb)

    bins = min(len(audio_rms), len(vis["motion"]))
    audio_rms = audio_rms[:bins]
    audio_peak = audio_peak[:bins]
    motion = vis["motion"][:bins]
    scene_cut = vis["scene_cut"][:bins]
    red_flash = vis["red_flash"][:bins]
    death_sig = vis["death_sig"][:bins]
    is_boring = vis["is_boring"][:bins]
    sharpness = vis["sharpness"][:bins]

    # Refine boring mask: require quiet audio + boring visual for at least 2.5s continuously
    quiet_audio = (audio_rms < 0.16) & (audio_peak < 0.22)
    raw_boring = is_boring & quiet_audio
    boring_mask = np.zeros(bins, dtype=np.bool_)
    run_len = 0
    min_boring_bins = max(4, int(2.5 / step))
    for i in range(bins):
        if raw_boring[i]:
            run_len += 1
        else:
            if run_len >= min_boring_bins:
                boring_mask[i - run_len : i] = True
            run_len = 0
    if run_len >= min_boring_bins:
        boring_mask[bins - run_len : bins] = True

    # Multimodal weighted fusion score
    w = profile["weights"]
    combined = (
        w["audio_peak"] * audio_peak
        + w["audio_rms"] * audio_rms
        + w["motion"] * motion
        + w["scene_cut"] * scene_cut
        + w["red_flash"] * red_flash
        + w["death_signal"] * death_sig
    )
    # Suppress boring menus/pauses/loadings strongly
    combined[boring_mask] *= 0.08

    # Smooth score over 1.5s window to locate peak centers cleanly
    smooth_k = min(3, bins)
    smoothed = np.convolve(combined, np.ones(smooth_k) / smooth_k, mode="same") if smooth_k > 1 else combined

    if progress_cb:
        progress_cb(68, "Detectando todos os momentos epicos, sustos e mortes cinematograficas...")

    # Peak picking: find ALL significant local peaks without artificially capping at 3 or 5
    sensitivity = float(settings.get("sensitivity", 0.55))
    min_gap_bins = max(4, int(profile["min_gap"] / step))
    # Adaptive threshold based on video's energy distribution
    median_s = float(np.median(smoothed))
    std_s = float(np.std(smoothed))
    threshold = max(0.14, median_s + std_s * (1.05 - sensitivity))

    # Collect local maxima above threshold (plus always pick top peak for short videos)
    candidate_indices: list[int] = []
    for i in range(1, max(2, bins - 1)):
        if boring_mask[i]:
            continue
        if smoothed[i] >= smoothed[i - 1] and smoothed[i] >= smoothed[min(bins - 1, i + 1)] and smoothed[i] >= threshold:
            candidate_indices.append(i)

    # Fallback for very uniform or short videos: sample top energetic windows spaced by min_gap
    if not candidate_indices:
        order = np.argsort(smoothed)[::-1]
        for idx in order:
            if not boring_mask[idx]:
                candidate_indices.append(int(idx))
                if len(candidate_indices) >= max(1, int(duration / 20)):
                    break

    # Non-maximum suppression keeping highest peaks spaced by min_gap_bins
    candidate_indices.sort(key=lambda i: float(smoothed[i]), reverse=True)
    selected_peaks: list[int] = []
    for idx in candidate_indices:
        if all(abs(idx - existing) >= min_gap_bins for existing in selected_peaks):
            selected_peaks.append(idx)

    # Sort by score descending to rank #1 .. #N
    selected_peaks.sort(key=lambda i: float(smoothed[i]), reverse=True)

    # Build clip objects with category classification + death context expansion + Qwen 3.5 Vision enrichment
    video_name = Path(video_path).stem
    proj_tag = project_id or uuid.uuid4().hex[:8]
    min_dur = float(settings.get("default_clip_min_sec", 12))
    max_dur = float(settings.get("default_clip_max_sec", 45))

    # Enrich top moments with Qwen Vision (limit vision calls to top 12 for speed; rest get instant calibrated metadata)
    vision_limit = 12
    moments: list[dict[str, Any]] = []
    total_peaks = max(1, len(selected_peaks))

    for rank_idx, pk in enumerate(selected_peaks):
        pk_sec = round(pk * step, 2)
        loc_slice = slice(max(0, pk - 4), min(bins, pk + 5))
        loc_death = float(np.max(death_sig[loc_slice]))
        loc_red = float(np.max(red_flash[loc_slice]))
        loc_peak = float(np.max(audio_peak[loc_slice]))
        loc_motion = float(np.max(motion[loc_slice]))
        loc_rms = float(np.mean(audio_rms[loc_slice]))

        # Heuristic category
        if loc_death >= 0.48 or (loc_red >= 0.58 and loc_death >= 0.30):
            h_cat = "morte_cinematografica"
        elif loc_peak >= 0.68 and loc_rms < 0.38:
            h_cat = "susto_tensao"
        elif loc_motion >= 0.68 and loc_red < 0.40:
            h_cat = "acrobacia_cinema"
        else:
            h_cat = "combate_intenso"

        is_death = h_cat == "morte_cinematografica"
        pre_pad = profile["death_pre_pad"] if is_death else profile["pre_pad"]
        post_pad = profile["death_post_pad"] if is_death else profile["post_pad"]

        # Expand boundaries dynamically while energy stays elevated
        start_sec = max(0.0, pk_sec - pre_pad)
        end_sec = min(duration, pk_sec + post_pad)
        if end_sec - start_sec < min_dur and duration >= min_dur:
            deficit = min_dur - (end_sec - start_sec)
            start_sec = max(0.0, start_sec - deficit * 0.55)
            end_sec = min(duration, start_sec + min_dur)
        if end_sec - start_sec > max_dur:
            end_sec = min(duration, start_sec + max_dur)

        # Find top 1-2 impact timestamps inside this clip for Punch-In/Punch-Out Zoom
        c_start_b = max(0, int(start_sec / step))
        c_end_b = min(bins, int(end_sec / step))
        impact_times: list[float] = [round(max(2.2, pk_sec - start_sec), 2)]
        if c_end_b - c_start_b > 8:
            sub = audio_peak[c_start_b:c_end_b] * 0.65 + motion[c_start_b:c_end_b] * 0.35
            top2 = np.argsort(sub)[::-1]
            for ti in top2:
                rel_t = round(ti * step, 2)
                if rel_t > 2.2 and rel_t < (end_sec - start_sec - 1.5):
                    if all(abs(rel_t - ex) >= 3.5 for ex in impact_times):
                        impact_times.append(rel_t)
                        if len(impact_times) >= 2:
                            break
        impact_times.sort()

        # Capture preview thumbnail + optional aftermath frame
        thumb_path = THUMBS_DIR / f"{proj_tag}_m{rank_idx + 1}_peak.jpg"
        saved_thumb = _save_frame_jpeg(video_path, pk_sec, thumb_path, width=640)
        vision_frames = [saved_thumb] if saved_thumb else []
        if is_death and pk_sec + 2.0 < duration:
            after_path = THUMBS_DIR / f"{proj_tag}_m{rank_idx + 1}_after.jpg"
            saved_after = _save_frame_jpeg(video_path, min(duration - 0.3, pk_sec + 2.0), after_path, width=480)
            if saved_after:
                vision_frames.append(saved_after)

        base_score = int(round(min(99, max(52, 55 + float(smoothed[pk]) * 44))))
        signals_summary = (
            f"audio_pico={loc_peak:.2f}, movimento={loc_motion:.2f}, "
            f"vermelho={loc_red:.2f}, sinal_morte={loc_death:.2f}, tempo={format_timestamp(pk_sec)}"
        )

        if rank_idx < vision_limit:
            if progress_cb:
                pct = 70 + int(((rank_idx + 1) / min(total_peaks, vision_limit)) * 22)
                progress_cb(min(93, pct), f"Qwen 3.5 avaliando momento #{rank_idx + 1} ({format_timestamp(pk_sec)})...")
            ai_info = analyze_moment_with_qwen(
                frame_paths=vision_frames,
                profile_id=profile_id,
                heuristic_category=h_cat,
                heuristic_score=base_score,
                signals_summary=signals_summary,
                video_name=video_name,
            )
        else:
            from backend.ollama_client import _fallback_moment_kit
            ai_info = _fallback_moment_kit(profile, h_cat, video_name)

        # If Qwen Vision confirms it's a menu/pause/loading screen, skip it!
        if ai_info.get("is_menu_or_boring") and len(selected_peaks) > 1:
            continue

        # If Qwen Vision identifies a player death/execution, guarantee full pre/post context!
        if ai_info.get("is_player_death") and not is_death:
            is_death = True
            start_sec = max(0.0, pk_sec - profile["death_pre_pad"])
            end_sec = min(duration, pk_sec + profile["death_post_pad"])

        final_score = max(45, min(99, base_score + int(ai_info.get("score_boost", 0))))
        moments.append(
            {
                "id": f"mom_{uuid.uuid4().hex[:8]}",
                "rank": 0,  # assigned after final sort
                "start": round(start_sec, 2),
                "end": round(end_sec, 2),
                "peak": pk_sec,
                "duration": round(end_sec - start_sec, 2),
                "start_str": format_timestamp(start_sec),
                "end_str": format_timestamp(end_sec),
                "score": final_score,
                "category": ai_info["category"],
                "category_label": _category_label(ai_info["category"]),
                "is_player_death": is_death,
                "impact_times": impact_times,
                "hook_2s": ai_info["hook_2s"],
                "cta_comment": ai_info.get("cta_comment", "Voce escaparia dessa?"),
                "title": ai_info["title"],
                "description": ai_info["description"],
                "hashtags": ai_info["hashtags"],
                "detected_game": ai_info.get("detected_game", profile["short_name"]),
                "reason": ai_info["reason"],
                "ai_powered": ai_info.get("ai_powered", False),
                "variation_index": 0,
                "thumb_url": f"/media/thumbs/{thumb_path.name}" if saved_thumb else "",
                "thumb_path": saved_thumb or "",
            }
        )

    # Re-sort by final score descending and assign ranks #1 .. #N
    moments.sort(key=lambda m: m["score"], reverse=True)
    for i, m in enumerate(moments, start=1):
        m["rank"] = i

    # Build Mode 2 (Long Gameplay 16:9) keep-segments by removing long dead stretches (>= 5s boring)
    keep_segments, removed_sec = _build_long_edit_segments(boring_mask, combined, duration, step=step)

    # Choose crispest high-energy frame for YouTube 16:9 Thumbnail
    thumb_scores = combined * 0.65 + sharpness * 0.35
    thumb_scores[boring_mask] = 0.0
    best_thumb_bin = int(np.argmax(thumb_scores)) if bins > 0 else 0
    best_thumb_sec = round(best_thumb_bin * step, 2)
    yt_thumb_path = THUMBS_DIR / f"{proj_tag}_youtube_thumb.jpg"
    _save_frame_jpeg(video_path, best_thumb_sec, yt_thumb_path, width=1920)

    # Build YouTube Chapters mapped onto the edited 16:9 timeline
    chapters = _build_youtube_chapters(keep_segments, moments)

    if progress_cb:
        progress_cb(100, f"Analise concluida! {len(moments)} momentos epicos encontrados.")

    boring_total_sec = round(float(np.sum(boring_mask)) * step, 1)
    return {
        "meta": meta,
        "profile_id": profile_id,
        "profile_name": profile["name"],
        "moments": moments,
        "total_moments": len(moments),
        "boring_skipped_sec": boring_total_sec,
        "long_edit": {
            "keep_segments": keep_segments,
            "removed_sec": round(removed_sec, 1),
            "edited_duration": round(sum(s["end"] - s["start"] for s in keep_segments), 1),
            "edited_duration_str": format_timestamp(sum(s["end"] - s["start"] for s in keep_segments)),
            "chapters": chapters,
            "best_thumb_sec": best_thumb_sec,
            "best_thumb_url": f"/media/thumbs/{yt_thumb_path.name}" if yt_thumb_path.exists() else "",
            "best_thumb_path": str(yt_thumb_path) if yt_thumb_path.exists() else "",
        },
    }


def _category_label(cat: str) -> str:
    return {
        "combate_intenso": "Combate & Acao",
        "morte_cinematografica": "Morte / Execucao",
        "susto_tensao": "Susto & Tensao",
        "acrobacia_cinema": "Cinema & Acrobacia",
    }.get(cat, "Momento Epico")


def _build_long_edit_segments(
    boring_mask: np.ndarray,
    combined: np.ndarray,
    duration: float,
    step: float = 0.5,
) -> tuple[list[dict[str, float]], float]:
    """Keep all engaging/story/dialogue/fight segments and cut long dead walks/menus/loadings (>= 5s)."""
    bins = len(boring_mask)
    if bins == 0:
        return [{"start": 0.0, "end": round(duration, 2)}], 0.0

    # Mark long dead blocks (>= 5s of boring or ultra-flat walking)
    dead = boring_mask | (combined < 0.07)
    min_cut_bins = int(5.0 / step)
    cut_mask = np.zeros(bins, dtype=np.bool_)
    run = 0
    for i in range(bins):
        if dead[i]:
            run += 1
        else:
            if run >= min_cut_bins:
                # Leave 1s breath at edges so cuts feel smooth
                cut_mask[i - run + 2 : max(i - run + 2, i - 2)] = True
            run = 0
    if run >= min_cut_bins:
        cut_mask[bins - run + 2 : bins] = True

    segments: list[dict[str, float]] = []
    in_seg = False
    seg_start = 0.0
    for i in range(bins):
        if not cut_mask[i] and not in_seg:
            in_seg = True
            seg_start = i * step
        elif cut_mask[i] and in_seg:
            in_seg = False
            seg_end = i * step
            if seg_end - seg_start >= 1.5:
                segments.append({"start": round(seg_start, 2), "end": round(seg_end, 2)})
    if in_seg:
        segments.append({"start": round(seg_start, 2), "end": round(duration, 2)})

    if not segments:
        segments = [{"start": 0.0, "end": round(duration, 2)}]

    kept = sum(s["end"] - s["start"] for s in segments)
    return segments, max(0.0, duration - kept)


def _build_youtube_chapters(
    keep_segments: list[dict[str, float]],
    moments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map top epic moments onto the edited 16:9 timeline to create copy-ready YouTube chapters."""
    chapters: list[dict[str, Any]] = [{"timestamp": "00:00", "seconds": 0, "label": "Inicio da Gameplay"}]

    def map_time(orig_sec: float) -> Optional[float]:
        acc = 0.0
        for seg in keep_segments:
            if seg["start"] <= orig_sec <= seg["end"]:
                return acc + (orig_sec - seg["start"])
            acc += seg["end"] - seg["start"]
        return None

    chronological = sorted(moments, key=lambda m: m["start"])
    last_ts = 0.0
    for m in chronological[:12]:
        mapped = map_time(m["peak"])
        if mapped is None or mapped - last_ts < 18.0:
            continue
        chapters.append(
            {
                "timestamp": format_timestamp(mapped),
                "seconds": int(mapped),
                "label": f"{m['category_label']} - {m['title'][:52]}",
            }
        )
        last_ts = mapped

    return chapters
