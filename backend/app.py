"""ClipRush Studio Pro - FastAPI Backend Server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
from backend.editor import SUBTITLE_STYLES, extract_clip_frame, render_long_gameplay, render_viral_clip
from backend.errors import XRayError, build_xray_from_exception
from backend.ollama_client import check_ollama_status, generate_youtube_long_kit, regenerate_viral_kit
from backend.youtube_uploader import (
    disconnect_youtube,
    enqueue_autopilot_clips,
    get_autopilot_state,
    get_youtube_connection_status,
    publish_autopilot_item,
    remove_autopilot_item,
    start_autopilot_daemon,
    start_oauth_flow,
    update_autopilot_config,
)
from backend.storage import (
    add_clip,
    add_scheduled,
    clean_disk_buckets,
    delete_clip,
    delete_scheduled,
    get_clip,
    get_project,
    list_clips,
    list_projects,
    list_scheduled,
    scan_disk_usage,
    update_clip,
    update_scheduled_status,
    upsert_project,
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

app = FastAPI(title="ClipRush Studio Pro", version="2.0.0")
start_autopilot_daemon()

# In-memory background job tracker
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.RLock()


def _create_job(kind: str, label: str) -> dict[str, Any]:
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = {
        "id": job_id,
        "kind": kind,
        "label": label,
        "status": "running",  # running | done | error
        "progress": 0,
        "message": "Iniciando...",
        "result": None,
        "xray_error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    return job


def _update_job(job_id: str, **kwargs: Any) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kwargs)


def _get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _JOBS_LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None


# ---- Mandatory Cache-Control: no-store Middleware on ALL routes & static files ----

@app.middleware("http")
async def add_no_store_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
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


class AnalyzeRequest(BaseModel):
    profile_id: str = "generico"


class MomentPatchRequest(BaseModel):
    hook_2s: Optional[str] = None
    cta_comment: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None


class BatchRenderRequest(BaseModel):
    moment_ids: Optional[list[str]] = None
    limit: Optional[int] = None  # 5, 10, 20, or None for all
    style: str = "viral_blur"  # viral_blur | tela_cheia | clipe_limpo
    subtitles_mode: str = "off"  # off | whisper
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


class AutoPipelineRequest(BaseModel):
    profile_id: str = "generico"
    top_n: int = 5
    style: str = "viral_blur"
    subtitles_mode: str = "off"
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


class YouTubeConnectRequest(BaseModel):
    client_secrets_path: Optional[str] = None


class AutopilotConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    daily_time: Optional[str] = None
    privacy_status: Optional[str] = None


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
    return {
        "system_ok": True,
        "ffmpeg": ff,
        "ollama": ollama,
        "output_dir": str(get_output_dir()),
        "settings": settings,
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


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: str) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado.", category="SISTEMA", status_code=404)
    return proj


@app.post("/api/projects/upload")
async def api_upload_video(
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
        async with aiofiles.open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                await out.write(chunk)
    except Exception as exc:
        raise XRayError(
            "Erro ao salvar o arquivo de video enviado no disco.",
            category="UPLOAD",
            technical_detail=str(exc),
            status_code=500,
        )

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
    return project


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
    return project


@app.get("/api/projects/stream-local")
def api_stream_local(path: str):
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado")
    return FileResponse(str(p))


@app.post("/api/projects/{project_id}/analyze")
def api_analyze_project(project_id: str, req: AnalyzeRequest) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado para iniciar analise.", category="ANALISE", status_code=404)

    profile_id = req.profile_id if req.profile_id in GAME_PROFILES else proj.get("profile_id", "generico")
    job = _create_job("analyze", f"Analisando {proj['name']}")

    def _worker() -> None:
        try:
            def _cb(pct: int, msg: str) -> None:
                _update_job(job["id"], progress=pct, message=msg)

            result = analyze_gameplay(
                video_path=proj["video_path"],
                profile_id=profile_id,
                project_id=project_id,
                progress_cb=_cb,
            )

            # Generate YouTube long-form SEO kit automatically alongside analysis
            long_edit = result["long_edit"]
            yt_kit = generate_youtube_long_kit(
                video_name=proj["name"],
                profile_id=profile_id,
                chapters=long_edit["chapters"],
                total_duration_str=long_edit["edited_duration_str"],
            )

            proj["profile_id"] = profile_id
            proj["meta"] = result["meta"]
            proj["moments"] = result["moments"]
            proj["boring_skipped_sec"] = result["boring_skipped_sec"]
            proj["long_edit"] = long_edit
            proj["youtube_kit"] = yt_kit
            proj["status"] = "analyzed"
            upsert_project(proj)

            _update_job(job["id"], status="done", progress=100, message="Analise finalizada!", result=proj)
        except Exception as exc:
            xray = build_xray_from_exception(exc, category="ANALISE", context={"project_id": project_id})
            _update_job(job["id"], status="error", message=xray["explanation_pt"], xray_error=xray)

    threading.Thread(target=_worker, daemon=True).start()
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
            regen = regenerate_viral_kit(
                frame_paths=frames,
                profile_id=proj.get("profile_id", "generico"),
                category=m.get("category", "combate_intenso"),
                video_name=proj.get("name", "gameplay"),
                previous_title=m.get("title", ""),
                previous_hook=m.get("hook_2s", ""),
                variation_index=next_var,
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
    """1-Click Automatic Mode: Analyze Multimodal -> Qwen 3.5 Vision -> Render Top 5 -> Viral Kit -> Ready for Library."""
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado para o Modo Automatico.", category="ANALISE", status_code=404)

    profile_id = req.profile_id if req.profile_id in GAME_PROFILES else proj.get("profile_id", "generico")
    top_n = max(1, min(20, int(req.top_n or 5)))
    job = _create_job("auto_pipeline", f"Modo Automatico Top {top_n}: {proj['name']}")

    def _worker() -> None:
        rendered_clips = []
        try:
            # Stage 1 (0% -> 52%): Multimodal Analysis + Qwen 3.5 Vision
            def _analyze_cb(pct: int, msg: str) -> None:
                mapped = max(2, int(pct * 0.50))
                _update_job(job["id"], progress=mapped, message=f"[Etapa 1/2 Analise] {msg}")

            result = analyze_gameplay(
                video_path=proj["video_path"],
                profile_id=profile_id,
                project_id=project_id,
                progress_cb=_analyze_cb,
            )

            long_edit = result["long_edit"]
            yt_kit = generate_youtube_long_kit(
                video_name=proj["name"],
                profile_id=profile_id,
                chapters=long_edit["chapters"],
                total_duration_str=long_edit["edited_duration_str"],
            )

            proj["profile_id"] = profile_id
            proj["meta"] = result["meta"]
            proj["moments"] = result["moments"]
            proj["boring_skipped_sec"] = result["boring_skipped_sec"]
            proj["long_edit"] = long_edit
            proj["youtube_kit"] = yt_kit
            proj["status"] = "analyzed"
            upsert_project(proj)

            targets = (result["moments"] or [])[:top_n]
            if not targets:
                raise XRayError(
                    "Nenhum momento epico foi detectado neste video para o Modo Automatico.",
                    category="ANALISE",
                    status_code=400,
                )

            # Stage 2 (52% -> 99%): Continuous Render of Top N Clips + Viral Kit
            total = len(targets)
            for idx, m in enumerate(targets):
                pct = 52 + int((idx / total) * 45)
                _update_job(
                    job["id"],
                    progress=pct,
                    message=f"[Etapa 2/2 Render] Gerando clipe #{m['rank']} ({idx + 1}/{total}) — {m['category_label']}...",
                )
                res = render_viral_clip(
                    video_path=proj["video_path"],
                    moment=m,
                    style=req.style,
                    subtitles_mode=req.subtitles_mode,
                    subtitle_style=req.subtitle_style,
                    enable_hook=req.enable_hook,
                    enable_impact_zoom=req.enable_impact_zoom,
                    enable_preview_flash=req.enable_preview_flash,
                    enable_infinite_loop=req.enable_infinite_loop,
                    enable_cta_comment=req.enable_cta_comment,
                    enable_impact_sfx=req.enable_impact_sfx,
                    enable_smart_pacing=req.enable_smart_pacing,
                    enable_progress_bar=req.enable_progress_bar,
                    enable_audio_norm=req.enable_audio_norm,
                    enable_action_tracking=req.enable_action_tracking,
                    project_name=proj["name"],
                )
                clip_record = add_clip(
                    {
                        "project_id": project_id,
                        "project_name": proj["name"],
                        "profile_id": profile_id,
                        "detected_game": m.get("detected_game", ""),
                        "moment_id": m["id"],
                        "rank": m["rank"],
                        "score": m["score"],
                        "category": m["category"],
                        "category_label": m["category_label"],
                        "hook_2s": m["hook_2s"],
                        "cta_comment": m.get("cta_comment", "Voce escaparia dessa?"),
                        "title": m["title"],
                        "description": m["description"],
                        "hashtags": m.get("hashtags", []),
                        "thumb_url": m.get("thumb_url", ""),
                        "thumb_path": m.get("thumb_path", ""),
                        "variation_index": 0,
                        **res,
                    }
                )
                rendered_clips.append(clip_record)

            _update_job(
                job["id"],
                status="done",
                progress=100,
                message=f"Modo Automatico concluido! {len(rendered_clips)} clipe(s) prontos na Biblioteca para publicar.",
                result={"project": proj, "clips": rendered_clips, "count": len(rendered_clips)},
            )
        except Exception as exc:
            xray = build_xray_from_exception(
                exc,
                category="AUTO_PIPELINE",
                context={"project_id": project_id, "rendered_so_far": len(rendered_clips)},
            )
            _update_job(job["id"], status="error", message=xray["explanation_pt"], xray_error=xray)

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job["id"], "status": "running", "top_n": top_n}


# ---- Rendering: Mode 1 (Batch 9:16) & Mode 2 (Long 16:9) ----

@app.post("/api/projects/{project_id}/render-batch")
def api_render_batch(project_id: str, req: BatchRenderRequest) -> dict[str, Any]:
    proj = get_project(project_id)
    if not proj:
        raise XRayError("Projeto nao encontrado para renderizacao.", category="RENDER", status_code=404)

    all_moments = proj.get("moments") or []
    if not all_moments:
        raise XRayError(
            "Nenhum momento foi analisado ainda neste projeto. Execute a analise antes de exportar.",
            category="RENDER",
            status_code=400,
        )

    if req.moment_ids:
        id_set = set(req.moment_ids)
        targets = [m for m in all_moments if m["id"] in id_set]
    elif req.limit and req.limit > 0:
        targets = all_moments[: req.limit]
    else:
        targets = list(all_moments)

    if not targets:
        raise XRayError("Nenhum clipe selecionado para renderizar.", category="RENDER", status_code=400)

    job = _create_job("render_batch", f"Renderizando {len(targets)} clipes 9:16")

    def _worker() -> None:
        rendered_clips = []
        try:
            total = len(targets)
            for idx, m in enumerate(targets):
                pct = int((idx / total) * 95)
                _update_job(
                    job["id"],
                    progress=max(3, pct),
                    message=f"Renderizando clipe {idx + 1}/{total} (#{m['rank']} - {m['category_label']})...",
                )
                res = render_viral_clip(
                    video_path=proj["video_path"],
                    moment=m,
                    style=req.style,
                    subtitles_mode=req.subtitles_mode,
                    subtitle_style=req.subtitle_style,
                    enable_hook=req.enable_hook,
                    enable_impact_zoom=req.enable_impact_zoom,
                    enable_preview_flash=req.enable_preview_flash,
                    enable_infinite_loop=req.enable_infinite_loop,
                    enable_cta_comment=req.enable_cta_comment,
                    enable_impact_sfx=req.enable_impact_sfx,
                    enable_smart_pacing=req.enable_smart_pacing,
                    enable_progress_bar=req.enable_progress_bar,
                    enable_audio_norm=req.enable_audio_norm,
                    enable_action_tracking=req.enable_action_tracking,
                    project_name=proj["name"],
                )
                clip_record = add_clip(
                    {
                        "project_id": project_id,
                        "project_name": proj["name"],
                        "profile_id": proj.get("profile_id", "generico"),
                        "detected_game": m.get("detected_game", ""),
                        "moment_id": m["id"],
                        "rank": m["rank"],
                        "score": m["score"],
                        "category": m["category"],
                        "category_label": m["category_label"],
                        "hook_2s": m["hook_2s"],
                        "cta_comment": m.get("cta_comment", "Voce escaparia dessa?"),
                        "title": m["title"],
                        "description": m["description"],
                        "hashtags": m.get("hashtags", []),
                        "thumb_url": m.get("thumb_url", ""),
                        "thumb_path": m.get("thumb_path", ""),
                        "variation_index": int(m.get("variation_index", 0)),
                        **res,
                    }
                )
                rendered_clips.append(clip_record)

            _update_job(
                job["id"],
                status="done",
                progress=100,
                message=f"Concluido! {len(rendered_clips)} clipe(s) salvos na pasta de saida.",
                result={"clips": rendered_clips, "count": len(rendered_clips)},
            )
        except Exception as exc:
            xray = build_xray_from_exception(
                exc,
                category="RENDER",
                context={"project_id": project_id, "rendered_so_far": len(rendered_clips)},
            )
            _update_job(job["id"], status="error", message=xray["explanation_pt"], xray_error=xray)

    threading.Thread(target=_worker, daemon=True).start()
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

    job = _create_job("render_long", f"Exportando Gameplay 16:9 ({proj['name']})")

    def _worker() -> None:
        try:
            def _cb(pct: int, msg: str) -> None:
                _update_job(job["id"], progress=pct, message=msg)

            res = render_long_gameplay(
                video_path=proj["video_path"],
                keep_segments=long_edit["keep_segments"],
                best_thumb_sec=long_edit.get("best_thumb_sec", 0.0),
                project_name=proj["name"],
                progress_cb=_cb,
            )
            yt_kit = proj.get("youtube_kit") or {}
            clip_record = add_clip(
                {
                    "project_id": project_id,
                    "project_name": proj["name"],
                    "profile_id": proj.get("profile_id", "generico"),
                    "rank": 0,
                    "score": 100,
                    "category": "youtube_long",
                    "category_label": "Gameplay 16:9 YouTube",
                    "hook_2s": "",
                    "title": (yt_kit.get("titles") or [f"{proj['name']} - Gameplay Editada 1080p"])[0],
                    "description": yt_kit.get("description", ""),
                    "hashtags": [f"#{t.replace(' ', '')}" for t in (yt_kit.get("tags") or [])[:6]],
                    "youtube_kit": yt_kit,
                    **res,
                }
            )
            _update_job(
                job["id"],
                status="done",
                progress=100,
                message="Gameplay Longa 16:9 e Thumbnail 1080p exportadas com sucesso!",
                result={"clip": clip_record, "youtube_kit": yt_kit},
            )
        except Exception as exc:
            xray = build_xray_from_exception(exc, category="RENDER", context={"project_id": project_id, "mode": "long_16_9"})
            _update_job(job["id"], status="error", message=xray["explanation_pt"], xray_error=xray)

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job["id"], "status": "running"}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict[str, Any]:
    job = _get_job(job_id)
    if not job:
        raise XRayError("Tarefa em segundo plano nao encontrada.", category="SISTEMA", status_code=404)
    return job


# ---- Clip Library ----

@app.get("/api/clips")
def api_list_clips() -> dict[str, Any]:
    return {"clips": list_clips()}


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
    regen = regenerate_viral_kit(
        frame_paths=frames,
        profile_id=clip.get("profile_id", "generico"),
        category=clip.get("category", "combate_intenso"),
        video_name=clip.get("project_name") or clip.get("file_name") or "gameplay",
        previous_title=clip.get("title", ""),
        previous_hook=clip.get("hook_2s", ""),
        variation_index=next_var,
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
    try:
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict) or not ("installed" in parsed or "web" in parsed):
            raise ValueError("JSON sem estrutura 'installed' ou 'web'.")
    except Exception as exc:
        raise XRayError(
            "O JSON enviado nao parece um arquivo client_secrets.json valido do Google Cloud.",
            category="YOUTUBE_AUTH",
            technical_detail=str(exc),
            status_code=400,
        )
    dest = DATA_DIR / "client_secrets.json"
    dest.write_bytes(raw)
    save_settings({"youtube_client_secrets_path": str(dest)})
    return {"ok": True, "path": str(dest), "status": get_youtube_connection_status()}


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
    )


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
def api_youtube_autopilot_publish_now(item_id: str) -> dict[str, Any]:
    return publish_autopilot_item(item_id, mark_daily_slot=False)


# ---- Disk Cleanup ----

@app.get("/api/disk/scan")
def api_disk_scan() -> dict[str, Any]:
    return scan_disk_usage()


@app.post("/api/disk/clean")
def api_disk_clean(req: DiskCleanRequest) -> dict[str, Any]:
    return clean_disk_buckets(req.bucket_ids)


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


# ---- Media & Static Mounts ----

app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
app.mount("/media/thumbs", StaticFiles(directory=str(THUMBS_DIR)), name="media_thumbs")
app.mount("/media/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="media_uploads")


@app.get("/media/saida/{filename}")
def serve_output_media(filename: str):
    target = (get_output_dir() / filename).resolve()
    if not target.exists() or target.parent != get_output_dir().resolve():
        raise HTTPException(status_code=404, detail="Arquivo de saida nao encontrado")
    return FileResponse(str(target))


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def serve_index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
