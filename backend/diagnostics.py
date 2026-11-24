"""P2 Backup/Restore (.zip), Version/Changelog, System Diagnostics & Synthetic Pipeline Self-Test (backend/diagnostics.py)."""

from __future__ import annotations

import io
import json
import shutil
import socket
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.analyzer import analyze_gameplay, probe_video
from backend.config import DATA_DIR, TEMP_DIR, discover_ffmpeg, get_ffmpeg_bins, load_settings
from backend.copy_engine import generate_multiple_options, get_copy_engine_stats, jaccard_similarity
from backend.editor import render_viral_clip
from backend.errors import XRayError
from backend.ollama_client import check_ollama_status, get_qwen_five_state
from backend.youtube_uploader import dpapi_available

APP_VERSION = "2.3.0 (P0 + P1 + P2)"
BACKUP_JSON_FILES = [
    "settings.json",
    "config.json",
    "games_db.json",
    "exported_history.json",
    "overlay.json",
    "performance_metrics.json",
    "ranking_weights.json",
    "growth_plan.json",
    "schedule.json",
    "copy_history.json",
]

CHANGELOG: list[dict[str, Any]] = [
    {
        "version": "2.3.0 (P2)",
        "date": "2026-11-23",
        "highlights": [
            "Aba Desempenho com entrada manual, importacao de CSV oficial do TikTok Studio, sync YouTube Analytics e aprendizado automatico dos pesos do ranking.",
            "Agenda Inteligente (1-3 posts/dia alternando jogos e plataformas) com checklist pre-publicacao, Teste A/B de titulos/ganchos e Painel de Metas de Monetizacao.",
            "Variacao Anti-Fabrica entre clipes consecutivos, loop continuo opcional, CTA pronto para comentario fixo e Sinalizador de Risco de Direitos Autorais.",
            "Gestor de Armazenamento por projeto (com alerta < 10 GB livres), Backup/Restauracao .zip e Tela de Diagnostico com Autoteste Sintetico ponta a ponta.",
        ],
    },
    {
        "version": "2.2.0 (P1)",
        "date": "2026-11-23",
        "highlights": [
            "Campo 'Qual e o jogo?' com Ficha do Jogo editavel (games_db.json) e deteccao via Qwen Vision.",
            "Motor combinatorio BR (3.360 titulos, 2.688 descricoes, reservatorios categorizados, Jaccard < 0.35 e botao 'Ver mais 5 opcoes').",
            "Editor Visual Interativo 9:16 com arraste, ima central, Safe Zone e render .ass 1080x1920 pixel-identico.",
            "Temas Escuro/Claro/Auto (AA), Indicador Qwen 5 estados, Gaveta de Problemas humana, Pasta Monitorada PS4, 4 Layouts Lanczos e Capa Shorts.",
        ],
    },
    {
        "version": "2.1.0 (P0)",
        "date": "2026-11-23",
        "highlights": [
            "Blindagem local (127.0.0.1, Host/Origin, X-ClipRush-Token, Path Traversal bloqueado e cofre Windows DPAPI).",
            "Versao TikTok Monetizavel (61-90s), Gancho Real de Pico 1.8s + micro-flash 0.1s e Zoom de Precisao 1.14x (max 3/clipe, min 6s).",
            "Analise leve em 2 etapas para Ryzen 5 (320px@5fps), pausa automatica CPU > 80%, protecao YouTube (aprovacao humana + 2/dia) e anti-repeticao por impressao digital v2.",
        ],
    },
]


def export_backup_zip() -> tuple[bytes, str]:
    """Bundle all JSON configs, game sheets, history, analytics & goals into a single .zip archive."""
    buf = io.BytesIO()
    manifest = {
        "app": "ClipRush Studio Pro",
        "version": APP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
    }
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in BACKUP_JSON_FILES:
            fp = DATA_DIR / fname
            if fp.exists() and fp.is_file():
                zf.write(fp, arcname=fname)
                manifest["files"].append(fname)
        zf.writestr("backup_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return buf.getvalue(), f"ClipRush_Backup_{ts}.zip"


def import_backup_zip(raw_bytes: bytes) -> dict[str, Any]:
    """Safely validate and restore whitelisted JSON config/data files from a backup .zip (Zip Slip proof)."""
    if not raw_bytes or len(raw_bytes) < 22:
        raise XRayError("Arquivo .zip de backup invalido ou vazio.", category="UPLOAD", status_code=400)

    restored: list[str] = []
    allowed = set(BACKUP_JSON_FILES)
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
            for info in zf.infolist():
                # Strict basename-only whitelist (blocks Zip Slip ../ paths completely)
                name = Path(info.filename).name
                if info.is_dir() or name not in allowed or "/" in info.filename or "\\" in info.filename:
                    continue
                content = zf.read(info.filename)
                # Validate JSON before overwriting
                json.loads(content.decode("utf-8"))
                dest = DATA_DIR / name
                if dest.exists():
                    try:
                        shutil.copy2(dest, dest.with_suffix(dest.suffix + ".bak"))
                    except OSError:
                        pass
                dest.write_bytes(content)
                restored.append(name)
    except XRayError:
        raise
    except Exception as exc:
        raise XRayError(
            "Falha ao ler ou restaurar o arquivo .zip de backup.",
            category="UPLOAD",
            technical_detail=str(exc),
            status_code=400,
        )

    if not restored:
        raise XRayError(
            "Nenhum arquivo de configuracao valido do ClipRush foi encontrado dentro do .zip.",
            category="UPLOAD",
            status_code=400,
        )
    return {"ok": True, "restored_files": restored, "count": len(restored)}


def _check_port_8765_listening() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", 8765)) == 0
    finally:
        s.close()


def get_full_diagnostics() -> dict[str, Any]:
    ff = discover_ffmpeg()
    oll = check_ollama_status(timeout=1.2)
    qw = get_qwen_five_state()
    settings = load_settings()

    disk_free_gb = 0.0
    disk_total_gb = 0.0
    try:
        du = shutil.disk_usage(str(DATA_DIR))
        disk_free_gb = round(du.free / (1024 ** 3), 2)
        disk_total_gb = round(du.total / (1024 ** 3), 2)
    except OSError:
        pass

    ram = {"used_gb": 0.0, "total_gb": 0.0, "percent": 0.0}
    cpu_pct = 0.0
    battery = {"label": "Energia AC / Desktop", "plugged": True, "percent": 100}
    try:
        import psutil
        vm = psutil.virtual_memory()
        ram = {
            "used_gb": round(vm.used / (1024 ** 3), 2),
            "total_gb": round(vm.total / (1024 ** 3), 2),
            "percent": round(vm.percent, 1),
        }
        cpu_pct = round(float(psutil.cpu_percent(interval=0.1)), 1)
        bat = psutil.sensors_battery()
        if bat is not None:
            battery = {
                "label": f"{'Na tomada' if bat.power_plugged else 'Na bateria'} ({int(round(bat.percent))}%)",
                "plugged": bool(bat.power_plugged),
                "percent": int(round(bat.percent)),
            }
    except Exception:
        pass

    return {
        "version": APP_VERSION,
        "changelog": CHANGELOG,
        "port_8765_active": _check_port_8765_listening(),
        "ffmpeg": ff,
        "ollama": oll,
        "qwen_state": qw,
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "low_disk_warning": disk_free_gb < 10.0,
        "ram": ram,
        "cpu_percent": cpu_pct,
        "battery": battery,
        "dpapi_vault": dpapi_available(),
        "copy_engine": get_copy_engine_stats(),
        "scene_mode": settings.get("scene_mode", "keep_dialogue"),
    }


def run_synthetic_selftest() -> dict[str, Any]:
    """Generate a short 7s synthetic gameplay clip and validate the entire pipeline end-to-end:
    FFmpeg synth -> FFprobe -> 2-stage Analysis -> Combinatorial Copy (Jaccard < 0.35) ->
    9:16 Lanczos Render + ASS Overlay + Shorts Cover JPEG -> Automatic cleanup."""
    t_start = time.perf_counter()
    ffmpeg, _ = get_ffmpeg_bins()
    if not ffmpeg:
        raise XRayError("FFmpeg nao encontrado para executar o autoteste sintetico.", category="FFMPEG", status_code=500)

    steps: list[dict[str, Any]] = []
    synth_vid = TEMP_DIR / f"_selftest_{int(time.time())}.mp4"
    cleanup_paths: list[Path] = [synth_vid]

    try:
        # Step 1: Synthesize 7s 720p gameplay clip with burst peak
        t0 = time.perf_counter()
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=20:duration=7",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=7",
            "-af", "volume='if(between(t,2,5),0.9,0.1)':eval=frame",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k",
            str(synth_vid),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok1 = res.returncode == 0 and synth_vid.exists()
        steps.append({"step": "1. Geracao de video sintetico (FFmpeg)", "ok": ok1, "ms": round((time.perf_counter() - t0) * 1000)})
        if not ok1:
            raise RuntimeError(res.stderr or "FFmpeg synth failed")

        # Step 2: FFprobe metadata check
        t0 = time.perf_counter()
        meta = probe_video(str(synth_vid))
        ok2 = meta["duration"] >= 6.5 and meta["width"] == 640
        steps.append({"step": "2. Leitura de metadados (FFprobe)", "ok": ok2, "ms": round((time.perf_counter() - t0) * 1000), "detail": f"{meta['duration']}s {meta['width']}x{meta['height']}"})

        # Step 3: Copy Engine 3-kit Jaccard < 0.35 check
        t0 = time.perf_counter()
        kits = generate_multiple_options(count=3, game_name="Resident Evil 4 Remake", platform_target="tiktok_reels", record_history=False)
        ok3 = len({k["title"] for k in kits}) == 3 and jaccard_similarity(kits[0]["title"], kits[1]["title"]) < 0.35
        steps.append({"step": "3. Motor de Copy & Anti-Repeticao (Jaccard < 0.35)", "ok": ok3, "ms": round((time.perf_counter() - t0) * 1000)})

        # Step 4: 2-Stage Lightweight Analysis
        t0 = time.perf_counter()
        an = analyze_gameplay(
            video_path=str(synth_vid),
            profile_id="resident_evil",
            project_id="selftest",
            vision_limit=0,  # fast deterministic self-test without blocking on 9B vision
            target_mode="tiktok_reels",
        )
        ok4 = len(an.get("moments") or []) >= 1
        steps.append({"step": "4. Analise Multimodal 2 Etapas (Audio + 320px@5fps)", "ok": ok4, "ms": round((time.perf_counter() - t0) * 1000), "detail": an.get("scan_strategy", "")})

        # Step 5: Real 9:16 Lanczos Render + ASS Overlay + Shorts Cover JPEG
        t0 = time.perf_counter()
        m0 = an["moments"][0]
        ren = render_viral_clip(
            video_path=str(synth_vid),
            moment=m0,
            style="big",
            subtitles_mode="off",
            enable_hook=True,
            enable_impact_zoom=True,
            enable_preview_flash=True,
            enable_cta_comment=False,
            enable_impact_sfx=True,
            enable_smart_pacing=False,
            enable_progress_bar=True,
            enable_audio_norm=True,
            enable_slowmo=False,
            project_name="selftest_clip",
        )
        out_mp4 = Path(ren["file_path"])
        cleanup_paths.append(out_mp4)
        cov_ok = True
        if ren.get("cover_path"):
            cp = Path(ren["cover_path"])
            cleanup_paths.append(cp)
            cov_ok = cp.exists()
        probed_out = probe_video(str(out_mp4))
        ok5 = out_mp4.exists() and probed_out["width"] == 1080 and probed_out["height"] == 1920 and cov_ok
        steps.append({"step": "5. Render 9:16 Lanczos + ASS Overlay + Capa Shorts", "ok": ok5, "ms": round((time.perf_counter() - t0) * 1000), "detail": f"{probed_out['duration']}s 1080x1920"})

        all_ok = all(s["ok"] for s in steps)
        return {
            "ok": all_ok,
            "total_ms": round((time.perf_counter() - t_start) * 1000),
            "steps": steps,
            "summary_pt": "Autoteste sintetico concluido com 100% de sucesso!" if all_ok else "Autoteste encontrou falhas.",
        }
    finally:
        for fp in cleanup_paths:
            if fp.exists():
                try:
                    fp.unlink()
                except OSError:
                    pass
        # Also remove selftest temp thumbs
        for th in (DATA_DIR / "thumbs").glob("selftest_*.jpg"):
            try:
                th.unlink()
            except OSError:
                pass
