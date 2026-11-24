"""Multimodal PS4/PS5 gameplay analyzer built for scale & complete scenes (25s to 55s dynamic):
- Scans 100% of the timeline (00:00 to end)
- Combines Action/Gunfire/Explosion/Death peaks WITH Scene Cuts & Dialogue/Cutscene speech detection (300-3400Hz FFT)
- Dynamic scene boundaries (25s to 55s): expands backward & forward while combat, dialogue, or tension
  continues, ending cleanly at natural pauses / end of fight instead of cutting scenes in half
- Captures 2 real frames (peak + aftermath) per moment + zero duplicate titles/hooks
- Marks each moment as 'already_exported' or 'Inedito' via data/exported_history.json
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

from backend.config import GAME_PROFILES, THUMBS_DIR, get_ffmpeg_bins, load_settings
from backend.errors import XRayError
from backend.hw_guard import CPUGuard
from backend.ollama_client import _pick_unique_fallback, analyze_moment_with_qwen
from backend.storage import annotate_moments_export_status

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
SUBPROC_FLAGS = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS


def format_timestamp(seconds: float) -> str:
    secs = max(0, int(round(seconds)))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def probe_video(video_path: str) -> dict[str, Any]:
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
        "-probesize", "2000000",
        "-analyzeduration", "2000000",
        "-print_format", "json",
        "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,duration",
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
    size_bytes = Path(video_path).stat().st_size if Path(video_path).exists() else 0
    size_mb = round(size_bytes / (1024 * 1024), 2)
    size_gb = round(size_bytes / (1024 ** 3), 2)
    return {
        "duration": round(duration, 2),
        "duration_str": format_timestamp(duration),
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": astream is not None,
        "size_mb": size_mb,
        "size_gb": size_gb,
        "codec": vstream.get("codec_name", "unknown"),
    }


def _norm01(arr: np.ndarray) -> np.ndarray:
    if arr.size == 0:
        return arr
    p95 = float(np.percentile(arr, 95))
    mx = max(p95, float(np.max(arr)) * 0.85, 1e-6)
    return np.clip(arr / mx, 0.0, 1.0)


def _extract_audio_timeline(
    video_path: str,
    duration: float,
    step: float = 0.5,
    cancel_check: Optional[Callable[[], bool]] = None,
    cpu_guard: Optional[CPUGuard] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Stream mono PCM via FFmpeg in chunks and compute:
    - audio_rms: overall loudness energy
    - audio_peak: transient spikes (gunshots, explosions, jump scares)
    - speech_score: vocal-band (300Hz-3400Hz) dialogue & cutscene speech activity
    """
    bins = max(1, int(math.ceil(duration / step)))
    ffmpeg, _ = get_ffmpeg_bins()
    if not ffmpeg:
        z = np.zeros(bins, dtype=np.float32)
        return z, z.copy(), z.copy()

    sr = 8000 if duration > 1200 else 16000
    win = max(1, int(sr * step))
    bytes_per_win = win * 2

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
    rms = np.zeros(bins, dtype=np.float32)
    peak = np.zeros(bins, dtype=np.float32)
    speech = np.zeros(bins, dtype=np.float32)

    freqs = np.fft.rfftfreq(win, d=1.0 / sr)
    vocal_mask = (freqs >= 300.0) & (freqs <= 3400.0)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=SUBPROC_FLAGS)
    try:
        idx = 0
        assert proc.stdout is not None
        while idx < bins:
            if cancel_check and idx % 20 == 0 and cancel_check():
                proc.terminate()
                raise InterruptedError("Processamento cancelado pelo usuario.")
            if cpu_guard and idx % 40 == 0:
                cpu_guard.check_and_pause(cancel_check=cancel_check, progress_cb=progress_cb, current_pct=14)
            chunk = proc.stdout.read(bytes_per_win)
            if not chunk or len(chunk) < 2:
                break
            usable = len(chunk) - (len(chunk) % 2)
            seg = np.frombuffer(chunk[:usable], dtype=np.int16).astype(np.float32) / 32768.0
            if seg.size:
                r_val = float(np.sqrt(np.mean(seg * seg)))
                rms[idx] = r_val
                peak[idx] = float(np.max(np.abs(seg)))
                # Vocal-band FFT energy ratio for cutscene / character dialogue detection
                if seg.size == win and r_val > 0.015:
                    spec = np.abs(np.fft.rfft(seg))
                    tot_e = float(np.sum(spec)) + 1e-6
                    voc_ratio = float(np.sum(spec[vocal_mask])) / tot_e
                    speech[idx] = r_val * voc_ratio
            idx += 1
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()

    if bins >= 5:
        k = min(15, bins)
        baseline = np.convolve(rms, np.ones(k, dtype=np.float32) / k, mode="same")
        transient = np.maximum(0.0, peak - baseline * 1.35)
    else:
        transient = peak

    return _norm01(rms), _norm01(transient), _norm01(speech)


def _select_audio_candidate_windows(
    audio_rms: np.ndarray,
    audio_peak: np.ndarray,
    speech: np.ndarray,
    duration: float,
    step: float = 0.5,
    window_half_sec: float = 12.0,
) -> list[tuple[float, float]]:
    """Stage A -> Stage B bridge: pick candidate peak windows from audio/speech energy
    plus light periodic anchors so Optical Flow only inspects high-value segments on Ryzen 5."""
    bins = len(audio_rms)
    if bins == 0 or duration <= 0:
        return []

    audio_score = np.clip(audio_peak * 0.52 + audio_rms * 0.28 + speech * 0.35, 0.0, 1.0)
    k = min(3, bins)
    sm = np.convolve(audio_score, np.ones(k) / k, mode="same") if k > 1 else audio_score
    thresh = max(0.16, float(np.percentile(sm, 68)))

    raw_peaks: list[int] = []
    for i in range(1, max(2, bins - 1)):
        if sm[i] >= sm[i - 1] and sm[i] >= sm[min(bins - 1, i + 1)] and sm[i] >= thresh:
            raw_peaks.append(i)

    # Regional anchors every 90s so purely visual moments are not missed
    slice_bins = max(4, int(round(90.0 / step)))
    for ws in range(0, bins, slice_bins):
        we = min(bins, ws + slice_bins)
        if we > ws:
            raw_peaks.append(ws + int(np.argmax(sm[ws:we])))

    ordered = sorted(set(raw_peaks), key=lambda b: float(sm[b]), reverse=True)
    min_sep_bins = max(4, int(round(16.0 / step)))
    chosen: list[int] = []
    max_windows = max(4, min(48, int(math.ceil(duration / 35.0))))
    for b in ordered:
        if all(abs(b - ex) >= min_sep_bins for ex in chosen):
            chosen.append(b)
            if len(chosen) >= max_windows:
                break

    if not chosen:
        chosen = [bins // 2]

    # Convert peaks to merged [start_sec, end_sec] windows
    intervals = sorted(
        (max(0.0, b * step - window_half_sec), min(duration, b * step + window_half_sec))
        for b in chosen
    )
    merged: list[tuple[float, float]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1] + 2.0:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _extract_visual_candidate_windows(
    video_path: str,
    duration: float,
    windows: list[tuple[float, float]],
    step: float = 0.5,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    cpu_guard: Optional[CPUGuard] = None,
) -> dict[str, np.ndarray]:
    """Stage B (Lightweight Ryzen 5 Mode): runs Optical Flow ONLY inside candidate peak windows,
    resized strictly to 320px width at 5 fps (0.2s sampling), dramatically reducing CPU & RAM."""
    bins = max(1, int(math.ceil(duration / step)))
    motion = np.zeros(bins, dtype=np.float32)
    scene_cut = np.zeros(bins, dtype=np.float32)
    red_flash = np.zeros(bins, dtype=np.float32)
    death_sig = np.zeros(bins, dtype=np.float32)
    is_boring = np.zeros(bins, dtype=np.bool_)
    sharpness = np.zeros(bins, dtype=np.float32)
    counts = np.zeros(bins, dtype=np.int32)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise XRayError(
            "OpenCV nao conseguiu abrir o arquivo de video para analise visual.",
            category="ANALISE",
            status_code=400,
            context={"video_path": video_path},
        )

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    target_fps = 5.0  # Strict 5 fps cap for Ryzen 5 2500U lightweight mode
    frame_stride = max(1, int(round(native_fps / target_fps)))
    total_win = max(1, len(windows))

    try:
        for w_idx, (ws, we) in enumerate(windows):
            if cancel_check and cancel_check():
                raise InterruptedError("Processamento cancelado pelo usuario.")
            if cpu_guard:
                pct_now = 18 + int((w_idx / total_win) * 38)
                cpu_guard.check_and_pause(cancel_check=cancel_check, progress_cb=progress_cb, current_pct=pct_now)

            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, ws * 1000.0))
            prev_gray: Optional[np.ndarray] = None
            prev_hist: Optional[np.ndarray] = None
            prev_luma = 128.0
            f_local = 0
            max_frames = max(2, int(math.ceil((we - ws) * native_fps)) + 2)

            while f_local < max_frames:
                if cancel_check and f_local % 15 == 0 and cancel_check():
                    raise InterruptedError("Processamento cancelado pelo usuario.")
                ok = cap.grab()
                if not ok:
                    break
                if f_local % frame_stride == 0:
                    ret, frame = cap.retrieve()
                    if not ret or frame is None:
                        break
                    cur_sec = ws + (f_local / max(1.0, native_fps))
                    b_idx = min(bins - 1, max(0, int(cur_sec / step)))

                    # Resize strictly to 320px width preserving aspect ratio (320x180 for 16:9)
                    fh, fw = frame.shape[:2]
                    target_w = 320
                    target_h = max(90, int(round(fh * (target_w / max(1, fw)))))
                    small = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
                    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

                    luma = float(np.mean(gray))
                    luma_std = float(np.std(gray))
                    sharp_val = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
                    cv2.normalize(hist, hist)

                    b_ch, g_ch, r_ch = cv2.split(small)
                    red_excess = np.maximum(0, r_ch.astype(np.int16) - np.maximum(g_ch, b_ch).astype(np.int16))
                    red_score = float(np.mean(red_excess)) / 65.0
                    bm_y = max(8, target_h // 6)
                    bm_x = max(12, target_w // 7)
                    border_mask = np.ones_like(gray, dtype=bool)
                    border_mask[bm_y:-bm_y, bm_x:-bm_x] = False
                    border_red = float(np.mean(red_excess[border_mask])) / 55.0
                    rf_val = min(1.5, red_score * 0.55 + border_red * 0.75)

                    blue_pixels = np.mean((hsv[:, :, 0] >= 98) & (hsv[:, :, 0] <= 132) & (hsv[:, :, 1] > 95))
                    is_ps4_xmb = blue_pixels > 0.52 and luma_std < 48.0
                    is_loading_black = luma < 14.0 and luma_std < 12.0

                    mot_val = 0.0
                    cut_val = 0.0
                    dth_val = 0.0
                    boring_frame = bool(is_ps4_xmb or is_loading_black)

                    if prev_gray is not None:
                        diff = cv2.absdiff(gray, prev_gray)
                        mean_diff = float(np.mean(diff))
                        tiny_prev = cv2.resize(prev_gray, (160, 90))
                        tiny_curr = cv2.resize(gray, (160, 90))
                        flow = cv2.calcOpticalFlowFarneback(
                            tiny_prev, tiny_curr, None, 0.5, 2, 9, 2, 5, 1.1, 0
                        )
                        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
                        flow_score = float(np.percentile(mag, 85))
                        mot_val = mean_diff * 0.45 + flow_score * 4.5

                        if prev_hist is not None:
                            corr = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
                            cut_val = max(0.0, 1.0 - corr)

                        sat_mean = float(np.mean(hsv[:, :, 1]))
                        sudden_darken = (prev_luma - luma) > 28.0 and luma < 70.0
                        desaturated_dramatic = sat_mean < 28.0 and 12.0 < luma < 85.0 and cut_val > 0.25
                        dth_val = (
                            (0.65 if border_red > 0.42 else 0.0)
                            + (0.55 if sudden_darken else 0.0)
                            + (0.45 if desaturated_dramatic else 0.0)
                        )
                        if mean_diff < 2.2 and flow_score < 0.18:
                            boring_frame = True

                    motion[b_idx] = max(motion[b_idx], mot_val)
                    scene_cut[b_idx] = max(scene_cut[b_idx], cut_val)
                    red_flash[b_idx] = max(red_flash[b_idx], rf_val)
                    death_sig[b_idx] = max(death_sig[b_idx], dth_val)
                    sharpness[b_idx] = max(sharpness[b_idx], sharp_val)
                    if boring_frame and counts[b_idx] == 0:
                        is_boring[b_idx] = True
                    elif not boring_frame:
                        is_boring[b_idx] = False
                    counts[b_idx] += 1

                    prev_gray = gray
                    prev_hist = hist
                    prev_luma = luma
                f_local += 1

            if progress_cb:
                pct = 18 + int(((w_idx + 1) / total_win) * 39)
                progress_cb(
                    min(57, pct),
                    f"Etapa B (Leve 320px@5fps): Optical Flow na janela {w_idx + 1}/{total_win} ({format_timestamp(ws)}–{format_timestamp(we)})...",
                )
    finally:
        cap.release()

    return {
        "motion": _norm01(motion),
        "scene_cut": _norm01(scene_cut),
        "red_flash": _norm01(red_flash),
        "death_sig": _norm01(death_sig),
        "is_boring": is_boring,
        "sharpness": _norm01(sharpness),
        "windows_scanned": len(windows),
    }


def _extract_visual_timeline(
    video_path: str,
    duration: float,
    step: float = 0.5,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    cpu_guard: Optional[CPUGuard] = None,
) -> dict[str, np.ndarray]:
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
    report_every = max(1, bins // 20)

    try:
        while idx < bins:
            if cancel_check and idx % 10 == 0 and cancel_check():
                raise InterruptedError("Processamento cancelado pelo usuario.")
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

                luma = float(np.mean(gray))
                luma_std = float(np.std(gray))
                sharpness[idx] = float(cv2.Laplacian(gray, cv2.CV_64F).var())

                hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
                cv2.normalize(hist, hist)

                b_ch, g_ch, r_ch = cv2.split(small)
                red_excess = np.maximum(0, r_ch.astype(np.int16) - np.maximum(g_ch, b_ch).astype(np.int16))
                red_score = float(np.mean(red_excess)) / 65.0
                border_mask = np.ones_like(gray, dtype=bool)
                border_mask[22:-22, 36:-36] = False
                border_red = float(np.mean(red_excess[border_mask])) / 55.0
                red_flash[idx] = min(1.5, red_score * 0.55 + border_red * 0.75)

                blue_pixels = np.mean((hsv[:, :, 0] >= 98) & (hsv[:, :, 0] <= 132) & (hsv[:, :, 1] > 95))
                is_ps4_xmb = blue_pixels > 0.52 and luma_std < 48.0
                is_loading_black = luma < 14.0 and luma_std < 12.0

                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    mean_diff = float(np.mean(diff))
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

                    sat_mean = float(np.mean(hsv[:, :, 1]))
                    sudden_darken = (prev_luma - luma) > 28.0 and luma < 70.0
                    desaturated_dramatic = sat_mean < 28.0 and luma < 85.0 and luma > 12.0 and scene_cut[idx] > 0.25
                    death_sig[idx] = (
                        (0.65 if border_red > 0.42 else 0.0)
                        + (0.55 if sudden_darken else 0.0)
                        + (0.45 if desaturated_dramatic else 0.0)
                    )

                    is_static_pause = mean_diff < 2.2 and flow_score < 0.18
                    is_boring[idx] = bool(is_ps4_xmb or is_loading_black or is_static_pause)
                else:
                    is_boring[idx] = bool(is_ps4_xmb or is_loading_black)

                prev_gray = gray
                prev_hist = hist
                prev_luma = luma
                idx += 1

                if progress_cb and idx % report_every == 0:
                    pct = 15 + int((idx / bins) * 42)
                    if cpu_guard:
                        cpu_guard.check_and_pause(cancel_check=cancel_check, progress_cb=progress_cb, current_pct=min(57, pct))
                    progress_cb(
                        min(57, pct),
                        f"Etapa B (Detalhada): Lendo video completo ({format_timestamp(idx * step)} / {format_timestamp(duration)})...",
                    )

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


def _detect_all_peaks_across_timeline(
    smoothed: np.ndarray,
    dialogue_curve: np.ndarray,
    boring_mask: np.ndarray,
    duration: float,
    step: float,
    min_gap_sec: float,
    sensitivity: float,
) -> list[int]:
    """Find ALL distinct action, explosion, death, AND cutscene/dialogue moments across 100% of timeline."""
    bins = len(smoothed)
    if bins == 0:
        return []

    min_gap_bins = max(4, int(round(min_gap_sec / step)))
    non_boring_vals = smoothed[~boring_mask] if np.any(~boring_mask) else smoothed
    median_s = float(np.median(non_boring_vals))
    std_s = float(np.std(non_boring_vals))
    global_thresh = max(0.12, median_s + std_s * (0.72 - sensitivity * 0.55))

    candidate_set: set[int] = set()

    # 1. Global action + dialogue maxima
    for i in range(1, max(2, bins - 1)):
        if boring_mask[i]:
            continue
        if smoothed[i] >= smoothed[i - 1] and smoothed[i] >= smoothed[min(bins - 1, i + 1)] and smoothed[i] >= global_thresh:
            candidate_set.add(i)

    # 2. Dialogue / Cutscene peaks (intense speech + scene transitions even without loud explosions!)
    if bins >= 8:
        d_thresh = max(0.32, float(np.percentile(dialogue_curve, 82)))
        for i in range(2, bins - 2):
            if boring_mask[i]:
                continue
            if dialogue_curve[i] >= d_thresh and dialogue_curve[i] >= dialogue_curve[i - 1] and dialogue_curve[i] >= dialogue_curve[i + 1]:
                candidate_set.add(i)

    # 3. Regional sliding-window sweep across every 80s slice (guarantees 00:00 -> 100% end coverage)
    if duration >= 60.0:
        win_bins = max(min_gap_bins, int(round(80.0 / step)))
        for ws in range(0, bins, win_bins):
            we = min(bins, ws + win_bins)
            sub = smoothed[ws:we].copy()
            sub[boring_mask[ws:we]] = -1.0
            if sub.size == 0:
                continue
            best_local = int(np.argmax(sub))
            if sub[best_local] >= max(0.14, median_s * 0.88):
                candidate_set.add(ws + best_local)

    if not candidate_set:
        order = np.argsort(smoothed)[::-1]
        for idx in order:
            if not boring_mask[idx]:
                candidate_set.add(int(idx))
                if len(candidate_set) >= max(1, int(duration / max(20.0, min_gap_sec))):
                    break

    ordered = sorted(candidate_set, key=lambda i: float(smoothed[i]), reverse=True)
    selected: list[int] = []
    for idx in ordered:
        if all(abs(idx - existing) >= min_gap_bins for existing in selected):
            selected.append(idx)

    selected.sort(key=lambda i: float(smoothed[i]), reverse=True)
    return selected


def _expand_dynamic_scene_bounds(
    pk: int,
    bins: int,
    step: float,
    duration: float,
    combined: np.ndarray,
    motion: np.ndarray,
    audio_rms: np.ndarray,
    speech: np.ndarray,
    scene_cut: np.ndarray,
    boring_mask: np.ndarray,
    min_dur: float,
    max_dur: float,
    is_death: bool,
) -> tuple[float, float]:
    """Dynamically expand clip [start, end] between min_dur (25s) and max_dur (55s)
    following real ongoing combat, tension, or dialogue until a natural pause/scene end."""
    pk_sec = pk * step
    # Base core around peak (8s pre-tension, 8s payoff)
    s_bin = max(0, pk - int(round(8.0 / step)))
    e_bin = min(bins - 1, pk + int(round((9.0 if is_death else 8.0) / step)))

    # Activity signal: tracks genuine combat/audio/dialogue continuity (not just idle camera panning)
    active = (audio_rms > 0.28) | (speech > 0.28) | (combined > 0.36) | ((motion > 0.42) & (audio_rms > 0.16))
    active = active & (~boring_mask)

    max_pre_sec = min(42.0, max(20.0, max_dur * 0.45))
    max_post_sec = min(52.0, max(32.0, max_dur * 0.58))
    max_pre_bins = int(round(max_pre_sec / step))
    max_post_bins = int(round(max_post_sec / step))
    quiet_stop_bins = max(3, int(round(2.0 / step)))  # 2.0s of natural calm ends the scene

    # 1. Expand BACKWARD while ongoing combat/dialogue continues (up to 20s before peak)
    quiet_run = 0
    while s_bin > 0 and (pk - s_bin) < max_pre_bins and ((e_bin - s_bin) * step) < max_dur:
        prev_b = s_bin - 1
        if scene_cut[s_bin] > 0.58 and not active[prev_b]:
            break
        if active[prev_b]:
            s_bin = prev_b
            quiet_run = 0
        else:
            quiet_run += 1
            if quiet_run >= quiet_stop_bins:
                break
            s_bin = prev_b

    # 2. Expand FORWARD while fight, boss battle, execution, or dialogue continues (up to max_dur 55s!)
    quiet_run = 0
    while e_bin < (bins - 1) and (e_bin - pk) < max_post_bins and ((e_bin - s_bin) * step) < max_dur:
        next_b = e_bin + 1
        if scene_cut[next_b] > 0.62 and not active[next_b] and ((e_bin - s_bin) * step) >= min_dur:
            break
        if active[next_b]:
            e_bin = next_b
            quiet_run = 0
        else:
            quiet_run += 1
            if quiet_run >= quiet_stop_bins:
                # Keep 1s breath after dialogue/fight finishes
                break
            e_bin = next_b

    start_sec = max(0.0, s_bin * step)
    end_sec = min(duration, (e_bin + 1) * step)

    # 3. Guarantee minimum complete-scene duration (25s, or full video if source < 25s)
    effective_min = min(duration, min_dur)
    if (end_sec - start_sec) < effective_min:
        deficit = effective_min - (end_sec - start_sec)
        # Prefer adding slightly more post-context so payoff/dialogue finishes cleanly
        add_pre = deficit * 0.45
        add_post = deficit * 0.55
        start_sec = max(0.0, start_sec - add_pre)
        end_sec = min(duration, end_sec + add_post)
        if (end_sec - start_sec) < effective_min:
            rem = effective_min - (end_sec - start_sec)
            if start_sec <= 0.05:
                end_sec = min(duration, end_sec + rem)
            elif end_sec >= duration - 0.05:
                start_sec = max(0.0, start_sec - rem)

    # 4. Enforce max_dur (55s) cleanly centered around peak
    if (end_sec - start_sec) > max_dur:
        end_sec = min(duration, max(pk_sec + 10.0, start_sec + max_dur))
        start_sec = max(0.0, end_sec - max_dur)

    return round(start_sec, 2), round(end_sec, 2)


def analyze_gameplay(
    video_path: str,
    profile_id: str = "generico",
    project_id: str = "",
    progress_cb: Optional[Callable[[int, str], None]] = None,
    vision_limit: int = 3,
    cancel_check: Optional[Callable[[], bool]] = None,
    target_mode: str = "hibrido",
    scene_mode: Optional[str] = None,
    game_sheet: Optional[dict[str, Any]] = None,
    game_name: str = "",
) -> dict[str, Any]:
    """Run full 2-stage Ryzen-friendly multimodal analysis across 100% of timeline:
    - Stage A: Lightweight audio/spectrum scan across entire timeline
    - Stage B: Targeted Optical Flow (320px @ 5fps) ONLY on candidate peak windows (or full timeline if detailed_mode)
    - Smart Multi-Platform Target Mode:
      * 'hibrido': explosive bursts 20-32s ('Ideal TikTok / Reels') vs sustained scenes 36-55s ('Ideal YouTube Shorts')
      * 'tiktok_reels': forces 20-32s ('Ideal TikTok / Reels')
      * 'tiktok_long': forces monetizable 61-90s ('TikTok Monetizavel (61s-90s)')
      * 'youtube_shorts': forces 36-55s ('Ideal YouTube Shorts')
    """
    settings = load_settings()
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    if target_mode not in ("hibrido", "tiktok_reels", "tiktok_long", "youtube_shorts"):
        target_mode = "hibrido"

    detailed_mode = bool(settings.get("analysis_detailed_mode", False))
    cpu_thresh = float(settings.get("cpu_pause_threshold_pct", 80))
    cpu_guard = CPUGuard(threshold_pct=cpu_thresh, sustain_sec=5.0)

    tk_short_min = float(settings.get("tiktok_short_min_sec", 20))
    tk_short_max = float(settings.get("tiktok_short_max_sec", 32))
    tk_long_min = float(settings.get("tiktok_long_min_sec", 61))
    tk_long_max = float(settings.get("tiktok_long_max_sec", 90))
    yt_min = float(settings.get("youtube_shorts_min_sec", 36))
    yt_max = float(settings.get("youtube_shorts_max_sec", 55))

    if progress_cb:
        progress_cb(4, "Etapa 1/4: Lendo video e metadados via FFprobe...")
    meta = probe_video(video_path)
    duration = meta["duration"]
    if duration < 2.0:
        raise XRayError(
            "O video e muito curto (menos de 2 segundos) para analise de gameplay.",
            category="ANALISE",
            status_code=400,
        )

    step = 1.0 if duration > 1200.0 else 0.5

    if progress_cb:
        progress_cb(10, f"Etapa A (Audio/Espectro): Varrendo linha do tempo inteira ({meta['duration_str']})...")
    audio_rms, audio_peak, speech = _extract_audio_timeline(
        video_path, duration, step=step, cancel_check=cancel_check, cpu_guard=cpu_guard, progress_cb=progress_cb
    )

    if detailed_mode:
        if progress_cb:
            progress_cb(16, "Etapa B (Modo Detalhado): Analisando video completo...")
        vis = _extract_visual_timeline(
            video_path, duration, step=step, progress_cb=progress_cb, cancel_check=cancel_check, cpu_guard=cpu_guard
        )
        scan_strategy = "detailed_full_timeline"
    else:
        cand_windows = _select_audio_candidate_windows(audio_rms, audio_peak, speech, duration, step=step)
        if progress_cb:
            progress_cb(16, f"Etapa B (Leve 320px@5fps): Optical Flow em {len(cand_windows)} janelas candidatas...")
        vis = _extract_visual_candidate_windows(
            video_path,
            duration,
            windows=cand_windows,
            step=step,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
            cpu_guard=cpu_guard,
        )
        scan_strategy = f"two_stage_lightweight_320px_5fps ({len(cand_windows)} windows)"

    bins = min(len(audio_rms), len(vis["motion"]))
    audio_rms = audio_rms[:bins]
    audio_peak = audio_peak[:bins]
    speech = speech[:bins]
    motion = vis["motion"][:bins]
    scene_cut = vis["scene_cut"][:bins]
    red_flash = vis["red_flash"][:bins]
    death_sig = vis["death_sig"][:bins]
    is_boring = vis["is_boring"][:bins]
    sharpness = vis["sharpness"][:bins]

    # Quiet & boring mask: never mark active dialogue/cutscene speech as boring!
    quiet_audio = (audio_rms < 0.14) & (audio_peak < 0.20) & (speech < 0.22)
    raw_boring = is_boring & quiet_audio
    boring_mask = np.zeros(bins, dtype=np.bool_)
    run_len = 0
    min_boring_bins = max(3, int(3.0 / step))
    for i in range(bins):
        if raw_boring[i]:
            run_len += 1
        else:
            if run_len >= min_boring_bins:
                boring_mask[i - run_len : i] = True
            run_len = 0
    if run_len >= min_boring_bins:
        boring_mask[bins - run_len : bins] = True

    # Combine Action/Gunfire/Explosions/Deaths WITH Configurable "Modo Cenas" (Anti-Tedio)
    eff_scene_mode = scene_mode or str(settings.get("scene_mode") or "keep_dialogue")
    if eff_scene_mode not in ("keep_dialogue", "prioritize_gameplay", "ignore_cutscenes"):
        eff_scene_mode = "keep_dialogue"

    w = profile["weights"]
    action_score = (
        w["audio_peak"] * audio_peak
        + w["audio_rms"] * audio_rms
        + w["motion"] * motion
        + w["scene_cut"] * scene_cut
        + w["red_flash"] * red_flash
        + w["death_signal"] * death_sig
    )
    dialogue_curve = np.clip(speech * 0.68 + scene_cut * 0.32, 0.0, 1.0)

    if eff_scene_mode == "ignore_cutscenes":
        calm_cutscene = (speech > 0.36) & (action_score < 0.34)
        boring_mask = boring_mask | calm_cutscene
        dialogue_curve = dialogue_curve * 0.12
        combined = action_score * 1.12
    elif eff_scene_mode == "prioritize_gameplay":
        dialogue_curve = dialogue_curve * 0.45
        combined = np.maximum(action_score * 1.15, dialogue_curve * 0.55)
    else:
        # keep_dialogue (default): preserve spoken cutscenes alongside combat
        combined = np.maximum(action_score, dialogue_curve * 0.82)

    combined[boring_mask] *= 0.08

    smooth_k = min(3, bins)
    smoothed = np.convolve(combined, np.ones(smooth_k) / smooth_k, mode="same") if smooth_k > 1 else combined

    if progress_cb:
        progress_cb(62, "Etapa 2/4: IA analisando os melhores momentos, lutas e dialogos completos...")

    sensitivity = float(settings.get("sensitivity", 0.55))
    min_dur = float(settings.get("default_clip_min_sec", 25))
    max_dur = float(settings.get("default_clip_max_sec", 55))
    min_gap_sec = max(26.0, float(profile.get("min_gap", 26.0)))
    if duration < 110.0:
        min_gap_sec = min(min_gap_sec, max(14.0, duration / 4.0))

    selected_peaks = _detect_all_peaks_across_timeline(
        smoothed=smoothed,
        dialogue_curve=dialogue_curve,
        boring_mask=boring_mask,
        duration=duration,
        step=step,
        min_gap_sec=min_gap_sec,
        sensitivity=sensitivity,
    )

    video_name = Path(video_path).stem
    proj_tag = project_id or uuid.uuid4().hex[:8]
    moments: list[dict[str, Any]] = []
    used_titles: list[str] = []
    used_hooks: list[str] = []
    total_peaks = max(1, len(selected_peaks))

    for rank_idx, pk in enumerate(selected_peaks):
        if cancel_check and cancel_check():
            raise InterruptedError("Processamento cancelado pelo usuario.")

        pk_sec = round(pk * step, 2)
        loc_slice = slice(max(0, pk - 4), min(bins, pk + 5))
        loc_death = float(np.max(death_sig[loc_slice]))
        loc_red = float(np.max(red_flash[loc_slice]))
        loc_peak = float(np.max(audio_peak[loc_slice]))
        loc_motion = float(np.max(motion[loc_slice]))
        loc_rms = float(np.mean(audio_rms[loc_slice]))
        loc_speech = float(np.mean(speech[loc_slice]))

        if loc_death >= 0.48 or (loc_red >= 0.58 and loc_death >= 0.30):
            h_cat = "morte_cinematografica"
        elif loc_peak >= 0.68 and loc_rms < 0.38:
            h_cat = "susto_tensao"
        elif loc_speech >= 0.52 and loc_peak < 0.55 and loc_motion < 0.50:
            h_cat = "dialogo_historia"
        elif loc_motion >= 0.68 and loc_red < 0.40:
            h_cat = "acrobacia_cinema"
        else:
            h_cat = "combate_intenso"

        is_death = h_cat == "morte_cinematografica"

        # Smart Multi-Platform Duration & Classification (Hibrido IA vs TikTok Curto vs TikTok 61-90s vs YouTube Shorts)
        if target_mode == "tiktok_reels":
            scene_platform = "tiktok_reels"
            scene_badge = "Ideal TikTok / Reels"
            start_sec, end_sec = _expand_dynamic_scene_bounds(
                pk=pk, bins=bins, step=step, duration=duration,
                combined=combined, motion=motion, audio_rms=audio_rms, speech=speech,
                scene_cut=scene_cut, boring_mask=boring_mask,
                min_dur=tk_short_min, max_dur=tk_short_max, is_death=is_death,
            )
        elif target_mode == "tiktok_long":
            scene_platform = "tiktok_long"
            scene_badge = f"TikTok Monetizavel ({int(tk_long_min)}s-{int(tk_long_max)}s)"
            start_sec, end_sec = _expand_dynamic_scene_bounds(
                pk=pk, bins=bins, step=step, duration=duration,
                combined=combined, motion=motion, audio_rms=audio_rms, speech=speech,
                scene_cut=scene_cut, boring_mask=boring_mask,
                min_dur=tk_long_min, max_dur=tk_long_max, is_death=is_death,
            )
        elif target_mode == "youtube_shorts":
            scene_platform = "youtube_shorts"
            scene_badge = "Ideal YouTube Shorts"
            start_sec, end_sec = _expand_dynamic_scene_bounds(
                pk=pk, bins=bins, step=step, duration=duration,
                combined=combined, motion=motion, audio_rms=audio_rms, speech=speech,
                scene_cut=scene_cut, boring_mask=boring_mask,
                min_dur=yt_min, max_dur=yt_max, is_death=is_death,
            )
        else:
            # Modo Hibrido IA: inspect true core intensity (audio/speech/combat bursts) around peak
            wide_slice = slice(max(0, pk - int(18.0 / step)), min(bins, pk + int(22.0 / step)))
            core_active = (
                (audio_rms[wide_slice] > 0.34)
                | (speech[wide_slice] > 0.34)
                | (combined[wide_slice] > 0.46)
            )
            active_core_sec = float(np.sum(core_active)) * step
            speech_core_sec = float(np.sum(speech[wide_slice] > 0.34)) * step

            is_long_narrative = (
                (h_cat == "dialogo_historia" and speech_core_sec >= 14.0)
                or (active_core_sec >= 20.0)
            )
            if is_long_narrative and duration >= yt_min:
                scene_platform = "youtube_shorts"
                scene_badge = "Ideal YouTube Shorts"
                start_sec, end_sec = _expand_dynamic_scene_bounds(
                    pk=pk, bins=bins, step=step, duration=duration,
                    combined=combined, motion=motion, audio_rms=audio_rms, speech=speech,
                    scene_cut=scene_cut, boring_mask=boring_mask,
                    min_dur=yt_min, max_dur=yt_max, is_death=is_death,
                )
            else:
                scene_platform = "tiktok_reels"
                scene_badge = "Ideal TikTok / Reels"
                start_sec, end_sec = _expand_dynamic_scene_bounds(
                    pk=pk, bins=bins, step=step, duration=duration,
                    combined=combined, motion=motion, audio_rms=audio_rms, speech=speech,
                    scene_cut=scene_cut, boring_mask=boring_mask,
                    min_dur=tk_short_min, max_dur=tk_short_max, is_death=is_death,
                )

        c_start_b = max(0, int(start_sec / step))
        c_end_b = min(bins, int(end_sec / step))
        main_peak_rel = round(max(2.0, min((end_sec - start_sec) - 2.0, pk_sec - start_sec)), 2)
        impact_times: list[float] = [main_peak_rel]
        if c_end_b - c_start_b > 10:
            sub = audio_peak[c_start_b:c_end_b] * 0.65 + motion[c_start_b:c_end_b] * 0.35
            for ti in np.argsort(sub)[::-1]:
                rel_t = round(ti * step, 2)
                if 2.2 < rel_t < (end_sec - start_sec - 2.0):
                    if all(abs(rel_t - ex) >= 4.5 for ex in impact_times):
                        impact_times.append(rel_t)
                        if len(impact_times) >= 3:
                            break
        impact_times.sort()

        thumb_path = THUMBS_DIR / f"{proj_tag}_m{rank_idx + 1}_peak.jpg"
        after_path = THUMBS_DIR / f"{proj_tag}_m{rank_idx + 1}_after.jpg"
        saved_thumb = _save_frame_jpeg(video_path, pk_sec, thumb_path, width=640)
        after_sec = min(max(0.2, duration - 0.3), pk_sec + 3.0)
        saved_after = _save_frame_jpeg(video_path, after_sec, after_path, width=480)
        vision_frames = [p for p in (saved_thumb, saved_after) if p]

        base_score = int(round(min(99, max(54, 56 + float(smoothed[pk]) * 43))))
        signals_summary = (
            f"audio_pico={loc_peak:.2f}, fala={loc_speech:.2f}, movimento={loc_motion:.2f}, "
            f"vermelho={loc_red:.2f}, sinal_morte={loc_death:.2f}, tempo={format_timestamp(pk_sec)}"
        )

        if rank_idx < vision_limit:
            if progress_cb:
                pct = 64 + int(((rank_idx + 1) / min(total_peaks, vision_limit)) * 30)
                progress_cb(
                    min(94, pct),
                    f"Etapa 2/4: IA analisando momento #{rank_idx + 1} de {total_peaks} ({format_timestamp(pk_sec)})...",
                )
            ai_info = analyze_moment_with_qwen(
                frame_paths=vision_frames,
                profile_id=profile_id,
                heuristic_category=h_cat if h_cat != "dialogo_historia" else "acrobacia_cinema",
                heuristic_score=base_score,
                signals_summary=signals_summary,
                video_name=video_name,
                used_titles=used_titles,
                used_hooks=used_hooks,
                clip_index=rank_idx,
                platform_target=scene_platform,
                game_sheet=game_sheet,
                game_name=game_name,
            )
            if h_cat == "dialogo_historia" and not ai_info.get("is_player_death"):
                ai_info["category"] = "dialogo_historia"
        else:
            ai_info = _pick_unique_fallback(
                profile=profile,
                category=h_cat,
                clip_index=rank_idx,
                used_titles=used_titles,
                used_hooks=used_hooks,
                platform_target=scene_platform,
                game_sheet=game_sheet,
                game_name=game_name,
            )

        if ai_info.get("is_menu_or_boring") and len(selected_peaks) > 1:
            continue

        used_titles.append(ai_info["title"])
        used_hooks.append(ai_info["hook_2s"])

        if ai_info.get("is_player_death") and not is_death:
            is_death = True

        # P2: Learned channel performance boost (automatically adapts ranking weights)
        from backend.analytics import get_learned_ranking_boost

        clip_dur = round(end_sec - start_sec, 2)
        learned_boost = get_learned_ranking_boost(
            category=ai_info["category"],
            duration_sec=clip_dur,
            game_name=ai_info.get("detected_game", game_name or profile["short_name"]),
        )
        final_score = max(45, min(99, base_score + int(ai_info.get("score_boost", 0)) + learned_boost))

        # P2: Copyright Risk Flagging (long cutscenes or sustained licensed background music bed)
        win_rms = audio_rms[c_start_b:c_end_b] if c_end_b > c_start_b else np.zeros(1)
        win_peak = audio_peak[c_start_b:c_end_b] if c_end_b > c_start_b else np.zeros(1)
        win_speech = speech[c_start_b:c_end_b] if c_end_b > c_start_b else np.zeros(1)
        speech_sec_in_clip = float(np.sum(win_speech > 0.34)) * step
        sustained_music_ratio = float(np.mean((win_rms > 0.36) & (win_peak < 0.62))) if win_rms.size else 0.0
        cr_reasons: list[str] = []
        if (ai_info["category"] == "dialogo_historia" and clip_dur >= 32.0) or speech_sec_in_clip >= 16.0:
            cr_reasons.append("Cutscene longa com dialogo continuo")
        if sustained_music_ratio >= 0.62 and clip_dur >= 20.0:
            cr_reasons.append("Possivel trilha musical licenciada continua")
        cr_level = "high" if len(cr_reasons) >= 2 else ("medium" if len(cr_reasons) == 1 else "low")
        copyright_risk = {
            "level": cr_level,
            "flagged": cr_level != "low",
            "reasons": cr_reasons,
            "notice_pt": (
                "Aviso de Direitos Autorais: " + " + ".join(cr_reasons) + ". Confira a politica de monetizacao do estudio."
                if cr_reasons
                else ""
            ),
        }

        moments.append(
            {
                "id": f"mom_{uuid.uuid4().hex[:8]}",
                "rank": 0,
                "start": round(start_sec, 2),
                "end": round(end_sec, 2),
                "peak": pk_sec,
                "duration": clip_dur,
                "start_str": format_timestamp(start_sec),
                "end_str": format_timestamp(end_sec),
                "score": final_score,
                "learned_boost": learned_boost,
                "category": ai_info["category"],
                "category_label": _category_label(ai_info["category"]),
                "platform_target": scene_platform,
                "platform_badge": scene_badge,
                "copyright_risk": copyright_risk,
                "is_player_death": is_death,
                "impact_times": impact_times,
                "climax_rel": main_peak_rel,
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
                "after_thumb_path": saved_after or "",
            }
        )

    moments.sort(key=lambda m: m["score"], reverse=True)
    for i, m in enumerate(moments, start=1):
        m["rank"] = i

    annotate_moments_export_status(video_path, moments, duration=duration)

    keep_segments, removed_sec = _build_long_edit_segments(boring_mask, combined, duration, step=step)

    thumb_scores = combined * 0.65 + sharpness * 0.35
    thumb_scores[boring_mask] = 0.0
    best_thumb_bin = int(np.argmax(thumb_scores)) if bins > 0 else 0
    best_thumb_sec = round(best_thumb_bin * step, 2)
    yt_thumb_path = THUMBS_DIR / f"{proj_tag}_youtube_thumb.jpg"
    _save_frame_jpeg(video_path, best_thumb_sec, yt_thumb_path, width=1920)

    chapters = _build_youtube_chapters(keep_segments, moments)

    unseen_count = sum(1 for m in moments if not m.get("already_exported"))
    if progress_cb:
        progress_cb(
            100,
            f"Etapa 4/4: Analise concluida! {len(moments)} cenas completas encontradas ({unseen_count} ineditas).",
        )

    boring_total_sec = round(float(np.sum(boring_mask)) * step, 1)
    return {
        "meta": meta,
        "profile_id": profile_id,
        "profile_name": profile["name"],
        "moments": moments,
        "total_moments": len(moments),
        "unseen_moments": unseen_count,
        "boring_skipped_sec": boring_total_sec,
        "scan_strategy": scan_strategy,
        "cpu_pauses": cpu_guard.pause_count,
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
        "dialogo_historia": "Dialogo / Cutscene",
        "acrobacia_cinema": "Cinema & Acrobacia",
    }.get(cat, "Momento Epico")


def _build_long_edit_segments(
    boring_mask: np.ndarray,
    combined: np.ndarray,
    duration: float,
    step: float = 0.5,
) -> tuple[list[dict[str, float]], float]:
    bins = len(boring_mask)
    if bins == 0:
        return [{"start": 0.0, "end": round(duration, 2)}], 0.0

    dead = boring_mask | (combined < 0.07)
    min_cut_bins = max(3, int(5.0 / step))
    cut_mask = np.zeros(bins, dtype=np.bool_)
    run = 0
    for i in range(bins):
        if dead[i]:
            run += 1
        else:
            if run >= min_cut_bins:
                pad = max(1, int(1.0 / step))
                cut_mask[i - run + pad : max(i - run + pad, i - pad)] = True
            run = 0
    if run >= min_cut_bins:
        pad = max(1, int(1.0 / step))
        cut_mask[bins - run + pad : bins] = True

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
    for m in chronological[:20]:
        mapped = map_time(m["peak"])
        if mapped is None or mapped - last_ts < 25.0:
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
