"""Hardware Protection & Ryzen 5 2500U Resource Guard for ClipRush Studio Pro.
- Enforces Windows BELOW_NORMAL_PRIORITY_CLASS for heavy workers & FFmpeg.
- Monitors real CPU load via psutil and automatically pauses workers if CPU > 80% for > 5 seconds.
- Provides realistic processing time estimates calibrated for Ryzen 5 2500U (16GB RAM, no dGPU).
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

try:
    import psutil
except Exception:
    psutil = None  # type: ignore[assignment]

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def set_below_normal_priority() -> None:
    """Lower current process priority to BELOW_NORMAL on Windows so desktop & UI never stutter."""
    if os.name != "nt":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


class CPUGuard:
    """Watches system CPU via psutil and pauses execution whenever CPU stays > threshold for > sustain_sec."""

    def __init__(
        self,
        threshold_pct: float = 80.0,
        sustain_sec: float = 5.0,
        resume_pct: float = 72.0,
        max_pause_sec: float = 20.0,
    ) -> None:
        self.threshold_pct = float(threshold_pct)
        self.sustain_sec = float(sustain_sec)
        self.resume_pct = float(min(resume_pct, threshold_pct - 5.0))
        self.max_pause_sec = float(max_pause_sec)
        self._high_since: Optional[float] = None
        self._last_check: float = 0.0
        self.pause_count: int = 0
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

    def current_cpu_pct(self) -> float:
        if psutil is None:
            return 0.0
        try:
            return float(psutil.cpu_percent(interval=None))
        except Exception:
            return 0.0

    def check_and_pause(
        self,
        cancel_check: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        current_pct: int = 50,
    ) -> bool:
        """Check CPU usage; if > threshold_pct for > sustain_sec, pause cleanly until cooled down.
        Returns True if a thermal/CPU pause occurred."""
        if psutil is None:
            return False

        now = time.monotonic()
        if (now - self._last_check) < 0.8:
            return False
        self._last_check = now

        cpu = self.current_cpu_pct()
        if cpu > self.threshold_pct:
            if self._high_since is None:
                self._high_since = now
                return False
            if (now - self._high_since) < self.sustain_sec:
                return False

            # Sustained > threshold_pct for > sustain_sec -> pause worker briefly
            self.pause_count += 1
            if progress_cb:
                progress_cb(
                    current_pct,
                    f"CPU em {int(round(cpu))}% (> {int(self.threshold_pct)}%) — pausando brevemente para proteger seu processador...",
                )
            pause_start = time.monotonic()
            while (time.monotonic() - pause_start) < self.max_pause_sec:
                if cancel_check and cancel_check():
                    raise InterruptedError("Processamento cancelado pelo usuario.")
                time.sleep(0.75)
                cur = self.current_cpu_pct()
                if cur <= self.resume_pct:
                    break
            self._high_since = None
            return True

        self._high_since = None
        return False


def estimate_processing_time(
    duration_sec: float,
    detailed_mode: bool = False,
    use_vision: bool = True,
    clip_count: int = 5,
) -> dict[str, Any]:
    """Return realistic Ryzen 5 2500U (4C/8T, 16GB RAM, CPU x264) time estimates."""
    dur = max(1.0, float(duration_sec))
    mins = dur / 60.0

    # Stage A audio PCM scan (~1.2s per minute of source video)
    audio_sec = max(2.0, mins * 1.2)
    # Stage B Optical Flow: lightweight 2-stage (~1.8s/min) vs full detailed (~11s/min)
    visual_sec = max(3.0, mins * (11.0 if detailed_mode else 1.8))
    # Qwen Vision (top 3 scenes with cache miss ~8-15s each on Ryzen/RAM)
    vision_sec = 22.0 if use_vision else 0.5

    analyze_est = int(round(audio_sec + visual_sec + vision_sec))
    analyze_min = max(4, int(round(analyze_est * 0.75)))
    analyze_max = max(analyze_min + 4, int(round(analyze_est * 1.30)))

    # Per 9:16 clip render on Ryzen 5 2500U libx264 veryfast (~18-28s per 30s clip + Whisper ~5s)
    per_clip_sec = 24
    batch_render_sec = max(15, clip_count * per_clip_sec)

    def _fmt(sec: int) -> str:
        if sec < 60:
            return f"{sec}s"
        m = sec // 60
        s = sec % 60
        return f"{m}min {s}s" if s >= 10 else f"{m}min"

    mode_label = "Modo Detalhado (Timeline Completa)" if detailed_mode else "Modo Leve 2 Etapas (Ryzen 5)"
    return {
        "mode_label": mode_label,
        "analyze_min_sec": analyze_min,
        "analyze_max_sec": analyze_max,
        "analyze_label": f"{_fmt(analyze_min)} a {_fmt(analyze_max)}",
        "render_per_clip_sec": per_clip_sec,
        "render_batch_label": f"~{_fmt(batch_render_sec)} ({clip_count} clipes)",
        "summary_pt": (
            f"Estimativa ({mode_label}): Analise {_fmt(analyze_min)}–{_fmt(analyze_max)} • "
            f"Renderizacao ~{per_clip_sec}s/clipe (1 tarefa pesada por vez, limite CPU 80%)."
        ),
    }
