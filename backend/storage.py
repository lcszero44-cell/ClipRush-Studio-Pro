"""Persistent JSON storage for projects, clip library, scheduled posts, and disk cleanup."""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import DATA_DIR, TEMP_DIR, THUMBS_DIR, UPLOADS_DIR, get_output_dir

_LOCK = threading.RLock()
PROJECTS_FILE = DATA_DIR / "projects.json"
CLIPS_FILE = DATA_DIR / "clips_library.json"
SCHEDULE_FILE = DATA_DIR / "schedule.json"


def _read_json(path: Path, default: Any) -> Any:
    with _LOCK:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default


def _write_json(path: Path, data: Any) -> None:
    with _LOCK:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


# ---- Projects ----

def list_projects() -> list[dict[str, Any]]:
    projects = _read_json(PROJECTS_FILE, [])
    return sorted(projects, key=lambda x: x.get("created_at", ""), reverse=True)


def get_project(project_id: str) -> Optional[dict[str, Any]]:
    for proj in _read_json(PROJECTS_FILE, []):
        if proj.get("id") == project_id:
            return proj
    return None


def upsert_project(project: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        projects = _read_json(PROJECTS_FILE, [])
        now = datetime.now(timezone.utc).isoformat()
        if not project.get("id"):
            project["id"] = f"proj_{uuid.uuid4().hex[:10]}"
        project.setdefault("created_at", now)
        project["updated_at"] = now
        replaced = False
        for i, existing in enumerate(projects):
            if existing.get("id") == project["id"]:
                projects[i] = project
                replaced = True
                break
        if not replaced:
            projects.insert(0, project)
        _write_json(PROJECTS_FILE, projects)
        return project


# ---- Clip Library ----

def list_clips() -> list[dict[str, Any]]:
    clips = _read_json(CLIPS_FILE, [])
    # Mark whether output file still exists on disk
    valid = []
    for c in clips:
        fpath = c.get("file_path", "")
        c["file_exists"] = bool(fpath and Path(fpath).exists())
        if c["file_exists"]:
            try:
                c["file_size_mb"] = round(Path(fpath).stat().st_size / (1024 * 1024), 2)
            except OSError:
                c["file_size_mb"] = c.get("file_size_mb", 0)
        valid.append(c)
    return sorted(valid, key=lambda x: x.get("created_at", ""), reverse=True)


def add_clip(clip: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        clips = _read_json(CLIPS_FILE, [])
        clip.setdefault("id", f"clip_{uuid.uuid4().hex[:10]}")
        clip.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        clips.insert(0, clip)
        _write_json(CLIPS_FILE, clips)
        return clip


def get_clip(clip_id: str) -> Optional[dict[str, Any]]:
    for c in _read_json(CLIPS_FILE, []):
        if c.get("id") == clip_id:
            return c
    return None


def update_clip(clip_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    with _LOCK:
        clips = _read_json(CLIPS_FILE, [])
        for c in clips:
            if c.get("id") == clip_id:
                c.update(updates)
                c["updated_at"] = datetime.now(timezone.utc).isoformat()
                _write_json(CLIPS_FILE, clips)
                return c
        return None


def delete_clip(clip_id: str, delete_file: bool = False) -> bool:
    with _LOCK:
        clips = _read_json(CLIPS_FILE, [])
        target = None
        remaining = []
        for c in clips:
            if c.get("id") == clip_id:
                target = c
            else:
                remaining.append(c)
        if not target:
            return False
        if delete_file and target.get("file_path"):
            try:
                p = Path(target["file_path"])
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        _write_json(CLIPS_FILE, remaining)
        return True


# ---- Scheduler & Publishing ----

def list_scheduled() -> list[dict[str, Any]]:
    items = _read_json(SCHEDULE_FILE, [])
    return sorted(items, key=lambda x: x.get("scheduled_for", ""))


def add_scheduled(item: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        items = _read_json(SCHEDULE_FILE, [])
        item.setdefault("id", f"sched_{uuid.uuid4().hex[:10]}")
        item.setdefault("status", "agendado")
        item.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        items.append(item)
        _write_json(SCHEDULE_FILE, items)
        return item


def update_scheduled_status(item_id: str, status: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        items = _read_json(SCHEDULE_FILE, [])
        for it in items:
            if it.get("id") == item_id:
                it["status"] = status
                it["updated_at"] = datetime.now(timezone.utc).isoformat()
                _write_json(SCHEDULE_FILE, items)
                return it
        return None


def delete_scheduled(item_id: str) -> bool:
    with _LOCK:
        items = _read_json(SCHEDULE_FILE, [])
        new_items = [it for it in items if it.get("id") != item_id]
        if len(new_items) == len(items):
            return False
        _write_json(SCHEDULE_FILE, new_items)
        return True


# ---- Disk Cleanup ----

def _dir_stats(path: Path) -> tuple[int, int]:
    total_bytes = 0
    file_count = 0
    if not path.exists():
        return 0, 0
    for root, _, files in os.walk(path):
        for fname in files:
            fp = Path(root) / fname
            try:
                total_bytes += fp.stat().st_size
                file_count += 1
            except OSError:
                pass
    return total_bytes, file_count


def scan_disk_usage() -> dict[str, Any]:
    out_dir = get_output_dir()
    buckets = [
        {
            "id": "temp",
            "name": "Cache Temporario & Audio PCM",
            "description": "Arquivos temporarios de analise, textos de filtros FFmpeg e audio extraido.",
            "path": str(TEMP_DIR),
            "safe_default": True,
        },
        {
            "id": "thumbs",
            "name": "Miniaturas & Frames de Analise",
            "description": "Frames capturados durante a visao computacional e miniaturas de preview.",
            "path": str(THUMBS_DIR),
            "safe_default": True,
        },
        {
            "id": "uploads",
            "name": "Videos Enviados (Uploads)",
            "description": "Copias de gameplays enviadas via arrastar-e-soltar para a pasta interna.",
            "path": str(UPLOADS_DIR),
            "safe_default": False,
        },
        {
            "id": "saida",
            "name": "Pasta de Saida (Clipes Renderizados)",
            "description": "Videos MP4 finais exportados. Limpe somente apos ja ter publicado ou salvo backup.",
            "path": str(out_dir),
            "safe_default": False,
        },
    ]
    total_bytes = 0
    for b in buckets:
        size_b, count = _dir_stats(Path(b["path"]))
        b["bytes"] = size_b
        b["size_mb"] = round(size_b / (1024 * 1024), 2)
        b["file_count"] = count
        total_bytes += size_b

    disk_free_gb = 0.0
    try:
        usage = shutil.disk_usage(str(DATA_DIR))
        disk_free_gb = round(usage.free / (1024 ** 3), 2)
    except OSError:
        pass

    return {
        "buckets": buckets,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
        "disk_free_gb": disk_free_gb,
    }


def clean_disk_buckets(bucket_ids: list[str]) -> dict[str, Any]:
    mapping = {
        "temp": TEMP_DIR,
        "thumbs": THUMBS_DIR,
        "uploads": UPLOADS_DIR,
        "saida": get_output_dir(),
    }
    freed_bytes = 0
    removed_files = 0
    for bid in bucket_ids:
        target = mapping.get(bid)
        if not target or not target.exists():
            continue
        for item in list(target.iterdir()):
            try:
                if item.is_file():
                    sz = item.stat().st_size
                    item.unlink()
                    freed_bytes += sz
                    removed_files += 1
                elif item.is_dir():
                    sz, cnt = _dir_stats(item)
                    shutil.rmtree(item, ignore_errors=True)
                    freed_bytes += sz
                    removed_files += cnt
            except OSError:
                pass
    return {
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "removed_files": removed_files,
        "buckets_cleaned": bucket_ids,
    }
