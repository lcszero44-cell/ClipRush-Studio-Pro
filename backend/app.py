"""ClipRush Studio Pro - FastAPI Backend Server."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.analytics import (
    add_performance_record,
    delete_performance_record,
    get_analytics_dashboard,
    import_tiktok_studio_csv,
    sync_youtube_analytics,
)
from backend.analyzer import analyze_gameplay, probe_video
from backend.config import (
    ASSETS_DIR,
    DATA_DIR,
    FRONTEND_DIR,
    GAME_PROFILES,
    THUMBS_DIR,
    UPLOADS_DIR,
    detect_game_profile_from_name,
    discover_ffmpeg,
    get_output_dir,
    load_settings,
    save_settings,
)
from backend.editor import (
    SUBTITLE_STYLES,
    create_tiktok_long_version,
    create_tiktok_short_version,
    extract_clip_frame,
    render_long_gameplay,
    render_viral_clip,
)
from backend.copy_engine import generate_multiple_options, get_copy_engine_stats
from backend.diagnostics import (
    APP_VERSION,
    export_backup_zip,
    get_full_diagnostics,
    import_backup_zip,
    run_synthetic_selftest,
)
from backend.errors import XRayError, build_xray_from_exception
from backend.hw_guard import CPUGuard, estimate_processing_time
from backend.scheduler import (
    create_ab_test,
    generate_smart_agenda,
    get_growth_state,
    record_ab_winner,
    update_goals,
    update_slot_checklist,
)
from backend.ollama_client import (
    analyze_moment_with_qwen,
    calibrate_platform_hashtags,
    check_ollama_status,
    detect_game_with_qwen,
    generate_youtube_long_kit,
    get_qwen_five_state,
    regenerate_viral_kit,
)
from backend.watcher import (
    get_watcher_status,
    mark_watch_item_status,
    scan_watch_folder_now,
    start_watcher_daemon,
    update_watcher_config,
)
from backend.youtube_uploader import (
    YOUTUBE_AUDIT_WARNING,
    approve_autopilot_item,
    disconnect_youtube,
    dpapi_available,
    enqueue_autopilot_clips,
    get_autopilot_state,
    get_youtube_connection_status,
    publish_autopilot_item,
    remove_autopilot_item,
    save_protected_client_secrets,
    start_autopilot_daemon,
    start_oauth_flow,
    update_autopilot_config,
)
from backend.storage import (
    add_clip,
    add_scheduled,
    annotate_moments_export_status,
    clean_disk_buckets,
    clear_video_export_history,
    delete_clip,
    delete_project_media,
    delete_scheduled,
    find_game_sheet,
    get_clip,
    get_project,
    get_project_storage_breakdown,
    list_clips,
    list_games,
    list_projects,
    list_scheduled,
    load_overlay_config,
    record_exported_interval,
    save_overlay_config,
    scan_disk_usage,
    update_clip,
    update_scheduled_status,
    upsert_game_sheet,
    upsert_project,
)
from backend.worker_pool import (
    cancel_isolated_job,
    exclusive_heavy_task,
    get_active_analysis_cut,
    get_job_state,
    submit_isolated_job,
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# P0 Local Security: Per-startup random session token injected ONLY into local GET /
APP_SESSION_TOKEN = secrets.token_urlsafe(32)
ALLOWED_HOSTS = {"127.0.0.1:8765", "localhost:8765", "127.0.0.1", "localhost"}
ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

app = FastAPI(title="ClipRush Studio Pro", version="2.2.0-P1")
start_autopilot_daemon()
start_watcher_daemon()


def _safe_resolve_under(base_dir: Path, rel_path: str) -> Path:
    """Strictly prevent Path Traversal using Path.resolve().is_relative_to(base_dir.resolve())."""
    if not rel_path or "\x00" in rel_path:
        raise HTTPException(status_code=403, detail="Caminho invalido bloqueado por seguranca.")
    raw_p = Path(rel_path)
    if raw_p.is_absolute():
        raise HTTPException(status_code=403, detail="Acesso a caminho absoluto bloqueado (Path Traversal).")
    base_resolved = base_dir.resolve()
    try:
        candidate = (base_resolved / rel_path).resolve()
    except OSError:
        raise HTTPException(status_code=403, detail="Caminho invalido bloqueado.")
    if not candidate.is_relative_to(base_resolved):
        raise HTTPException(status_code=403, detail="Tentativa de Path Traversal bloqueada.")
    return candidate


def _is_allowed_local_media_file(candidate: Path) -> bool:
    """Allow streaming/revealing ONLY files under permitted dirs OR registered project video paths."""
    try:
        res = candidate.resolve()
    except OSError:
        return False
    allowed_roots = (get_output_dir().resolve(), THUMBS_DIR.resolve(), UPLOADS_DIR.resolve())
    if any(res.is_relative_to(root) for root in allowed_roots):
        return True
    for proj in list_projects():
        vp = proj.get("video_path")
        if vp:
            try:
                if Path(vp).resolve() == res:
                    return True
            except OSError:
                pass
    return False


# ---- P0 Security + Mandatory Cache-Control: no-store Middleware ----

@app.middleware("http")
async def security_and_no_store_middleware(request: Request, call_next):
    # 1. Host header validation (DNS rebinding & LAN exposure guard)
    host = (request.headers.get("host") or "").strip().lower()
    if host not in ALLOWED_HOSTS:
        return JSONResponse(
            status_code=403,
            content={
                "category": "SEGURANCA",
                "explanation_pt": f"Host nao autorizado ({host}). O ClipRush Studio Pro aceita apenas conexoes locais em 127.0.0.1:8765.",
                "file": "backend/app.py",
                "line": 146,
                "technical_detail": f"Blocked invalid Host header: {host}",
                "ai_copy_text": f"Blocked Host: {host}",
            },
        )

    # 2. Origin header validation when present (blocks cross-origin browser requests)
    origin = (request.headers.get("origin") or "").strip().rstrip("/").lower()
    if origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse(
            status_code=403,
            content={
                "category": "SEGURANCA",
                "explanation_pt": f"Origem nao autorizada ({origin}). Requisicoes externas foram bloqueadas.",
                "file": "backend/app.py",
                "line": 160,
                "technical_detail": f"Blocked invalid Origin header: {origin}",
                "ai_copy_text": f"Blocked Origin: {origin}",
            },
        )

    # 3. Mandatory X-ClipRush-Token on ALL mutating endpoints (POST, PUT, PATCH, DELETE)
    if request.method.upper() in MUTATING_METHODS:
        provided_token = (request.headers.get("x-cliprush-token") or "").strip()
        if not provided_token or not secrets.compare_digest(provided_token, APP_SESSION_TOKEN):
            return JSONResponse(
                status_code=403,
                content={
                    "category": "SEGURANCA",
                    "explanation_pt": "Token de sessao local (X-ClipRush-Token) ausente ou invalido. Recarregue a pagina http://127.0.0.1:8765.",
                    "file": "backend/app.py",
                    "line": 175,
                    "technical_detail": f"Missing or invalid X-ClipRush-Token for {request.method} {request.url.path}",
                    "ai_copy_text": f"403 Missing X-ClipRush-Token on {request.method} {request.url.path}",
                },
            )

    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# ---- Global X-Ray Error Handlers ----

@app.exception_handler(XRayError)
async def xray_exception_handler(request: Request, exc: XRayError):
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    payload = build_xray_from_exception(exc, category="SISTEMA", context={"path": str(request.url.path)})
    return JSONResponse(status_code=500, content=payload)


# ---- Pydantic Schemas ----

class LocalImportRequest(BaseModel):
    file_path: str
    profile_id: Optional[str] = None


class BrowseLocalFileRequest(BaseModel):
    profile_id: Optional[str] = "auto"


class AnalyzeRequest(BaseModel):
    profile_id: str = "generico"
    target_mode: str = "hibrido"  # hibrido | tiktok_reels | tiktok_long | youtube_shorts
    scene_mode: Optional[str] = None  # keep_dialogue | prioritize_gameplay | ignore_cutscenes
    game_name: Optional[str] = None


class MomentPatchRequest(BaseModel):
    hook_2s: Optional[str] = None
    cta_comment: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    hashtags: Optional[list[str]] = None
    start: Optional[float] = None
    end: Optional[float] = None


class BatchRenderRequest(BaseModel):
    moment_ids: Optional[list[str]] = None
    limit: Optional[int] = None  # 5, 10, 20, or None for all
    skip_exported: bool = True
    style: str = "fit"  # fit | big | fill | auto | viral_blur | tela_cheia | clipe_limpo
    subtitles_mode: str = "whisper"  # whisper (default ON) | off
    subtitle_style: str = "amarelo_viral"  # amarelo_viral | branco_clean | destaque_keyword
    enable_hook: bool = True
    enable_impact_zoom: bool = True
    enable_preview_flash: bool = True
    enable_infinite_loop: bool = True
    enable_cta_comment: bool = True
    enable_impact_sfx: bool = True
    enable_smart_pacing: bool = True
    enable_progress_bar: bool = True
    enable_audio_norm: bool = True
    enable_action_tracking: bool = True
    enable_slowmo: bool = True
    enable_anti_factory: bool = True
    enable_seamless_loop: bool = False
    overlay_config: Optional[dict[str, Any]] = None


class AutoPipelineRequest(BaseModel):
    profile_id: str = "generico"
    target_mode: str = "hibrido"  # hibrido | tiktok_reels | tiktok_long | youtube_shorts
    scene_mode: Optional[str] = None
    game_name: Optional[str] = None
    top_n: int = 10  # 10 = next 10 unseen; 0 = ALL unseen moments in full batch
    skip_exported: bool = True
    force_reanalyze: bool = False
    style: str = "fit"
    subtitles_mode: str = "whisper"  # whisper (default ON) | off
    subtitle_style: str = "amarelo_viral"
    enable_hook: bool = True
    enable_impact_zoom: bool = True
    enable_preview_flash: bool = True
    enable_infinite_loop: bool = True
    enable_cta_comment: bool = True
    enable_impact_sfx: bool = True
    enable_smart_pacing: bool = True
    enable_progress_bar: bool = True
    enable_audio_norm: bool = True
    enable_action_tracking: bool = True
    enable_slowmo: bool = True
    enable_anti_factory: bool = True
    enable_seamless_loop: bool = False
    overlay_config: Optional[dict[str, Any]] = None


class GameDetectRequest(BaseModel):
    project_id: Optional[str] = None
    filename: Optional[str] = None


class MoreCopyOptionsRequest(BaseModel):
    count: int = 5
    game_name: str = ""
    profile_id: str = "generico"
    category: str = "combate_intenso"
    platform_target: str = "youtube_shorts"
    exclude_titles: list[str] = []
    trending_tags: Optional[list[str]] = None


class TrendingTagsRequest(BaseModel):
    trending_hashtags: list[str]


class WatcherConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    folder_path: Optional[str] = None


class SmartAgendaRequest(BaseModel):
    posts_per_day: int = 2
    days: int = 7


class SlotChecklistRequest(BaseModel):
    checklist: dict[str, bool]
    status: Optional[str] = None


class ABCreateRequest(BaseModel):
    clip_id: str = ""
    title_hint: str = ""
    game_name: str = ""
    platform_target: str = "tiktok_reels"


class ABWinnerRequest(BaseModel):
    winner: str
    notes: str = ""


class YouTubeConnectRequest(BaseModel):
    client_secrets_path: Optional[str] = None


class AutopilotConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    daily_time: Optional[str] = None
    privacy_status: Optional[str] = None
    require_human_approval: Optional[bool] = None
    daily_upload_limit: Optional[int] = None


class AutopilotApproveRequest(BaseModel):
    approved: bool = True


class AutopilotPublishNowRequest(BaseModel):
    human_approved: bool = False


class AutopilotEnqueueRequest(BaseModel):
    clip_ids: Optional[list[str]] = None
    import_all_library: bool = False


class ScheduleCreateRequest(BaseModel):
    clip_id: Optional[str] = None
    title: str
    description: str = ""
    hashtags: list[str] = []
    platform: str = "youtube_shorts"  # youtube_shorts | tiktok | instagram_reels | youtube_long
    scheduled_for: str
    file_path: str = ""


class ScheduleStatusRequest(BaseModel):
    status: str


class DiskCleanRequest(BaseModel):
    bucket_ids: list[str]


class RevealFileRequest(BaseModel):
    file_path: str


# ---- Health & Status ----

@app.get("/api/health/live")
def health_live() -> dict[str, Any]:
    return {"status": "ok", "app": "ClipRush Studio Pro", "version": "2.0.0"}


@app.get("/api/health/status")
def health_status() -> dict[str, Any]:
    ff = discover_ffmpeg()
    ollama = check_ollama_status(timeout=1.5)
    settings = load_settings()
    cpu_pct = round(CPUGuard().current_cpu_pct(), 1)
    qwen_state = get_qwen_five_state(active_cut_number=get_active_analysis_cut())

    ram_info: dict[str, Any] = {"used_gb": 0.0, "total_gb": 0.0, "percent": 0.0}
    battery_info: dict[str, Any] = {"present": False, "plugged": True, "percent": 100, "label": "Energia AC / Desktop"}
    disk_free_gb = 0.0
    try:
        import psutil
        vm = psutil.virtual_memory()
        ram_info = {
            "used_gb": round(vm.used / (1024 ** 3), 2),
            "total_gb": round(vm.total / (1024 ** 3), 2),
            "percent": round(vm.percent, 1),
        }
        bat = psutil.sensors_battery()
        if bat is not None:
            pct = int(round(bat.percent))
            plugged = bool(bat.power_plugged)
            battery_info = {
                "present": True,
                "plugged": plugged,
                "percent": pct,
                "label": f"{'Na tomada' if plugged else 'Na bateria'} ({pct}%)",
            }
    except Exception:
        pass
    try:
        disk_free_gb = round(shutil.disk_usage(str(DATA_DIR)).free / (1024 ** 3), 2)
    except OSError:
        pass

    return {
        "system_ok": True,
        "ffmpeg": ff,
        "ollama": ollama,
        "qwen_state": qwen_state,
        "output_dir": str(get_output_dir()),
        "settings": settings,
        "hardware": {
            "cpu_percent": cpu_pct,
            "cpu_pause_threshold_pct": int(settings.get("cpu_pause_threshold_pct", 80)),
            "max_concurrent_heavy": 1,
            "priority": "BELOW_NORMAL",
            "ram": ram_info,
            "disk_free_gb": disk_free_gb,
            "battery": battery_info,
        },
        "security": {
            "host_bound": "127.0.0.1:8765",
            "token_protected": True,
            "dpapi_vault": dpapi_available(),
        },
        "copy_engine": get_copy_engine_stats(),
        "watcher": get_watcher_status(),
    }


@app.get("/api/profiles")
def get_profiles() -> dict[str, Any]:
    return {
        "profiles": list(GAME_PROFILES.values()),
        "subtitle_styles": list(SUBTITLE_STYLES.values()),
    }


# ---- Projects: Upload, Import, Analyze, Edit Moments ----

@app.get("/api/projects")
def api_list_projects() -> dict[str, Any]:
    return {"projects": list_projects()}


def _attach_time_estimate(proj: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    dur = float((proj.get("meta") or {}).get("duration") or 0.0)
    proj["time_estimate"] = estimate_processing_time(
        duration_sec=max(10.0, dur),
        detailed_mode=bool(settings.get("analysis_detailed_mode", False)),
        use_vision=bool(settings.get("use_ollama_vision", True)),
        clip_count=5,
    )
    return proj


@app.get("/api/projects/stream-local")
def api_stream_local(path: str, request: Request):
    candidate = Path(path)
    if not _is_allowed_local_media_file(candidate):
        raise HTTPException(
            status_code=403,
            detail="Acesso bloqueado: apenas videos de projetos importados ou pastas permitidas podem ser transmitidos.",
        )
    return _stream_video_range(request, candidate.resolve())


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: str) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado.", category="SISTEMA", status_code=404)
    if proj.get("moments") and proj.get("video_path"):
        annotate_moments_export_status(
            proj["video_path"], proj["moments"], duration=(proj.get("meta") or {}).get("duration")
        )
    return _attach_time_estimate(proj)


@app.post("/api/projects/{project_id}/reset-export-history")
def api_reset_export_history(project_id: str) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado.", category="SISTEMA", status_code=404)
    clear_video_export_history(proj["video_path"])
    if proj.get("moments"):
        annotate_moments_export_status(proj["video_path"], proj["moments"])
        upsert_project(proj)
    return {"ok": True, "project": proj}


def _open_native_windows_file_dialog() -> str:
    """Open a topmost native Windows file picker in a dedicated Tk thread (zero file copy)."""
    result: dict[str, Any] = {"path": "", "error": None}

    def _run_tk() -> None:
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
                root.lift()
                root.focus_force()
            except Exception:
                pass
            selected = filedialog.askopenfilename(
                parent=root,
                title="Selecionar Gameplay PS4/PS5 - ClipRush Studio Pro",
                filetypes=[
                    ("Videos de Gameplay", "*.mp4;*.mkv;*.mov;*.webm;*.avi;*.m4v;*.ts"),
                    ("Todos os Arquivos", "*.*"),
                ],
            )
            result["path"] = str(selected or "").strip()
        except Exception as exc:
            result["error"] = exc
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass

    t = threading.Thread(target=_run_tk, daemon=True)
    t.start()
    t.join()

    if result["error"] is not None:
        raise XRayError(
            "Nao foi possivel abrir o seletor nativo de arquivos do Windows. "
            "Voce ainda pode arrastar o video ou colar o caminho local diretamente.",
            category="UPLOAD",
            technical_detail=str(result["error"]),
            status_code=500,
        )
    return result["path"]


@app.post("/api/project/browse-local-file")
def api_browse_local_file(req: Optional[BrowseLocalFileRequest] = None) -> dict[str, Any]:
    """Open native Windows file picker and immediately import the selected path with ZERO file copy."""
    selected_path = _open_native_windows_file_dialog()
    if not selected_path:
        return {"cancelled": True, "project": None}

    p = Path(selected_path)
    if not p.exists() or not p.is_file():
        raise XRayError(
            f"O arquivo selecionado nao pode ser lido no disco: {selected_path}",
            category="UPLOAD",
            status_code=404,
            context={"file_path": selected_path},
        )

    meta = probe_video(str(p))
    req_profile = req.profile_id if req else "auto"
    resolved_profile = (
        detect_game_profile_from_name(p.name)
        if not req_profile or req_profile == "auto"
        else req_profile
    )
    project = upsert_project(
        {
            "name": p.stem,
            "original_filename": p.name,
            "video_path": str(p.resolve()),
            "media_url": f"/api/projects/stream-local?path={str(p.resolve())}",
            "profile_id": resolved_profile,
            "meta": meta,
            "moments": [],
            "long_edit": None,
            "youtube_kit": None,
            "status": "imported",
        }
    )
    return {"cancelled": False, "project": _attach_time_estimate(project)}


@app.post("/api/projects/upload")
def api_upload_video(
    file: UploadFile = File(...),
    profile_id: str = Form("auto"),
) -> dict[str, Any]:
    if not file.filename:
        raise XRayError("Nenhum arquivo de video foi selecionado para envio.", category="UPLOAD", status_code=400)

    ext = Path(file.filename).suffix.lower() or ".mp4"
    if ext not in (".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts", ".m4v"):
        raise XRayError(
            f"Formato de arquivo '{ext}' nao suportado. Use MP4, MKV, MOV, AVI ou WEBM.",
            category="UPLOAD",
            status_code=400,
        )

    safe_stem = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in Path(file.filename).stem).strip()[:60]
    dest = UPLOADS_DIR / f"{safe_stem}_{uuid.uuid4().hex[:6]}{ext}"

    try:
        with dest.open("wb") as out_f:
            shutil.copyfileobj(file.file, out_f, length=4 * 1024 * 1024)
    except Exception as exc:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise XRayError(
            "Erro ao salvar o arquivo de video enviado no disco. Verifique o espaco livre.",
            category="UPLOAD",
            technical_detail=str(exc),
            status_code=500,
        )
    finally:
        try:
            file.file.close()
        except Exception:
            pass

    meta = probe_video(str(dest))
    resolved_profile = (
        detect_game_profile_from_name(file.filename)
        if not profile_id or profile_id == "auto"
        else profile_id
    )
    project = upsert_project(
        {
            "name": Path(file.filename).stem,
            "original_filename": file.filename,
            "video_path": str(dest),
            "media_url": f"/media/uploads/{dest.name}",
            "profile_id": resolved_profile,
            "meta": meta,
            "moments": [],
            "long_edit": None,
            "youtube_kit": None,
            "status": "imported",
        }
    )
    return _attach_time_estimate(project)


@app.post("/api/projects/import-local")
def api_import_local(req: LocalImportRequest) -> dict[str, Any]:
    p = Path(req.file_path.strip().strip('"'))
    if not p.exists() or not p.is_file():
        raise XRayError(
            f"O arquivo informado nao existe: {req.file_path}. Verifique o caminho completo do video de gameplay.",
            category="UPLOAD",
            status_code=404,
            context={"file_path": req.file_path},
        )
    meta = probe_video(str(p))
    resolved_profile = (
        detect_game_profile_from_name(p.name)
        if not req.profile_id or req.profile_id == "auto"
        else req.profile_id
    )
    project = upsert_project(
        {
            "name": p.stem,
            "original_filename": p.name,
            "video_path": str(p.resolve()),
            "media_url": f"/api/projects/stream-local?path={str(p.resolve())}",
            "profile_id": resolved_profile,
            "meta": meta,
            "moments": [],
            "long_edit": None,
            "youtube_kit": None,
            "status": "imported",
        }
    )
    return _attach_time_estimate(project)


def _stream_video_range(request: Request, file_path: Path, media_type: str = "video/mp4"):
    """True HTTP 206 Partial Content byte-range streamer so HTML5 <video> players load & seek instantly."""
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo de video nao encontrado")

    file_size = file_path.stat().st_size
    ext = file_path.suffix.lower()
    if ext == ".jpg" or ext == ".jpeg":
        return FileResponse(str(file_path), media_type="image/jpeg")
    if ext == ".png":
        return FileResponse(str(file_path), media_type="image/png")

    range_header = request.headers.get("range")
    chunk_size = 512 * 1024  # 512 KB streaming chunks

    if range_header and range_header.lower().startswith("bytes="):
        spec = range_header.split("=", 1)[1].split(",")[0].strip()
        start_s, _, end_s = spec.partition("-")
        try:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else min(file_size - 1, start + 4 * 1024 * 1024 - 1)
        except ValueError:
            start, end = 0, file_size - 1
        start = max(0, min(start, file_size - 1))
        end = max(start, min(end, file_size - 1))
        content_length = end - start + 1

        def _iter_range():
            with file_path.open("rb") as fh:
                fh.seek(start)
                remaining = content_length
                while remaining > 0:
                    data = fh.read(min(chunk_size, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            _iter_range(),
            status_code=206,
            media_type=media_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
            },
        )

    def _iter_full():
        with file_path.open("rb") as fh:
            while True:
                data = fh.read(chunk_size)
                if not data:
                    break
                yield data

    return StreamingResponse(
        _iter_full(),
        status_code=200,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@app.post("/api/projects/{project_id}/analyze")
def api_analyze_project(project_id: str, req: AnalyzeRequest) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado para iniciar analise.", category="ANALISE", status_code=404)

    profile_id = req.profile_id if req.profile_id in GAME_PROFILES else proj.get("profile_id", "generico")
    target_mode = (
        req.target_mode
        if req.target_mode in ("hibrido", "tiktok_reels", "tiktok_long", "youtube_shorts")
        else "hibrido"
    )
    game_name = (req.game_name or proj.get("game_name") or "").strip()
    game_sheet = find_game_sheet(game_name) if game_name else proj.get("game_sheet")
    job = submit_isolated_job(
        kind="analyze",
        label=f"Analisando {proj['name']}",
        kwargs={
            "project_id": project_id,
            "profile_id": profile_id,
            "target_mode": target_mode,
            "scene_mode": req.scene_mode,
            "game_name": game_name,
            "game_sheet": game_sheet,
        },
    )
    return {"job_id": job["id"], "status": "running"}


@app.patch("/api/projects/{project_id}/moments/{moment_id}")
def api_patch_moment(project_id: str, moment_id: str, req: MomentPatchRequest) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado.", category="SISTEMA", status_code=404)
    for m in proj.get("moments", []):
        if m.get("id") == moment_id:
            if req.hook_2s is not None:
                m["hook_2s"] = req.hook_2s.strip().upper()[:48]
            if req.cta_comment is not None:
                m["cta_comment"] = req.cta_comment.strip()[:48]
            if req.title is not None:
                m["title"] = req.title.strip()[:110]
            if req.description is not None:
                m["description"] = req.description.strip()[:400]
            if req.hashtags is not None:
                m["hashtags"] = [str(t).strip() for t in req.hashtags if str(t).strip()][:10]
            if req.start is not None:
                m["start"] = max(0.0, round(float(req.start), 2))
            if req.end is not None:
                m["end"] = max(m["start"] + 2.0, round(float(req.end), 2))
            m["duration"] = round(m["end"] - m["start"], 2)
            upsert_project(proj)
            return m
    raise XRayError("Momento nao encontrado no projeto.", category="SISTEMA", status_code=404)


@app.post("/api/projects/{project_id}/moments/{moment_id}/regenerate-metadata")
def api_regenerate_moment_metadata(project_id: str, moment_id: str) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado.", category="SISTEMA", status_code=404)
    for m in proj.get("moments", []):
        if m.get("id") == moment_id:
            frames = [m["thumb_path"]] if m.get("thumb_path") and Path(m["thumb_path"]).exists() else []
            next_var = int(m.get("variation_index", 0)) + 1
            with exclusive_heavy_task():
                regen = regenerate_viral_kit(
                    frame_paths=frames,
                    profile_id=proj.get("profile_id", "generico"),
                    category=m.get("category", "combate_intenso"),
                    video_name=proj.get("name", "gameplay"),
                    previous_title=m.get("title", ""),
                    previous_hook=m.get("hook_2s", ""),
                    variation_index=next_var,
                    platform_target=m.get("platform_target", "youtube_shorts"),
                )
            m["hook_2s"] = regen["hook_2s"]
            m["cta_comment"] = regen.get("cta_comment", m.get("cta_comment", "Voce escaparia dessa?"))
            m["title"] = regen["title"]
            m["description"] = regen["description"]
            m["hashtags"] = regen["hashtags"]
            m["detected_game"] = regen.get("detected_game", m.get("detected_game", ""))
            m["ai_powered"] = regen.get("ai_powered", False)
            m["variation_index"] = next_var
            upsert_project(proj)
            return m
    raise XRayError("Momento nao encontrado no projeto.", category="SISTEMA", status_code=404)


@app.post("/api/projects/{project_id}/auto-pipeline")
def api_auto_pipeline(project_id: str, req: AutoPipelineRequest) -> dict[str, Any]:
    """Scale Auto Pipeline running in isolated OS process (zero event-loop/GIL blocking)."""
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado para o Modo Automatico.", category="ANALISE", status_code=404)

    profile_id = req.profile_id if req.profile_id in GAME_PROFILES else proj.get("profile_id", "generico")
    top_n = int(req.top_n) if req.top_n is not None else 10
    label_n = "TODOS os Ineditos" if top_n <= 0 else f"Proximos {top_n} Ineditos"
    job = submit_isolated_job(
        kind="auto_pipeline",
        label=f"Lote Automatico ({label_n}): {proj['name']}",
        kwargs={"project_id": project_id, "profile_id": profile_id, "req": req.model_dump()},
    )
    return {"job_id": job["id"], "status": "running", "top_n": top_n}


# ---- Rendering: Mode 1 (Batch 9:16) & Mode 2 (Long 16:9) ----

@app.post("/api/projects/{project_id}/render-batch")
def api_render_batch(project_id: str, req: BatchRenderRequest) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado para renderizacao.", category="RENDER", status_code=404)

    all_moments = annotate_moments_export_status(proj["video_path"], proj.get("moments") or [])
    if not all_moments:
        raise XRayError(
            "Nenhum momento foi analisado ainda neste projeto. Execute a analise antes de exportar.",
            category="RENDER",
            status_code=400,
        )

    if req.moment_ids:
        id_set = set(req.moment_ids)
        targets = [m for m in all_moments if m["id"] in id_set]
    else:
        pool = [m for m in all_moments if not m.get("already_exported")] if req.skip_exported else list(all_moments)
        if not pool:
            raise XRayError(
                "Todos os momentos encontrados ja foram exportados! Selecione manualmente um card especifico ou clique em 'Resetar Historico'.",
                category="RENDER",
                status_code=400,
            )
        targets = pool[: req.limit] if (req.limit and req.limit > 0) else pool

    if not targets:
        raise XRayError("Nenhum clipe selecionado para renderizar.", category="RENDER", status_code=400)

    job = submit_isolated_job(
        kind="render_batch",
        label=f"Renderizando {len(targets)} clipes 9:16",
        kwargs={"project_id": project_id, "target_ids": [m["id"] for m in targets], "req": req.model_dump()},
    )
    return {"job_id": job["id"], "total": len(targets), "status": "running"}


@app.post("/api/projects/{project_id}/render-long")
def api_render_long(project_id: str) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado para exportar gameplay longa.", category="RENDER", status_code=404)
    long_edit = proj.get("long_edit")
    if not long_edit or not long_edit.get("keep_segments"):
        raise XRayError(
            "Execute a analise da gameplay primeiro para mapear os trechos mortos e capitulos.",
            category="RENDER",
            status_code=400,
        )

    job = submit_isolated_job(
        kind="render_long",
        label=f"Exportando Gameplay 16:9 ({proj['name']})",
        kwargs={"project_id": project_id},
    )
    return {"job_id": job["id"], "status": "running"}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict[str, Any]:
    job = get_job_state(job_id)
    if not job:
        raise XRayError("Tarefa em segundo plano nao encontrada.", category="SISTEMA", status_code=404)
    return job


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str) -> dict[str, Any]:
    return cancel_isolated_job(job_id)


# ---- Clip Library ----

@app.get("/api/clips")
def api_list_clips() -> dict[str, Any]:
    return {"clips": list_clips()}


@app.post("/api/clips/{clip_id}/create-tiktok-25s")
def api_create_tiktok_25s(clip_id: str) -> dict[str, Any]:
    """Create a punchy 25s TikTok/Reels cut from an already-rendered clip (>35s) preserving
    the first 3.5s Visual Hook + the 21.5s most intense climax window without re-reading the original gameplay."""
    clip = get_clip(clip_id)
    if not clip:
        raise XRayError("Clipe nao encontrado na biblioteca.", category="RENDER", status_code=404)

    with exclusive_heavy_task():
        res = create_tiktok_short_version(
            clip_path=clip.get("file_path", ""),
            project_name=clip.get("project_name") or "clipe",
        )
    tiktok_tags = calibrate_platform_hashtags(
        clip.get("hashtags", []),
        platform_target="tiktok_reels",
        profile_id=clip.get("profile_id", "generico"),
        detected_game=clip.get("detected_game", ""),
    )
    short_title = (clip.get("title") or "Clipe Epico").replace("#Shorts", "").strip()
    new_clip = add_clip(
        {
            "project_id": clip.get("project_id", ""),
            "project_name": clip.get("project_name", ""),
            "source_video_path": clip.get("source_video_path", ""),
            "start": clip.get("start", 0.0),
            "end": clip.get("end", 0.0),
            "peak": clip.get("peak", 0.0),
            "profile_id": clip.get("profile_id", "generico"),
            "detected_game": clip.get("detected_game", ""),
            "moment_id": clip.get("moment_id", ""),
            "parent_clip_id": clip_id,
            "is_short_25s_variant": True,
            "rank": clip.get("rank", 1),
            "score": clip.get("score", 90),
            "category": clip.get("category", "combate_intenso"),
            "category_label": clip.get("category_label", "Combate & Acao"),
            "platform_target": "tiktok_reels",
            "platform_badge": "Ideal TikTok / Reels (Versao 25s)",
            "hook_2s": clip.get("hook_2s", ""),
            "cta_comment": clip.get("cta_comment", "Voce escaparia dessa?"),
            "title": short_title,
            "description": clip.get("description", ""),
            "hashtags": tiktok_tags,
            "thumb_url": clip.get("thumb_url", ""),
            "thumb_path": clip.get("thumb_path", ""),
            "variation_index": 0,
            **res,
        }
    )
    return new_clip


@app.post("/api/clips/{clip_id}/create-tiktok-long")
def api_create_tiktok_long(clip_id: str) -> dict[str, Any]:
    """Generate a Monetizable TikTok (61s-90s) complete expanded version of the same moment
    by expanding context before and after the action peak from the source gameplay."""
    clip = get_clip(clip_id)
    if not clip:
        raise XRayError("Clipe nao encontrado na biblioteca.", category="RENDER", status_code=404)

    source_video = clip.get("source_video_path") or ""
    if (not source_video or not Path(source_video).exists()) and clip.get("project_id"):
        proj = get_project(clip["project_id"])
        if proj and proj.get("video_path"):
            source_video = proj["video_path"]

    with exclusive_heavy_task():
        res = create_tiktok_long_version(
            source_video_path=source_video,
            clip_meta=clip,
            project_name=clip.get("project_name") or "clipe",
        )

    settings = load_settings()
    tk_min = int(settings.get("tiktok_long_min_sec", 61))
    tk_max = int(settings.get("tiktok_long_max_sec", 90))
    long_tags = calibrate_platform_hashtags(
        clip.get("hashtags", []),
        platform_target="tiktok_long",
        profile_id=clip.get("profile_id", "generico"),
        detected_game=clip.get("detected_game", ""),
    )
    long_title = (clip.get("title") or "Gameplay Completa").replace("#Shorts", "").strip()
    new_clip = add_clip(
        {
            "project_id": clip.get("project_id", ""),
            "project_name": clip.get("project_name", ""),
            "profile_id": clip.get("profile_id", "generico"),
            "detected_game": clip.get("detected_game", ""),
            "moment_id": clip.get("moment_id", ""),
            "parent_clip_id": clip_id,
            "is_tiktok_long_variant": True,
            "rank": clip.get("rank", 1),
            "score": clip.get("score", 92),
            "category": clip.get("category", "combate_intenso"),
            "category_label": clip.get("category_label", "Combate & Acao"),
            "platform_target": "tiktok_long",
            "platform_badge": f"TikTok Monetizavel ({tk_min}s-{tk_max}s)",
            "hook_2s": clip.get("hook_2s", ""),
            "cta_comment": clip.get("cta_comment", "Valeu esperar ate o fim?"),
            "title": long_title,
            "description": clip.get("description", ""),
            "hashtags": long_tags,
            "thumb_url": clip.get("thumb_url", ""),
            "thumb_path": clip.get("thumb_path", ""),
            "variation_index": 0,
            **res,
        }
    )
    return new_clip


@app.post("/api/clips/{clip_id}/regenerate-metadata")
def api_regenerate_clip_metadata(clip_id: str) -> dict[str, Any]:
    """Generate a brand-new creative variation of Title, Hook, Description & Hashtags
    using Qwen 3.5 Vision without re-rendering the MP4 video."""
    clip = get_clip(clip_id)
    if not clip:
        raise XRayError("Clipe nao encontrado na biblioteca para refazer texto.", category="SISTEMA", status_code=404)

    frames: list[str] = []
    if clip.get("thumb_path") and Path(clip["thumb_path"]).exists():
        frames.append(clip["thumb_path"])
    elif clip.get("file_path") and Path(clip["file_path"]).exists():
        extracted = extract_clip_frame(clip["file_path"], THUMBS_DIR / f"regen_{clip_id}.jpg")
        if extracted:
            frames.append(extracted)

    next_var = int(clip.get("variation_index", 0)) + 1
    with exclusive_heavy_task():
        regen = regenerate_viral_kit(
            frame_paths=frames,
            profile_id=clip.get("profile_id", "generico"),
            category=clip.get("category", "combate_intenso"),
            video_name=clip.get("project_name") or clip.get("file_name") or "gameplay",
            previous_title=clip.get("title", ""),
            previous_hook=clip.get("hook_2s", ""),
            variation_index=next_var,
            platform_target=clip.get("platform_target", "youtube_shorts"),
        )

    updated = update_clip(
        clip_id,
        {
            "detected_game": regen.get("detected_game", clip.get("detected_game", "")),
            "hook_2s": regen["hook_2s"],
            "cta_comment": regen.get("cta_comment", clip.get("cta_comment", "Voce escaparia dessa?")),
            "title": regen["title"],
            "description": regen["description"],
            "hashtags": regen["hashtags"],
            "ai_powered": regen.get("ai_powered", False),
            "variation_index": next_var,
        },
    )
    if not updated:
        raise XRayError("Falha ao salvar a nova variacao do clipe.", category="SISTEMA", status_code=500)
    return updated


@app.patch("/api/clips/{clip_id}")
def api_patch_clip(clip_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {k: payload[k] for k in ("title", "description", "hashtags", "hook_2s", "cta_comment") if k in payload}
    updated = update_clip(clip_id, allowed)
    if not updated:
        raise XRayError("Clipe nao encontrado na biblioteca.", category="SISTEMA", status_code=404)
    return updated


@app.delete("/api/clips/{clip_id}")
def api_delete_clip(clip_id: str, delete_file: bool = True) -> dict[str, Any]:
    ok = delete_clip(clip_id, delete_file=delete_file)
    if not ok:
        raise XRayError("Clipe nao encontrado na biblioteca.", category="SISTEMA", status_code=404)
    return {"deleted": True, "id": clip_id}


# ---- Scheduler & Publishing ----

@app.get("/api/schedule")
def api_list_schedule() -> dict[str, Any]:
    return {"items": list_scheduled()}


@app.post("/api/schedule")
def api_create_schedule(req: ScheduleCreateRequest) -> dict[str, Any]:
    return add_scheduled(req.model_dump())


@app.patch("/api/schedule/{item_id}")
def api_update_schedule(item_id: str, req: ScheduleStatusRequest) -> dict[str, Any]:
    updated = update_scheduled_status(item_id, req.status)
    if not updated:
        raise XRayError("Agendamento nao encontrado.", category="SISTEMA", status_code=404)
    return updated


@app.delete("/api/schedule/{item_id}")
def api_delete_schedule(item_id: str) -> dict[str, Any]:
    ok = delete_scheduled(item_id)
    if not ok:
        raise XRayError("Agendamento nao encontrado.", category="SISTEMA", status_code=404)
    return {"deleted": True}


# ---- Official YouTube API OAuth2 & Daily Shorts Autopilot ----

@app.get("/api/youtube/status")
def api_youtube_status() -> dict[str, Any]:
    return get_youtube_connection_status()


@app.post("/api/youtube/upload-secrets")
async def api_youtube_upload_secrets(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise XRayError(
            "Envie o arquivo client_secrets.json baixado do Google Cloud Console (extensao .json).",
            category="YOUTUBE_AUTH",
            status_code=400,
        )
    raw = await file.read()
    vault_res = save_protected_client_secrets(raw)
    return {
        "ok": True,
        "path": vault_res["path"],
        "vault": vault_res["vault"],
        "audit_warning": YOUTUBE_AUDIT_WARNING,
        "status": get_youtube_connection_status(),
    }


@app.post("/api/youtube/connect")
def api_youtube_connect(req: YouTubeConnectRequest) -> dict[str, Any]:
    return start_oauth_flow(custom_secrets_path=req.client_secrets_path)


@app.post("/api/youtube/disconnect")
def api_youtube_disconnect() -> dict[str, Any]:
    return disconnect_youtube()


@app.get("/api/youtube/autopilot")
def api_youtube_autopilot_get() -> dict[str, Any]:
    return get_autopilot_state()


@app.put("/api/youtube/autopilot/config")
def api_youtube_autopilot_config(req: AutopilotConfigRequest) -> dict[str, Any]:
    return update_autopilot_config(
        enabled=req.enabled,
        daily_time=req.daily_time,
        privacy_status=req.privacy_status,
        require_human_approval=req.require_human_approval,
        daily_upload_limit=req.daily_upload_limit,
    )


@app.post("/api/youtube/autopilot/approve/{item_id}")
def api_youtube_autopilot_approve(item_id: str, req: Optional[AutopilotApproveRequest] = None) -> dict[str, Any]:
    approved = req.approved if req is not None else True
    return approve_autopilot_item(item_id, approved=approved)


@app.post("/api/youtube/autopilot/queue")
def api_youtube_autopilot_enqueue(req: AutopilotEnqueueRequest) -> dict[str, Any]:
    all_clips = list_clips()
    if req.import_all_library:
        targets = [c for c in all_clips if c.get("file_exists")]
    elif req.clip_ids:
        id_set = set(req.clip_ids)
        targets = [c for c in all_clips if c.get("id") in id_set]
    else:
        raise XRayError("Selecione ao menos um clipe da Biblioteca para adicionar a fila diaria.", category="YOUTUBE_UPLOAD", status_code=400)

    if not targets:
        raise XRayError("Nenhum clipe valido disponivel na Biblioteca para adicionar a fila.", category="YOUTUBE_UPLOAD", status_code=400)
    return enqueue_autopilot_clips(targets)


@app.delete("/api/youtube/autopilot/queue/{item_id}")
def api_youtube_autopilot_remove(item_id: str) -> dict[str, Any]:
    return remove_autopilot_item(item_id)


@app.post("/api/youtube/autopilot/publish-now/{item_id}")
def api_youtube_autopilot_publish_now(
    item_id: str,
    req: Optional[AutopilotPublishNowRequest] = None,
) -> dict[str, Any]:
    human_approved = bool(req.human_approved) if req is not None else False
    return publish_autopilot_item(item_id, mark_daily_slot=False, human_approved=human_approved)


# ---- Disk Cleanup ----

@app.get("/api/disk/scan")
def api_disk_scan() -> dict[str, Any]:
    return scan_disk_usage()


@app.post("/api/disk/clean")
def api_disk_clean(req: DiskCleanRequest) -> dict[str, Any]:
    return clean_disk_buckets(req.bucket_ids)


# ---- P1: Game Sheets, Combinatorial Copy Engine, Visual 9:16 Overlay & PS4 Watch Folder ----

@app.get("/api/games")
def api_list_games() -> dict[str, Any]:
    settings = load_settings()
    return {
        "games": list_games(),
        "trending_hashtags": settings.get("trending_hashtags") or [],
    }


@app.post("/api/games")
def api_upsert_game(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        saved = upsert_game_sheet(payload)
        return {"ok": True, "game": saved, "games": list_games()}
    except ValueError as exc:
        raise XRayError(str(exc), category="SISTEMA", status_code=400)


@app.post("/api/games/detect")
def api_detect_game(req: GameDetectRequest) -> dict[str, Any]:
    frame_path: Optional[str] = None
    filename = req.filename or ""
    if req.project_id:
        proj = get_project(req.project_id)
        if proj:
            filename = proj.get("original_filename") or proj.get("name") or filename
            vp = proj.get("video_path")
            if vp and Path(vp).exists():
                tmp_frame = THUMBS_DIR / f"detect_{req.project_id}.jpg"
                frame_path = extract_clip_frame(vp, tmp_frame, at_ratio=0.25)
    with exclusive_heavy_task():
        res = detect_game_with_qwen(frame_path, filename=filename)
    return res


@app.post("/api/copy/more-options")
def api_copy_more_options(req: MoreCopyOptionsRequest) -> dict[str, Any]:
    sheet = find_game_sheet(req.game_name) if req.game_name else None
    options = generate_multiple_options(
        count=req.count,
        game_name=req.game_name,
        profile_id=req.profile_id,
        category=req.category,
        platform_target=req.platform_target,
        game_sheet=sheet,
        exclude_titles=req.exclude_titles,
        trending_tags=req.trending_tags,
        record_history=True,
    )
    return {"options": options, "stats": get_copy_engine_stats()}


@app.put("/api/copy/trending")
def api_save_trending_tags(req: TrendingTagsRequest) -> dict[str, Any]:
    cleaned = []
    seen = set()
    for t in req.trending_hashtags:
        s = str(t).strip()
        if not s:
            continue
        if not s.startswith("#"):
            s = f"#{s.replace(' ', '')}"
        if s.lower() not in seen:
            seen.add(s.lower())
            cleaned.append(s)
    cfg = save_settings({"trending_hashtags": cleaned[:20]})
    return {"ok": True, "trending_hashtags": cfg.get("trending_hashtags", [])}


@app.get("/api/overlay")
def api_get_overlay() -> dict[str, Any]:
    return load_overlay_config()


@app.put("/api/overlay")
def api_put_overlay(payload: dict[str, Any]) -> dict[str, Any]:
    return save_overlay_config(payload)


@app.get("/api/watcher/status")
def api_watcher_status() -> dict[str, Any]:
    return get_watcher_status()


@app.put("/api/watcher/config")
def api_watcher_config(req: WatcherConfigRequest) -> dict[str, Any]:
    return update_watcher_config(enabled=req.enabled, folder_path=req.folder_path)


@app.post("/api/watcher/scan-now")
def api_watcher_scan_now() -> dict[str, Any]:
    return scan_watch_folder_now(require_stability=False)


@app.post("/api/watcher/queue/{item_id}/import")
def api_watcher_import_item(item_id: str) -> dict[str, Any]:
    st = get_watcher_status()
    item = next((q for q in st.get("queue", []) if q.get("id") == item_id), None)
    if not item:
        raise XRayError("Item da pasta monitorada nao encontrado.", category="WATCHER", status_code=404)
    p = Path(item["file_path"])
    if not p.exists() or not p.is_file():
        raise XRayError(f"Arquivo nao acessivel no momento: {p}", category="WATCHER", status_code=404)
    meta = probe_video(str(p))
    profile_id = detect_game_profile_from_name(p.name)
    sheet = find_game_sheet(p.stem)
    project = upsert_project(
        {
            "name": p.stem,
            "original_filename": p.name,
            "video_path": str(p.resolve()),
            "media_url": f"/api/projects/stream-local?path={str(p.resolve())}",
            "profile_id": sheet["profile_id"] if sheet else profile_id,
            "game_name": sheet["official_name"] if sheet else "",
            "game_sheet": sheet,
            "meta": meta,
            "moments": [],
            "long_edit": None,
            "youtube_kit": None,
            "status": "imported",
        }
    )
    mark_watch_item_status(item_id, "importado")
    return {"ok": True, "project": _attach_time_estimate(project), "watcher": get_watcher_status()}


@app.delete("/api/watcher/queue/{item_id}")
def api_watcher_dismiss_item(item_id: str) -> dict[str, Any]:
    return mark_watch_item_status(item_id, "descartado")


@app.post("/api/system/open-logs")
def api_open_logs() -> dict[str, Any]:
    log_file = DATA_DIR / "launcher.log"
    if not log_file.exists():
        log_file.write_text("", encoding="utf-8")
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(log_file.resolve())])
    return {"ok": True, "path": str(log_file)}


# ---- P2: Performance Analytics, Smart Agenda/A/B/Goals, Per-Project Storage, Backup .zip & Diagnostics ----

@app.get("/api/analytics/dashboard")
def api_analytics_dashboard() -> dict[str, Any]:
    return get_analytics_dashboard()


@app.post("/api/analytics/record")
def api_analytics_record(payload: dict[str, Any]) -> dict[str, Any]:
    return add_performance_record(payload)


@app.delete("/api/analytics/record/{record_id}")
def api_analytics_delete(record_id: str) -> dict[str, Any]:
    return delete_performance_record(record_id)


@app.post("/api/analytics/import-tiktok-csv")
async def api_analytics_import_csv(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    return import_tiktok_studio_csv(raw)


@app.post("/api/analytics/sync-youtube")
def api_analytics_sync_youtube() -> dict[str, Any]:
    return sync_youtube_analytics()


@app.get("/api/growth/state")
def api_growth_state() -> dict[str, Any]:
    return get_growth_state()


@app.post("/api/growth/agenda/generate")
def api_growth_agenda_generate(req: SmartAgendaRequest) -> dict[str, Any]:
    return generate_smart_agenda(posts_per_day=req.posts_per_day, days=req.days)


@app.patch("/api/growth/agenda/{slot_id}")
def api_growth_agenda_slot(slot_id: str, req: SlotChecklistRequest) -> dict[str, Any]:
    return update_slot_checklist(slot_id, req.checklist, status=req.status)


@app.post("/api/growth/ab")
def api_growth_ab_create(req: ABCreateRequest) -> dict[str, Any]:
    return create_ab_test(
        clip_id=req.clip_id,
        title_hint=req.title_hint,
        game_name=req.game_name,
        platform_target=req.platform_target,
    )


@app.post("/api/growth/ab/{test_id}/winner")
def api_growth_ab_winner(test_id: str, req: ABWinnerRequest) -> dict[str, Any]:
    return record_ab_winner(test_id, winner=req.winner, notes=req.notes)


@app.put("/api/growth/goals")
def api_growth_goals(payload: dict[str, Any]) -> dict[str, Any]:
    return update_goals(payload)


@app.get("/api/disk/projects")
def api_disk_projects() -> dict[str, Any]:
    return get_project_storage_breakdown()


@app.delete("/api/disk/projects/{project_id}")
def api_disk_delete_project(project_id: str, target: str = "renders") -> dict[str, Any]:
    return delete_project_media(project_id, target=target)


@app.get("/api/backup/export")
def api_backup_export():
    zip_bytes, filename = export_backup_zip()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/backup/import")
async def api_backup_import(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    return import_backup_zip(raw)


@app.get("/api/diagnostics")
def api_diagnostics() -> dict[str, Any]:
    return get_full_diagnostics()


@app.post("/api/diagnostics/selftest")
def api_diagnostics_selftest() -> dict[str, Any]:
    with exclusive_heavy_task():
        return run_synthetic_selftest()


# ---- Settings ----

@app.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    return load_settings()


@app.put("/api/settings")
def api_put_settings(payload: dict[str, Any]) -> dict[str, Any]:
    return save_settings(payload)


# ---- Windows System Helpers ----

@app.post("/api/system/open-output")
def api_open_output() -> dict[str, Any]:
    out_dir = get_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(out_dir))  # type: ignore[attr-defined]
    return {"ok": True, "path": str(out_dir)}


@app.post("/api/system/reveal-file")
def api_reveal_file(req: RevealFileRequest) -> dict[str, Any]:
    p = Path(req.file_path)
    if not _is_allowed_local_media_file(p):
        raise HTTPException(
            status_code=403,
            detail="Acao bloqueada: somente arquivos de saida ou projetos importados podem ser revelados no Explorer.",
        )
    if os.name == "nt":
        if p.exists():
            subprocess.Popen(["explorer", "/select,", str(p.resolve())])
        else:
            os.startfile(str(get_output_dir()))  # type: ignore[attr-defined]
    return {"ok": True, "path": str(p)}


@app.post("/api/system/start-ollama")
def api_start_ollama() -> dict[str, Any]:
    status = check_ollama_status(timeout=1.2)
    if status["online"]:
        return {"ok": True, "started": False, "status": status}
    ollama_exe = shutil.which("ollama") or str(Path.home() / "AppData/Local/Programs/Ollama/ollama.exe")
    if not Path(ollama_exe).exists():
        raise XRayError(
            "Executavel do Ollama nao encontrado no Windows. Instale o Ollama e baixe o modelo qwen3.5:9b.",
            category="OLLAMA",
            status_code=404,
        )
    subprocess.Popen([ollama_exe, "serve"], creationflags=CREATE_NO_WINDOW)
    return {"ok": True, "started": True, "message": "Ollama iniciado em segundo plano."}


@app.post("/api/system/test-xray")
def api_test_xray() -> dict[str, Any]:
    raise XRayError(
        "Simulacao de diagnostico do Painel Raio-X: este alerta confirma que falhas de FFmpeg, Ollama, Upload ou Render sao capturadas com arquivo, linha exata e botao de copia para IA.",
        category="DIAGNOSTICO",
        technical_detail="TestXRayTrigger: verificacao manual do Painel Raio-X de Erros",
        status_code=418,
    )


# ---- Strict Path-Traversal-Protected Media & Static Mounts ----

app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/media/thumbs/{filename:path}")
def serve_thumb_media(filename: str):
    target = _safe_resolve_under(THUMBS_DIR, filename)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Miniatura nao encontrada")
    return FileResponse(str(target))


@app.get("/media/uploads/{filename:path}")
def serve_upload_media(filename: str, request: Request):
    target = _safe_resolve_under(UPLOADS_DIR, filename)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Upload nao encontrado")
    return _stream_video_range(request, target)


@app.get("/media/saida/{filename:path}")
def serve_output_media(filename: str, request: Request):
    target = _safe_resolve_under(get_output_dir(), filename)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Arquivo de saida nao encontrado")
    return _stream_video_range(request, target)


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def serve_index():
    """Inject the per-startup random X-ClipRush-Token securely into the local HTML page."""
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    token_snippet = (
        f'<meta name="cliprush-token" content="{APP_SESSION_TOKEN}" />\n'
        f'  <script>window.__CLIPRUSH_TOKEN__ = {json.dumps(APP_SESSION_TOKEN)};</script>\n'
        "</head>"
    )
    if "</head>" in html:
        html = html.replace("</head>", token_snippet, 1)
    return HTMLResponse(content=html, status_code=200)
