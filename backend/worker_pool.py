"""Isolated Process Worker Manager for ClipRush Studio Pro.
Runs heavy CPU/GPU tasks (OpenCV Optical Flow, Whisper, Qwen 3.5 Vision, FFmpeg renders)
in an isolated child OS process (max 1 concurrent heavy job) with BELOW_NORMAL priority so
the FastAPI HTTP server & MP4 video players NEVER freeze or contend for the Python GIL.
Supports real-time progress updates and instant cancellation (terminating child FFmpeg trees).
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing as mp
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from backend.config import TEMP_DIR, load_settings
from backend.errors import build_xray_from_exception
from backend.hw_guard import CPUGuard, set_below_normal_priority

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.RLock()
_EXEC_SEMAPHORE = threading.Semaphore(1)  # Strict P0 hardware guard: max 1 heavy task (FFmpeg OR Whisper OR Qwen)
_ACTIVE_PROCS: dict[str, mp.Process] = {}


@contextlib.contextmanager
def exclusive_heavy_task() -> Iterator[None]:
    """Acquire the global single-heavy-task semaphore so FFmpeg, Whisper, and Qwen NEVER run concurrently."""
    _EXEC_SEMAPHORE.acquire()
    try:
        yield
    finally:
        _EXEC_SEMAPHORE.release()


def _job_progress_file(job_id: str) -> Path:
    return TEMP_DIR / f"job_state_{job_id}.json"


def _job_cancel_file(job_id: str) -> Path:
    return TEMP_DIR / f"job_cancel_{job_id}.flag"


def _write_progress_file(job_id: str, payload: dict[str, Any]) -> None:
    fp = _job_progress_file(job_id)
    tmp = fp.with_suffix(f".{os.getpid()}.tmp")
    raw = json.dumps(payload, ensure_ascii=False)
    for attempt in range(15):
        try:
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(fp)
            return
        except (PermissionError, OSError):
            time.sleep(0.025 * (attempt + 1))
    # Final fallback: write directly in-place so terminal done/error state is NEVER lost on Windows
    try:
        fp.write_text(raw, encoding="utf-8")
    except Exception:
        pass
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _read_progress_file(job_id: str) -> Optional[dict[str, Any]]:
    fp = _job_progress_file(job_id)
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _child_entry(job_id: str, task_kind: str, kwargs: dict[str, Any]) -> None:
    """Entry point executed inside the isolated OS child process (completely outside Uvicorn's GIL)."""
    # Lower own priority to BELOW_NORMAL on Windows so browser & FastAPI HTTP always stay instant
    set_below_normal_priority()

    cancel_fp = _job_cancel_file(job_id)

    def _is_cancelled() -> bool:
        return cancel_fp.exists()

    def _report(progress: int, message: str, status: str = "running", result: Any = None, xray_error: Any = None) -> None:
        _write_progress_file(
            job_id,
            {
                "status": status,
                "progress": progress,
                "message": message,
                "result": result,
                "xray_error": xray_error,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    try:
        if task_kind == "analyze":
            result = _run_analyze_task(kwargs, _report, _is_cancelled)
        elif task_kind == "auto_pipeline":
            result = _run_auto_pipeline_task(kwargs, _report, _is_cancelled)
        elif task_kind == "render_batch":
            result = _run_render_batch_task(kwargs, _report, _is_cancelled)
        elif task_kind == "render_long":
            result = _run_render_long_task(kwargs, _report, _is_cancelled)
        else:
            raise ValueError(f"Unknown task kind: {task_kind}")

        if _is_cancelled():
            _report(0, "Processamento cancelado pelo usuario.", status="cancelled")
        else:
            final_msg = result.get("_done_message") if isinstance(result, dict) else "Concluido!"
            _report(100, final_msg or "Concluido!", status="done", result=result)
    except InterruptedError:
        _report(0, "Processamento cancelado pelo usuario.", status="cancelled")
    except Exception as exc:
        cat = {
            "analyze": "ANALISE",
            "auto_pipeline": "AUTO_PIPELINE",
            "render_batch": "RENDER",
            "render_long": "RENDER",
        }.get(task_kind, "SISTEMA")
        xray = build_xray_from_exception(exc, category=cat, context={"job_id": job_id, "task": task_kind})
        _report(0, xray["explanation_pt"], status="error", xray_error=xray)


# ---- Isolated Task Implementations ----

def _ensure_unique_vision_copy(proj: dict[str, Any], moment: dict[str, Any], used_titles: list[str], used_hooks: list[str], clip_index: int) -> None:
    from backend.ollama_client import analyze_moment_with_qwen
    from backend.storage import find_game_sheet

    title_l = (moment.get("title") or "").strip().lower()
    hook_l = (moment.get("hook_2s") or "").strip().lower()
    used_t_set = {t.strip().lower() for t in used_titles if t}
    used_h_set = {h.strip().lower() for h in used_hooks if h}

    needs_refresh = (not moment.get("ai_powered")) or (title_l in used_t_set) or (hook_l in used_h_set)
    if needs_refresh:
        frames = [
            p for p in (moment.get("thumb_path"), moment.get("after_thumb_path"))
            if p and Path(p).exists()
        ]
        g_name = str(proj.get("game_name") or "").strip()
        g_sheet = proj.get("game_sheet") or (find_game_sheet(g_name) if g_name else None)
        ai_info = analyze_moment_with_qwen(
            frame_paths=frames,
            profile_id=proj.get("profile_id", "generico"),
            heuristic_category=moment.get("category", "combate_intenso"),
            heuristic_score=moment.get("score", 75),
            signals_summary=f"tempo={moment.get('start_str', '00:00')}-{moment.get('end_str', '00:00')}",
            video_name=proj.get("name", "gameplay"),
            used_titles=used_titles,
            used_hooks=used_hooks,
            clip_index=clip_index,
            platform_target=moment.get("platform_target", "youtube_shorts"),
            game_sheet=g_sheet,
            game_name=g_name,
        )
        moment["hook_2s"] = ai_info["hook_2s"]
        moment["cta_comment"] = ai_info.get("cta_comment", moment.get("cta_comment", "Voce escaparia dessa?"))
        moment["title"] = ai_info["title"]
        moment["description"] = ai_info["description"]
        moment["hashtags"] = ai_info["hashtags"]
        moment["detected_game"] = ai_info.get("detected_game", moment.get("detected_game", ""))
        moment["ai_powered"] = ai_info.get("ai_powered", False)

    used_titles.append(moment["title"])
    used_hooks.append(moment["hook_2s"])


def _run_analyze_task(kwargs: dict[str, Any], report: Callable, is_cancelled: Callable[[], bool]) -> dict[str, Any]:
    from backend.analyzer import analyze_gameplay
    from backend.ollama_client import generate_youtube_long_kit
    from backend.storage import find_game_sheet, get_project, upsert_project

    project_id = kwargs["project_id"]
    profile_id = kwargs["profile_id"]
    target_mode = kwargs.get("target_mode", "hibrido")
    scene_mode = kwargs.get("scene_mode")
    game_name = str(kwargs.get("game_name") or "").strip()
    proj = get_project(project_id)
    if not proj:
        raise RuntimeError("Projeto nao encontrado.")

    eff_game_name = game_name or str(proj.get("game_name") or "").strip()
    eff_sheet = kwargs.get("game_sheet") or proj.get("game_sheet") or (find_game_sheet(eff_game_name) if eff_game_name else None)

    result = analyze_gameplay(
        video_path=proj["video_path"],
        profile_id=profile_id,
        project_id=project_id,
        progress_cb=lambda pct, msg: report(pct, msg),
        cancel_check=is_cancelled,
        target_mode=target_mode,
        scene_mode=scene_mode,
        game_sheet=eff_sheet,
        game_name=eff_game_name,
    )

    if is_cancelled():
        raise InterruptedError()

    report(96, "Etapa 4/4: Montando capitulos e SEO para o YouTube...")
    long_edit = result["long_edit"]
    yt_kit = generate_youtube_long_kit(
        video_name=proj["name"],
        profile_id=profile_id,
        chapters=long_edit["chapters"],
        total_duration_str=long_edit["edited_duration_str"],
    )

    proj["profile_id"] = profile_id
    proj["target_mode"] = target_mode
    if eff_game_name:
        proj["game_name"] = eff_game_name
    if eff_sheet:
        proj["game_sheet"] = eff_sheet
    proj["meta"] = result["meta"]
    proj["moments"] = result["moments"]
    proj["boring_skipped_sec"] = result["boring_skipped_sec"]
    proj["long_edit"] = long_edit
    proj["youtube_kit"] = yt_kit
    proj["status"] = "analyzed"
    upsert_project(proj)
    proj["_done_message"] = f"Analise concluida! {len(proj['moments'])} cenas completas (25s-55s) encontradas."
    return proj


def _run_auto_pipeline_task(kwargs: dict[str, Any], report: Callable, is_cancelled: Callable[[], bool]) -> dict[str, Any]:
    from backend.analyzer import analyze_gameplay
    from backend.editor import render_viral_clip
    from backend.errors import XRayError
    from backend.ollama_client import generate_youtube_long_kit
    from backend.storage import (
        add_clip,
        annotate_moments_export_status,
        find_game_sheet,
        get_project,
        list_clips,
        record_exported_interval,
        upsert_project,
    )

    project_id = kwargs["project_id"]
    profile_id = kwargs["profile_id"]
    req = kwargs["req"]
    top_n = int(req.get("top_n", 10))
    target_mode = req.get("target_mode", "hibrido")
    scene_mode = req.get("scene_mode")
    req_game = str(req.get("game_name") or "").strip()

    proj = get_project(project_id)
    if not proj:
        raise RuntimeError("Projeto nao encontrado.")

    eff_game = req_game or str(proj.get("game_name") or "").strip()
    eff_sheet = find_game_sheet(eff_game) if eff_game else proj.get("game_sheet")

    need_analyze = (
        req.get("force_reanalyze")
        or not proj.get("moments")
        or proj.get("profile_id") != profile_id
        or proj.get("target_mode") != target_mode
        or (eff_game and proj.get("game_name") != eff_game)
    )
    if need_analyze:
        result = analyze_gameplay(
            video_path=proj["video_path"],
            profile_id=profile_id,
            project_id=project_id,
            progress_cb=lambda pct, msg: report(max(2, int(pct * 0.44)), msg),
            cancel_check=is_cancelled,
            target_mode=target_mode,
            scene_mode=scene_mode,
            game_sheet=eff_sheet,
            game_name=eff_game,
        )
        if is_cancelled():
            raise InterruptedError()

        long_edit = result["long_edit"]
        yt_kit = generate_youtube_long_kit(
            video_name=proj["name"],
            profile_id=profile_id,
            chapters=long_edit["chapters"],
            total_duration_str=long_edit["edited_duration_str"],
        )
        proj["profile_id"] = profile_id
        proj["target_mode"] = target_mode
        if eff_game:
            proj["game_name"] = eff_game
        if eff_sheet:
            proj["game_sheet"] = eff_sheet
        proj["meta"] = result["meta"]
        proj["moments"] = result["moments"]
        proj["boring_skipped_sec"] = result["boring_skipped_sec"]
        proj["long_edit"] = long_edit
        proj["youtube_kit"] = yt_kit
        proj["status"] = "analyzed"
        upsert_project(proj)
    else:
        report(44, "Etapa 2/4: Reaproveitando cenas ja analisadas e filtrando trechos ineditos...")

    all_moments = annotate_moments_export_status(proj["video_path"], proj.get("moments") or [])
    pool = [m for m in all_moments if not m.get("already_exported")] if req.get("skip_exported", True) else list(all_moments)
    if not pool:
        raise XRayError(
            "Todos os momentos detectados deste video ja foram exportados! Clique em 'Resetar Historico' ou importe uma nova gameplay.",
            category="ANALISE",
            status_code=400,
        )

    targets = pool if top_n <= 0 else pool[:top_n]
    existing_proj_clips = [c for c in list_clips() if c.get("project_id") == project_id]
    used_titles = [c.get("title", "") for c in existing_proj_clips if c.get("title")]
    used_hooks = [c.get("hook_2s", "") for c in existing_proj_clips if c.get("hook_2s")]
    base_idx = len(existing_proj_clips)

    rendered_clips = []
    total = len(targets)
    src_dur = (proj.get("meta") or {}).get("duration")
    cpu_guard = CPUGuard(threshold_pct=float(load_settings().get("cpu_pause_threshold_pct", 80)), sustain_sec=5.0)
    for idx, m in enumerate(targets):
        if is_cancelled():
            raise InterruptedError()
        pct = 46 + int((idx / total) * 50)
        cpu_guard.check_and_pause(cancel_check=is_cancelled, progress_cb=report, current_pct=pct)
        report(pct, f"Etapa 3/4: Gerando clipe {idx + 1} de {total} ({round(m['duration'])}s) com zoom e legenda...")
        _ensure_unique_vision_copy(proj, m, used_titles, used_hooks, base_idx + idx)

        if is_cancelled():
            raise InterruptedError()

        res = render_viral_clip(
            video_path=proj["video_path"],
            moment=m,
            style=req.get("style", "viral_blur"),
            subtitles_mode=req.get("subtitles_mode", "whisper"),
            subtitle_style=req.get("subtitle_style", "amarelo_viral"),
            enable_hook=req.get("enable_hook", True),
            enable_impact_zoom=req.get("enable_impact_zoom", True),
            enable_preview_flash=req.get("enable_preview_flash", True),
            enable_infinite_loop=req.get("enable_infinite_loop", True),
            enable_cta_comment=req.get("enable_cta_comment", True),
            enable_impact_sfx=req.get("enable_impact_sfx", True),
            enable_smart_pacing=req.get("enable_smart_pacing", True),
            enable_progress_bar=req.get("enable_progress_bar", True),
            enable_audio_norm=req.get("enable_audio_norm", True),
            enable_action_tracking=req.get("enable_action_tracking", True),
            enable_slowmo=req.get("enable_slowmo", True),
            project_name=proj["name"],
            overlay_config=req.get("overlay_config"),
            enable_anti_factory=bool(req.get("enable_anti_factory", True)),
            clip_index=idx,
            enable_seamless_loop=bool(req.get("enable_seamless_loop", False)),
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
                "platform_target": m.get("platform_target", "youtube_shorts"),
                "platform_badge": m.get("platform_badge", "Ideal YouTube Shorts"),
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
        record_exported_interval(proj["video_path"], m["start"], m["end"], clip_id=clip_record["id"], duration=src_dur)
        m["already_exported"] = True
        rendered_clips.append(clip_record)

    report(98, "Etapa 4/4: Salvando historico e atualizando Biblioteca de Clipes...")
    annotate_moments_export_status(proj["video_path"], proj["moments"], duration=src_dur)
    upsert_project(proj)
    remaining = sum(1 for m in proj["moments"] if not m.get("already_exported"))
    return {
        "project": proj,
        "clips": rendered_clips,
        "count": len(rendered_clips),
        "remaining_unseen": remaining,
        "_done_message": f"Concluido! {len(rendered_clips)} clipe(s) prontos na Biblioteca (restam {remaining} ineditos).",
    }


def _run_render_batch_task(kwargs: dict[str, Any], report: Callable, is_cancelled: Callable[[], bool]) -> dict[str, Any]:
    from backend.editor import render_viral_clip
    from backend.storage import (
        add_clip,
        annotate_moments_export_status,
        get_project,
        list_clips,
        record_exported_interval,
        upsert_project,
    )

    project_id = kwargs["project_id"]
    target_ids = set(kwargs["target_ids"])
    req = kwargs["req"]

    proj = get_project(project_id)
    if not proj:
        raise RuntimeError("Projeto nao encontrado.")

    targets = [m for m in (proj.get("moments") or []) if m["id"] in target_ids]
    existing_proj_clips = [c for c in list_clips() if c.get("project_id") == project_id]
    used_titles = [c.get("title", "") for c in existing_proj_clips if c.get("title")]
    used_hooks = [c.get("hook_2s", "") for c in existing_proj_clips if c.get("hook_2s")]
    base_idx = len(existing_proj_clips)

    rendered_clips = []
    total = len(targets)
    src_dur = (proj.get("meta") or {}).get("duration")
    cpu_guard = CPUGuard(threshold_pct=float(load_settings().get("cpu_pause_threshold_pct", 80)), sustain_sec=5.0)
    for idx, m in enumerate(targets):
        if is_cancelled():
            raise InterruptedError()
        pct = max(5, int((idx / max(1, total)) * 92))
        cpu_guard.check_and_pause(cancel_check=is_cancelled, progress_cb=report, current_pct=pct)
        report(pct, f"Etapa 3/4: Gerando clipe {idx + 1} de {total} ({round(m['duration'])}s) com zoom e legenda...")
        _ensure_unique_vision_copy(proj, m, used_titles, used_hooks, base_idx + idx)

        if is_cancelled():
            raise InterruptedError()

        res = render_viral_clip(
            video_path=proj["video_path"],
            moment=m,
            style=req.get("style", "viral_blur"),
            subtitles_mode=req.get("subtitles_mode", "whisper"),
            subtitle_style=req.get("subtitle_style", "amarelo_viral"),
            enable_hook=req.get("enable_hook", True),
            enable_impact_zoom=req.get("enable_impact_zoom", True),
            enable_preview_flash=req.get("enable_preview_flash", True),
            enable_infinite_loop=req.get("enable_infinite_loop", True),
            enable_cta_comment=req.get("enable_cta_comment", True),
            enable_impact_sfx=req.get("enable_impact_sfx", True),
            enable_smart_pacing=req.get("enable_smart_pacing", True),
            enable_progress_bar=req.get("enable_progress_bar", True),
            enable_audio_norm=req.get("enable_audio_norm", True),
            enable_action_tracking=req.get("enable_action_tracking", True),
            enable_slowmo=req.get("enable_slowmo", True),
            project_name=proj["name"],
            overlay_config=req.get("overlay_config"),
            enable_anti_factory=bool(req.get("enable_anti_factory", True)),
            clip_index=idx,
            enable_seamless_loop=bool(req.get("enable_seamless_loop", False)),
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
                "platform_target": m.get("platform_target", "youtube_shorts"),
                "platform_badge": m.get("platform_badge", "Ideal YouTube Shorts"),
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
        record_exported_interval(proj["video_path"], m["start"], m["end"], clip_id=clip_record["id"], duration=src_dur)
        m["already_exported"] = True
        rendered_clips.append(clip_record)

    report(97, "Etapa 4/4: Finalizando arquivos MP4 e atualizando Biblioteca...")
    annotate_moments_export_status(proj["video_path"], proj["moments"], duration=src_dur)
    upsert_project(proj)
    remaining = sum(1 for m in proj["moments"] if not m.get("already_exported"))
    return {
        "clips": rendered_clips,
        "count": len(rendered_clips),
        "remaining_unseen": remaining,
        "project": proj,
        "_done_message": f"Concluido! {len(rendered_clips)} clipe(s) salvos na Biblioteca (restam {remaining} ineditos).",
    }


def _run_render_long_task(kwargs: dict[str, Any], report: Callable, is_cancelled: Callable[[], bool]) -> dict[str, Any]:
    from backend.editor import render_long_gameplay
    from backend.storage import add_clip, get_project

    project_id = kwargs["project_id"]
    proj = get_project(project_id)
    if not proj:
        raise RuntimeError("Projeto nao encontrado.")
    long_edit = proj["long_edit"]
    res = render_long_gameplay(
        video_path=proj["video_path"],
        keep_segments=long_edit["keep_segments"],
        best_thumb_sec=long_edit.get("best_thumb_sec", 0.0),
        project_name=proj["name"],
        progress_cb=lambda pct, msg: report(pct, f"Etapa 3/4: {msg}"),
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
    return {
        "clip": clip_record,
        "youtube_kit": yt_kit,
        "_done_message": "Gameplay Longa 16:9 e Thumbnail 1080p exportadas com sucesso!",
    }


# ---- Public Job Orchestrator API (Called by FastAPI) ----

def submit_isolated_job(kind: str, label: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Queue and run a heavy task in an isolated child process (max 1 concurrent) without blocking FastAPI."""
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "id": job_id,
        "kind": kind,
        "label": label,
        "status": "running",  # running | done | error | cancelled
        "progress": 1,
        "message": "Etapa 1/4: Iniciando motor isolado em segundo plano...",
        "result": None,
        "xray_error": None,
        "created_at": now,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    _write_progress_file(job_id, {"status": "running", "progress": 1, "message": job["message"]})

    def _supervisor() -> None:
        with _EXEC_SEMAPHORE:
            if _job_cancel_file(job_id).exists():
                with _JOBS_LOCK:
                    _JOBS[job_id].update({"status": "cancelled", "message": "Processamento cancelado pelo usuario."})
                return

            ctx = mp.get_context("spawn")
            proc = ctx.Process(target=_child_entry, args=(job_id, kind, kwargs), daemon=True)
            with _JOBS_LOCK:
                _ACTIVE_PROCS[job_id] = proc
            proc.start()

            try:
                while proc.is_alive():
                    latest = _read_progress_file(job_id)
                    if latest:
                        with _JOBS_LOCK:
                            _JOBS[job_id].update(
                                {
                                    "progress": latest.get("progress", _JOBS[job_id]["progress"]),
                                    "message": latest.get("message", _JOBS[job_id]["message"]),
                                }
                            )
                    time.sleep(0.35)
                proc.join(timeout=2.0)

                latest = _read_progress_file(job_id)
                with _JOBS_LOCK:
                    if _job_cancel_file(job_id).exists() or (latest and latest.get("status") == "cancelled"):
                        _JOBS[job_id].update({"status": "cancelled", "message": "Processamento cancelado pelo usuario."})
                    elif latest and latest.get("status") in ("done", "error"):
                        _JOBS[job_id].update(latest)
                    elif proc.exitcode != 0:
                        _JOBS[job_id].update(
                            {
                                "status": "error",
                                "message": f"O processo de renderizacao foi interrompido (codigo {proc.exitcode}).",
                            }
                        )
                    else:
                        _JOBS[job_id].update(
                            {
                                "status": "error",
                                "message": "O processo terminou sem gravar o resultado final. Tente novamente.",
                            }
                        )
            finally:
                with _JOBS_LOCK:
                    _ACTIVE_PROCS.pop(job_id, None)
                # Clean temporary flag/progress files after a brief retention window
                for tmp_f in (_job_progress_file(job_id), _job_cancel_file(job_id)):
                    try:
                        if tmp_f.exists():
                            tmp_f.unlink()
                    except OSError:
                        pass

    threading.Thread(target=_supervisor, daemon=True, name=f"Supervisor-{job_id}").start()
    return dict(job)


def get_job_state(job_id: str) -> Optional[dict[str, Any]]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        snapshot = dict(job)
    # Merge freshest child progress file if still running
    if snapshot["status"] == "running":
        latest = _read_progress_file(job_id)
        if latest:
            snapshot["progress"] = latest.get("progress", snapshot["progress"])
            snapshot["message"] = latest.get("message", snapshot["message"])
    return snapshot


def get_active_analysis_cut() -> Optional[int]:
    """Return active moment/cut index being analyzed by Qwen right now (for 5-state indicator), or None."""
    import re
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job.get("status") == "running":
                msg = str(job.get("message") or "")
                m = re.search(r"(?:momento|corte|clipe)\s*#?(\d+)", msg, flags=re.IGNORECASE)
                if m and ("IA analisando" in msg or "Qwen" in msg):
                    return int(m.group(1))
    return None


def cancel_isolated_job(job_id: str) -> dict[str, Any]:
    """Immediately cancel a running or queued job and kill its isolated child FFmpeg/OpenCV tree."""
    with _JOBS_LOCK:
        if job_id not in _JOBS:
            return {"ok": False, "message": "Tarefa nao encontrada."}
        proc = _ACTIVE_PROCS.get(job_id)
        _JOBS[job_id]["status"] = "cancelled"
        _JOBS[job_id]["message"] = "Cancelando processamento..."

    try:
        _job_cancel_file(job_id).write_text("cancel", encoding="utf-8")
    except OSError:
        pass

    if proc is not None and proc.is_alive() and proc.pid:
        if os.name == "nt":
            # Kill entire child process tree (/T) so active FFmpeg stops immediately!
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            proc.terminate()

    with _JOBS_LOCK:
        _JOBS[job_id]["status"] = "cancelled"
        _JOBS[job_id]["message"] = "Processamento cancelado pelo usuario."
    return {"ok": True, "id": job_id, "status": "cancelled"}
