"""PS4 Share Watch Folder Monitor for ClipRush Studio Pro (P1):
- Watches E:\\PS4\\SHARE\\Video Clips (or custom configured folder) in a lightweight 5s daemon thread.
- Confirms file copy completion (stable byte size across 2 polls) before queuing new MP4/MKV/MOV clips.
- Persists queue in data/watch_queue.json with atomic writes so USB drives can be plugged/unplugged safely.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import DATA_DIR, load_settings, save_settings

WATCH_QUEUE_FILE = DATA_DIR / "watch_queue.json"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}

_LOCK = threading.RLock()
_STARTED = False
_SIZE_SNAPSHOT: dict[str, int] = {}
_LAST_SCAN_AT = ""
_LAST_ERROR = ""


def _read_queue() -> list[dict[str, Any]]:
    with _LOCK:
        if not WATCH_QUEUE_FILE.exists():
            return []
        try:
            data = json.loads(WATCH_QUEUE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []


def _write_queue(items: list[dict[str, Any]]) -> None:
    with _LOCK:
        tmp = WATCH_QUEUE_FILE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(WATCH_QUEUE_FILE)


def scan_watch_folder_now(require_stability: bool = False) -> dict[str, Any]:
    """Scan configured PS4 watch folder for new video files and enqueue any unseen clips."""
    global _LAST_SCAN_AT, _LAST_ERROR
    settings = load_settings()
    folder_str = str(settings.get("watch_folder_path") or r"E:\PS4\SHARE\Video Clips").strip()
    folder = Path(folder_str)
    _LAST_SCAN_AT = datetime.now(timezone.utc).isoformat()

    if not folder_str or not folder.exists() or not folder.is_dir():
        _LAST_ERROR = f"Pasta nao encontrada ou dispositivo desconectado: {folder_str}"
        return {
            "folder_exists": False,
            "folder_path": folder_str,
            "added": 0,
            "queue": _read_queue(),
            "last_scan_at": _LAST_SCAN_AT,
            "error": _LAST_ERROR,
        }

    _LAST_ERROR = ""
    queue = _read_queue()
    known_paths = {str(it.get("file_path") or "").lower() for it in queue}
    added = 0

    try:
        candidates = sorted(
            (p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )[:50]
    except OSError as exc:
        _LAST_ERROR = str(exc)
        candidates = []

    for fp in candidates:
        resolved = str(fp.resolve())
        key_l = resolved.lower()
        if key_l in known_paths:
            continue
        try:
            sz = fp.stat().st_size
        except OSError:
            continue
        if sz < 50_000:
            continue

        if require_stability:
            prev_sz = _SIZE_SNAPSHOT.get(key_l)
            _SIZE_SNAPSHOT[key_l] = sz
            if prev_sz is None or prev_sz != sz:
                # Still being copied from PS4/USB -> wait for next poll
                continue

        queue.insert(
            0,
            {
                "id": f"wq_{uuid.uuid4().hex[:8]}",
                "file_name": fp.name,
                "file_path": resolved,
                "size_mb": round(sz / (1024 * 1024), 2),
                "status": "pendente",  # pendente | importado | descartado
                "detected_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        known_paths.add(key_l)
        added += 1

    if added > 0:
        _write_queue(queue[:80])

    return {
        "folder_exists": True,
        "folder_path": folder_str,
        "added": added,
        "queue": _read_queue(),
        "last_scan_at": _LAST_SCAN_AT,
        "error": "",
    }


def get_watcher_status() -> dict[str, Any]:
    settings = load_settings()
    folder_str = str(settings.get("watch_folder_path") or r"E:\PS4\SHARE\Video Clips").strip()
    exists = False
    try:
        exists = bool(folder_str and Path(folder_str).exists() and Path(folder_str).is_dir())
    except OSError:
        exists = False
    queue = _read_queue()
    pending = [q for q in queue if q.get("status") == "pendente"]
    return {
        "enabled": bool(settings.get("watch_folder_enabled", False)),
        "folder_path": folder_str,
        "folder_exists": exists,
        "pending_count": len(pending),
        "queue": queue,
        "last_scan_at": _LAST_SCAN_AT,
        "last_error": _LAST_ERROR,
    }


def update_watcher_config(enabled: Optional[bool] = None, folder_path: Optional[str] = None) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if enabled is not None:
        updates["watch_folder_enabled"] = bool(enabled)
    if folder_path is not None:
        updates["watch_folder_path"] = str(folder_path).strip().strip('"')
    if updates:
        save_settings(updates)
    if updates.get("watch_folder_enabled"):
        scan_watch_folder_now(require_stability=False)
    return get_watcher_status()


def mark_watch_item_status(item_id: str, status: str) -> dict[str, Any]:
    queue = _read_queue()
    for it in queue:
        if it.get("id") == item_id:
            it["status"] = status
            break
    _write_queue(queue)
    return get_watcher_status()


def start_watcher_daemon() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    def _loop() -> None:
        while True:
            try:
                if load_settings().get("watch_folder_enabled", False):
                    scan_watch_folder_now(require_stability=True)
            except Exception:
                pass
            time.sleep(5.0)

    threading.Thread(target=_loop, daemon=True, name="PS4WatchFolderMonitor").start()
