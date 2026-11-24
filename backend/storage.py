"""Persistent JSON storage for projects, clip library, scheduled posts, and disk cleanup."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import DATA_DIR, TEMP_DIR, THUMBS_DIR, UPLOADS_DIR, get_ffmpeg_bins, get_output_dir

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXPORTED_HISTORY_SCHEMA_VERSION = 2

_LOCK = threading.RLock()
PROJECTS_FILE = DATA_DIR / "projects.json"
CLIPS_FILE = DATA_DIR / "clips_library.json"
SCHEDULE_FILE = DATA_DIR / "schedule.json"
EXPORTED_HISTORY_FILE = DATA_DIR / "exported_history.json"
GAMES_DB_FILE = DATA_DIR / "games_db.json"
OVERLAY_FILE = DATA_DIR / "overlay.json"


def _read_json(path: Path, default: Any) -> Any:
    with _LOCK:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default


def _write_json(path: Path, data: Any, create_backup: bool = True) -> None:
    with _LOCK:
        raw = json.dumps(data, indent=2, ensure_ascii=False)
        if create_backup and path.exists():
            try:
                bak = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, bak)
            except OSError:
                pass
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        for attempt in range(12):
            try:
                tmp.write_text(raw, encoding="utf-8")
                tmp.replace(path)
                return
            except (PermissionError, OSError):
                time.sleep(0.025 * (attempt + 1))
        path.write_text(raw, encoding="utf-8")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


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
                p = Path(target["file_path"]).resolve()
                allowed_out = get_output_dir().resolve()
                if p.exists() and p.is_file() and p.is_relative_to(allowed_out):
                    p.unlink()
            except OSError:
                pass
        _write_json(CLIPS_FILE, remaining)
        return True


# ---- Exported Clips History v2 (Content-Fingerprint Anti-Repetition: Size + Duration + Start/Mid/End SHA-256) ----

def _quick_probe_duration(video_path: Path) -> float:
    _, ffprobe = get_ffmpeg_bins()
    if not ffprobe or not video_path.exists():
        return 0.0
    try:
        res = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout.strip():
            return round(float(res.stdout.strip()), 2)
    except Exception:
        pass
    return 0.0


def _compute_legacy_v1_fingerprint(video_path: str) -> str:
    """Legacy v1 fingerprint (included filename) kept only for transparent read migration."""
    p = Path(video_path)
    h = hashlib.sha256()
    h.update(p.name.lower().encode("utf-8", errors="ignore"))
    if p.exists() and p.is_file():
        sz = p.stat().st_size
        h.update(str(sz).encode("ascii"))
        try:
            with p.open("rb") as fh:
                h.update(fh.read(65536))
                if sz > 65536:
                    fh.seek(max(0, sz - 65536))
                    h.update(fh.read(65536))
        except OSError:
            pass
    return h.hexdigest()[:24]


def compute_video_fingerprint_details(video_path: str, duration: Optional[float] = None) -> dict[str, Any]:
    """Path-independent content fingerprint:
    exact file size in bytes + duration (0.1s) + partial SHA-256 (start 64KB + middle 64KB + end 64KB).
    Recognizes renamed or moved video files instantaneously!"""
    p = Path(video_path)
    file_size = 0
    partial_h = hashlib.sha256()
    chunk_sz = 65536

    if p.exists() and p.is_file():
        try:
            file_size = int(p.stat().st_size)
            with p.open("rb") as fh:
                # Start 64KB
                fh.seek(0)
                partial_h.update(fh.read(chunk_sz))
                # Middle 64KB
                if file_size > chunk_sz * 2:
                    mid_off = max(0, (file_size // 2) - (chunk_sz // 2))
                    fh.seek(mid_off)
                    partial_h.update(fh.read(chunk_sz))
                # End 64KB
                if file_size > chunk_sz:
                    end_off = max(0, file_size - chunk_sz)
                    fh.seek(end_off)
                    partial_h.update(fh.read(chunk_sz))
        except OSError:
            pass
    else:
        partial_h.update(str(p).encode("utf-8", errors="ignore"))

    dur_val = round(float(duration), 2) if duration is not None else _quick_probe_duration(p)
    partial_hex = partial_h.hexdigest()
    composite = hashlib.sha256(f"{file_size}|{dur_val:.1f}|{partial_hex}".encode("ascii")).hexdigest()[:32]
    return {
        "fingerprint": composite,
        "file_size": file_size,
        "duration": dur_val,
        "partial_sha256": partial_hex,
    }


def compute_video_fingerprint(video_path: str, duration: Optional[float] = None) -> str:
    return compute_video_fingerprint_details(video_path, duration=duration)["fingerprint"]


def _load_exported_history_v2() -> dict[str, Any]:
    """Load exported_history.json, transparently migrating legacy v1 dict to schema_version: 2."""
    with _LOCK:
        raw = _read_json(EXPORTED_HISTORY_FILE, None)
        if not isinstance(raw, dict):
            return {
                "schema_version": EXPORTED_HISTORY_SCHEMA_VERSION,
                "videos": {},
                "legacy_v1": {},
            }
        if raw.get("schema_version") == EXPORTED_HISTORY_SCHEMA_VERSION and isinstance(raw.get("videos"), dict):
            raw.setdefault("legacy_v1", {})
            return raw

        # Migrate v1 -> v2 preserving every single historical interval
        migrated = {
            "schema_version": EXPORTED_HISTORY_SCHEMA_VERSION,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
            "videos": {},
            "legacy_v1": {k: v for k, v in raw.items() if isinstance(v, dict) and isinstance(v.get("exported"), list)},
        }
        _write_json(EXPORTED_HISTORY_FILE, migrated, create_backup=True)
        return migrated


def get_video_exported_intervals(video_path: str, duration: Optional[float] = None) -> list[dict[str, Any]]:
    details = compute_video_fingerprint_details(video_path, duration=duration)
    fp = details["fingerprint"]
    hist = _load_exported_history_v2()
    videos = hist.get("videos") or {}
    entry = videos.get(fp)
    if entry and isinstance(entry.get("exported"), list):
        return list(entry["exported"])

    # Fallback check against migrated legacy v1 entries (by exact filename+size hash)
    leg_fp = _compute_legacy_v1_fingerprint(video_path)
    leg_entry = (hist.get("legacy_v1") or {}).get(leg_fp)
    if leg_entry and isinstance(leg_entry.get("exported"), list):
        return list(leg_entry["exported"])
    return []


def is_interval_exported(
    video_path: str,
    start: float,
    end: float,
    intervals: Optional[list[dict[str, Any]]] = None,
    duration: Optional[float] = None,
) -> bool:
    """Return True if [start, end] significantly overlaps any previously exported interval for this video."""
    existing = intervals if intervals is not None else get_video_exported_intervals(video_path, duration=duration)
    dur = max(1.0, float(end) - float(start))
    for item in existing:
        es = float(item.get("start", 0.0))
        ee = float(item.get("end", 0.0))
        overlap = max(0.0, min(float(end), ee) - max(float(start), es))
        if overlap >= 8.0 or (overlap / dur) >= 0.42:
            return True
    return False


def record_exported_interval(
    video_path: str,
    start: float,
    end: float,
    clip_id: str = "",
    duration: Optional[float] = None,
) -> None:
    with _LOCK:
        details = compute_video_fingerprint_details(video_path, duration=duration)
        fp = details["fingerprint"]
        hist = _load_exported_history_v2()
        videos = hist.setdefault("videos", {})
        fname = Path(video_path).name
        entry = videos.get(fp) or {
            "fingerprint": fp,
            "file_size": details["file_size"],
            "duration": details["duration"],
            "partial_sha256": details["partial_sha256"],
            "known_names": [],
            "last_path": str(video_path),
            "exported": [],
        }
        entry["file_size"] = details["file_size"]
        entry["duration"] = details["duration"]
        entry["partial_sha256"] = details["partial_sha256"]
        entry["last_path"] = str(video_path)
        if fname and fname not in entry.setdefault("known_names", []):
            entry["known_names"].append(fname)
        entry["exported"].append(
            {
                "start": round(float(start), 2),
                "end": round(float(end), 2),
                "clip_id": clip_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        videos[fp] = entry
        _write_json(EXPORTED_HISTORY_FILE, hist, create_backup=True)


def clear_video_export_history(video_path: str, duration: Optional[float] = None) -> None:
    with _LOCK:
        details = compute_video_fingerprint_details(video_path, duration=duration)
        fp = details["fingerprint"]
        leg_fp = _compute_legacy_v1_fingerprint(video_path)
        hist = _load_exported_history_v2()
        changed = False
        if fp in hist.get("videos", {}):
            del hist["videos"][fp]
            changed = True
        if leg_fp in hist.get("legacy_v1", {}):
            del hist["legacy_v1"][leg_fp]
            changed = True
        if changed:
            _write_json(EXPORTED_HISTORY_FILE, hist, create_backup=True)


def annotate_moments_export_status(
    video_path: str,
    moments: list[dict[str, Any]],
    duration: Optional[float] = None,
) -> list[dict[str, Any]]:
    intervals = get_video_exported_intervals(video_path, duration=duration)
    for m in moments:
        m["already_exported"] = is_interval_exported(
            video_path, m.get("start", 0), m.get("end", 0), intervals=intervals, duration=duration
        )
    return moments


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
        "low_disk_warning": disk_free_gb < 10.0,
    }


def get_project_storage_breakdown() -> dict[str, Any]:
    """P2 Per-Project Storage Manager: calculates GB/MB occupied by each session/project
    (copied original upload + rendered MP4s + covers) and warns if disk free < 10 GB."""
    projects = list_projects()
    clips = list_clips()
    uploads_root = UPLOADS_DIR.resolve()
    out_root = get_output_dir().resolve()

    rows: list[dict[str, Any]] = []
    for proj in projects:
        pid = proj.get("id", "")
        vp = Path(proj.get("video_path") or "")
        upload_bytes = 0
        has_copied_upload = False
        try:
            if vp.exists() and vp.is_file() and vp.resolve().is_relative_to(uploads_root):
                upload_bytes = vp.stat().st_size
                has_copied_upload = True
        except OSError:
            pass

        proj_clips = [c for c in clips if c.get("project_id") == pid]
        render_bytes = 0
        render_count = 0
        for c in proj_clips:
            for k in ("file_path", "cover_path"):
                fp = Path(c.get(k) or "")
                try:
                    if fp.exists() and fp.is_file() and fp.resolve().is_relative_to(out_root):
                        render_bytes += fp.stat().st_size
                        if k == "file_path":
                            render_count += 1
                except OSError:
                    pass

        total_b = upload_bytes + render_bytes
        rows.append(
            {
                "project_id": pid,
                "name": proj.get("name") or "Projeto",
                "created_at": proj.get("created_at", ""),
                "has_copied_upload": has_copied_upload,
                "upload_mb": round(upload_bytes / (1024 * 1024), 2),
                "render_count": render_count,
                "render_mb": round(render_bytes / (1024 * 1024), 2),
                "total_gb": round(total_b / (1024 ** 3), 3),
                "total_mb": round(total_b / (1024 * 1024), 2),
            }
        )

    rows.sort(key=lambda r: r["total_mb"], reverse=True)
    disk_free_gb = 0.0
    try:
        disk_free_gb = round(shutil.disk_usage(str(DATA_DIR)).free / (1024 ** 3), 2)
    except OSError:
        pass
    return {
        "projects": rows,
        "disk_free_gb": disk_free_gb,
        "low_disk_warning": disk_free_gb < 10.0,
    }


def delete_project_media(project_id: str, target: str = "renders") -> dict[str, Any]:
    """Safely delete copied original upload OR old renders for a project (strict is_relative_to guard)."""
    proj = get_project(project_id)
    if not proj:
        return {"ok": False, "freed_mb": 0.0, "removed_files": 0}
    freed = 0
    removed = 0
    uploads_root = UPLOADS_DIR.resolve()
    out_root = get_output_dir().resolve()

    if target in ("uploads", "all"):
        vp = Path(proj.get("video_path") or "")
        try:
            if vp.exists() and vp.is_file() and vp.resolve().is_relative_to(uploads_root):
                freed += vp.stat().st_size
                vp.unlink()
                removed += 1
        except OSError:
            pass

    if target in ("renders", "all"):
        for c in list_clips():
            if c.get("project_id") == project_id:
                for k in ("file_path", "cover_path"):
                    fp = Path(c.get(k) or "")
                    try:
                        if fp.exists() and fp.is_file() and fp.resolve().is_relative_to(out_root):
                            freed += fp.stat().st_size
                            fp.unlink()
                            removed += 1
                    except OSError:
                        pass
                delete_clip(c["id"], delete_file=False)

    return {
        "ok": True,
        "project_id": project_id,
        "target": target,
        "freed_mb": round(freed / (1024 * 1024), 2),
        "removed_files": removed,
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


# ---- Editable Game Sheets Database (data/games_db.json) ----

DEFAULT_GAMES_DB: list[dict[str, Any]] = [
    {
        "id": "resident_evil_4",
        "official_name": "Resident Evil 4 Remake",
        "aliases": ["re4", "re4 remake", "resident evil 4", "biohazard 4", "resident evil"],
        "genre": "terror",
        "profile_id": "resident_evil",
        "characters": ["Leon S. Kennedy", "Ashley Graham", "Ada Wong", "Luis Serra", "Dr. Salvador", "Krauser", "Salazar", "El Gigante"],
        "locations": ["Vila", "Castelo", "Ilha", "Igreja", "Lago"],
        "weapons_items": ["Red9", "Riot Gun", "Faca de Combate", "Broken Butterfly", "Serra Eletrica", "Granada de Luz", "Spray de Primeiros Socorros"],
        "slangs_br": ["parry perfeito", "tomei serrada", "no sufoco", "sem municao", "chute giratorio", "deu tela vermelha", "quase fui de base"],
        "hashtags": ["#ResidentEvil4", "#RE4Remake", "#LeonKennedy", "#SurvivalHorror", "#TerrorPS4", "#RE4Brasil"],
        "is_custom": False,
    },
    {
        "id": "resident_evil_village",
        "official_name": "Resident Evil Village",
        "aliases": ["re8", "re village", "resident evil 8", "village"],
        "genre": "terror",
        "profile_id": "resident_evil",
        "characters": ["Ethan Winters", "Lady Dimitrescu", "Chris Redfield", "Heisenberg", "Mae Miranda"],
        "locations": ["Castelo Dimitrescu", "Casa Beneviento", "Fabrica", "Vila Nevada"],
        "weapons_items": ["Wolfsbane", "SYG-12", "LEMI", "Escopeta W870", "Fluido Quimico"],
        "slangs_br": ["corri muito", "susto sinistro", "quase infartei", "no limite da vida", "sem cura"],
        "hashtags": ["#ResidentEvilVillage", "#RE8", "#LadyDimitrescu", "#HorrorGaming", "#TerrorBR"],
        "is_custom": False,
    },
    {
        "id": "god_of_war",
        "official_name": "God of War",
        "aliases": ["gow", "god of war 2018", "god of war ragnarok", "kratos", "ragnarok"],
        "genre": "acao",
        "profile_id": "god_of_war",
        "characters": ["Kratos", "Atreus", "Baldur", "Freya", "Thor", "Sigrun", "Valquiria", "Mimir"],
        "locations": ["Midgard", "Alfheim", "Helheim", "Muspelheim", "Asgard", "Lago dos Nove"],
        "weapons_items": ["Machado Leviata", "Laminas do Caos", "Lanca Draupnir", "Escudo do Guardiao", "Furia Espartana"],
        "slangs_br": ["R3 brutal", "parry milimetrico", "amassei o chefe", "combo infinito", "furia ligada", "no modo Deus da Guerra"],
        "hashtags": ["#GodOfWar", "#Kratos", "#GodOfWarRagnarok", "#BossFight", "#PlayStationBR", "#R3Execution"],
        "is_custom": False,
    },
    {
        "id": "rdr2",
        "official_name": "Red Dead Redemption 2",
        "aliases": ["rdr2", "red dead 2", "red dead", "arthur morgan"],
        "genre": "mundo_aberto",
        "profile_id": "rdr2",
        "characters": ["Arthur Morgan", "John Marston", "Dutch van der Linde", "Micah Bell", "Sadie Adler"],
        "locations": ["Valentine", "Saint Denis", "Strawberry", "Rhodes", "Blackwater", "Acampamento"],
        "weapons_items": ["Revolver Schofield", "Carabina Lancaster", "Dead Eye", "Laco", "Dinamite", "Escopeta de Cano Duplo"],
        "slangs_br": ["Dead Eye insano", "tiro no chapeu", "duelo raiz", "cai do cavalo", "fora da lei", "honra baixa"],
        "hashtags": ["#RedDeadRedemption2", "#RDR2", "#ArthurMorgan", "#DeadEye", "#VelhoOeste", "#RockstarGames"],
        "is_custom": False,
    },
    {
        "id": "spider_man",
        "official_name": "Marvel's Spider-Man",
        "aliases": ["spider-man", "spiderman", "homem aranha", "miles morales", "spider man ps4"],
        "genre": "acao",
        "profile_id": "spider_man",
        "characters": ["Peter Parker", "Miles Morales", "Dr. Octopus", "Senhor Negativo", "Venom", "Taskmaster", "MJ"],
        "locations": ["Manhattan", "Torre dos Vingadores", "Times Square", "Central Park", "Harlem"],
        "weapons_items": ["Lanca-Teias", "Teia Eletrica", "Bomba de Teia", "Drone Aranha", "Traje Avancado"],
        "slangs_br": ["combo aereo limpo", "esquiva perfeita", "finalizacao de cinema", "sem tomar dano", "voando baixo"],
        "hashtags": ["#SpiderManPS4", "#HomemAranha", "#MarvelsSpiderMan", "#Combos", "#PS4Gameplay"],
        "is_custom": False,
    },
    {
        "id": "sniper_elite_5",
        "official_name": "Sniper Elite 5",
        "aliases": ["sniper elite", "sniper elite 4", "sniper elite 5", "se5", "se4"],
        "genre": "tiro",
        "profile_id": "sniper_elite",
        "characters": ["Karl Fairburne", "Sniper Inimigo", "Oficial", "Jager", "Invasor"],
        "locations": ["Muralha do Atlantico", "Franca Ocupada", "Bunker", "Torre de Vigia", "Castelo"],
        "weapons_items": ["Kar98k", "M1903 Springfield", "Welrod Silenciada", "Mina Teller", "Camera Raio-X", "Municao Perfurante"],
        "slangs_br": ["raio-X absurdo", "headshot a 300m", "bala cirurgica", "tiro no escuro", "stealth puro", "um tiro um abate"],
        "hashtags": ["#SniperElite", "#SniperElite5", "#Headshot", "#RaioX", "#TiroTatico", "#SniperBR"],
        "is_custom": False,
    },
    {
        "id": "the_last_of_us_2",
        "official_name": "The Last of Us Part II",
        "aliases": ["tlou", "tlou2", "the last of us", "last of us"],
        "genre": "terror",
        "profile_id": "resident_evil",
        "characters": ["Ellie", "Joel", "Abby", "Estalador", "Baiacu", "Rei dos Ratos"],
        "locations": ["Seattle", "Jackson", "Hospital", "Metro Abandonado", "Santa Barbara"],
        "weapons_items": ["Arco e Flecha", "Coquetel Molotov", "Canivete", "Rifle de Ferrolho", "Mina de Proximidade", "Tijolo"],
        "slangs_br": ["stealth tenso", "tijolada na cara", "estalador nas costas", "sem bala nenhuma", "esquiva no talo"],
        "hashtags": ["#TheLastOfUs", "#TLOU2", "#Ellie", "#PlayStation", "#Sobrevivencia"],
        "is_custom": False,
    },
    {
        "id": "gta_v",
        "official_name": "GTA V",
        "aliases": ["gta", "gta 5", "gta v", "grand theft auto", "gta online"],
        "genre": "mundo_aberto",
        "profile_id": "rdr2",
        "characters": ["Trevor", "Franklin", "Michael", "Lester", "Policia 5 Estrelas"],
        "locations": ["Los Santos", "Monte Chiliad", "Aeroporto", "Base Militar", "Cassino"],
        "weapons_items": ["Oppressor Mk II", "RPG", "Minigun", "Sticky Bomb", "Sniper Pesada"],
        "slangs_br": ["5 estrelas de fuga", "caos em Los Santos", "manobra impossivel", "explodiu geral", "bug lendario"],
        "hashtags": ["#GTAV", "#GTA5", "#GTAOnline", "#LosSantos", "#GTABrasil"],
        "is_custom": False,
    },
    {
        "id": "elden_ring",
        "official_name": "Elden Ring",
        "aliases": ["elden ring", "souls", "dark souls", "bloodborne", "sekiro"],
        "genre": "rpg",
        "profile_id": "god_of_war",
        "characters": ["Maculado", "Malenia", "Radahn", "Margit", "Godfrey", "Messmer"],
        "locations": ["Limgrave", "Caelid", "Leyndell", "Arvore Sacra", "Terras Intermedias"],
        "weapons_items": ["Rios de Sangue", "Lamina Blasfema", "Espada Grande", "Mimic Tear", "Frasco Carmesim"],
        "slangs_br": ["no hit", "rolada perfeita", "tomei hit kill", "morri com 1% de vida", "parry de mestre", "venci no sufoco"],
        "hashtags": ["#EldenRing", "#Soulslike", "#Malenia", "#BossFight", "#FromSoftware"],
        "is_custom": False,
    },
    {
        "id": "ghost_of_tsushima",
        "official_name": "Ghost of Tsushima",
        "aliases": ["ghost of tsushima", "tsushima", "jin sakai"],
        "genre": "acao",
        "profile_id": "god_of_war",
        "characters": ["Jin Sakai", "Khotun Khan", "Lorde Shimura", "Yuna"],
        "locations": ["Ilha de Tsushima", "Castelo Kaneda", "Campos de Flores", "Templo Dourado"],
        "weapons_items": ["Katana Sakai", "Kunai", "Bomba de Fumaca", "Postura do Fantasma", "Arco Longo"],
        "slangs_br": ["duelo samurai", "parry perfeito", "modo fantasma", "corte limpo", "cinema puro"],
        "hashtags": ["#GhostOfTsushima", "#JinSakai", "#Samurai", "#PS4Exclusivo", "#PlayStation"],
        "is_custom": False,
    },
    {
        "id": "cod_warzone",
        "official_name": "Call of Duty Warzone",
        "aliases": ["cod", "warzone", "call of duty", "mw3", "bo6"],
        "genre": "tiro",
        "profile_id": "sniper_elite",
        "characters": ["Squad Inimigo", "Sniper Camper", "Operador"],
        "locations": ["Gulag", "Rebirth Island", "Urzikstan", "Safe Final"],
        "weapons_items": ["Kar98k", "SMG Meta", "C4", "UAV", "Mascara de Gas", "Granada de Concussao"],
        "slangs_br": ["squad wipe", "clutch 1v4", "amassei o lobby", "tiro de sniper limpo", "vitoria no gas"],
        "hashtags": ["#Warzone", "#CallOfDuty", "#WarzoneBR", "#Clutch", "#FPSBrasil"],
        "is_custom": False,
    },
    {
        "id": "mortal_kombat",
        "official_name": "Mortal Kombat 11",
        "aliases": ["mk11", "mk1", "mortal kombat"],
        "genre": "luta",
        "profile_id": "god_of_war",
        "characters": ["Scorpion", "Sub-Zero", "Liu Kang", "Noob Saibot", "Johnny Cage", "Raiden"],
        "locations": ["Arena", "Kripta", "Torre do Tempo"],
        "weapons_items": ["Fatal Blow", "Fatality", "Brutality", "Krushing Blow", "Kombo 50%"],
        "slangs_br": ["comeback absurdo", "Brutality seco", "read perfeito", "sem deixar levantar", "flawless victory"],
        "hashtags": ["#MortalKombat", "#MK11", "#Fatality", "#FightingGames", "#JogosDeLuta"],
        "is_custom": False,
    },
]


def _ensure_games_db() -> list[dict[str, Any]]:
    with _LOCK:
        existing = _read_json(GAMES_DB_FILE, None)
        if isinstance(existing, list) and existing:
            return existing
        _write_json(GAMES_DB_FILE, DEFAULT_GAMES_DB, create_backup=False)
        return list(DEFAULT_GAMES_DB)


def list_games() -> list[dict[str, Any]]:
    return _ensure_games_db()


def find_game_sheet(query: str) -> Optional[dict[str, Any]]:
    """Find a Game Sheet by exact id, official_name, or alias substring (case-insensitive)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    games = _ensure_games_db()
    # 1. Exact id or official name
    for g in games:
        if g.get("id", "").lower() == q or g.get("official_name", "").lower() == q:
            return g
    # 2. Exact alias
    for g in games:
        if any(str(a).lower() == q for a in (g.get("aliases") or [])):
            return g
    # 3. Substring match on official name or aliases (longest match first)
    best: Optional[tuple[int, dict[str, Any]]] = None
    for g in games:
        candidates = [g.get("official_name", "").lower()] + [str(a).lower() for a in (g.get("aliases") or [])]
        for c in candidates:
            if c and (c in q or q in c):
                score = len(c)
                if best is None or score > best[0]:
                    best = (score, g)
    return best[1] if best else None


def upsert_game_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    """Create or update an editable Game Sheet in data/games_db.json with atomic write + .bak."""
    with _LOCK:
        games = _ensure_games_db()
        name = str(sheet.get("official_name") or "").strip()
        if not name:
            raise ValueError("official_name obrigatorio para salvar Ficha do Jogo.")
        raw_id = str(sheet.get("id") or "").strip().lower()
        if not raw_id:
            raw_id = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")[:40] or f"game_{uuid.uuid4().hex[:6]}"

        def _clean_list(val: Any) -> list[str]:
            if isinstance(val, str):
                items = [x.strip() for x in val.replace("\n", ",").split(",")]
            elif isinstance(val, list):
                items = [str(x).strip() for x in val]
            else:
                items = []
            out: list[str] = []
            seen: set[str] = set()
            for it in items:
                if it and it.lower() not in seen:
                    seen.add(it.lower())
                    out.append(it)
            return out

        raw_tags = _clean_list(sheet.get("hashtags"))
        clean_tags = [t if t.startswith("#") else f"#{t.replace(' ', '')}" for t in raw_tags]

        record = {
            "id": raw_id,
            "official_name": name,
            "aliases": _clean_list(sheet.get("aliases")) or [name.lower()],
            "genre": str(sheet.get("genre") or "acao").strip().lower(),
            "profile_id": str(sheet.get("profile_id") or "generico").strip(),
            "characters": _clean_list(sheet.get("characters")),
            "locations": _clean_list(sheet.get("locations")),
            "weapons_items": _clean_list(sheet.get("weapons_items")),
            "slangs_br": _clean_list(sheet.get("slangs_br")),
            "hashtags": clean_tags,
            "is_custom": bool(sheet.get("is_custom", True)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        replaced = False
        for i, g in enumerate(games):
            if g.get("id") == raw_id or g.get("official_name", "").lower() == name.lower():
                record["id"] = g.get("id") or raw_id
                games[i] = record
                replaced = True
                break
        if not replaced:
            games.insert(0, record)

        _write_json(GAMES_DB_FILE, games, create_backup=True)
        return record


# ---- Visual 9:16 Title Overlay Config (data/overlay.json) ----

DEFAULT_OVERLAY_CONFIG: dict[str, Any] = {
    "text": "OLHA ESSE FINAL ABSURDO...",
    "x": 540,              # 0..1080 (center = 540, Safe Zone X: 120..960, max width 840)
    "y": 250,              # 0..1920 (Safe Zone Y: 150..400)
    "font_family": "Arial Black",
    "font_size": 52,       # 32..84 px in PlayResX 1080x1920
    "color": "#FFD700",
    "outline_color": "#000000",
    "outline_width": 4,
    "animation": "fade",   # none | fade | pop | slide_down
    "duration_sec": 2.3,   # 1.5..4.5s
    "start_sec": 0.15,
}


def load_overlay_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_OVERLAY_CONFIG)
    saved = _read_json(OVERLAY_FILE, None)
    if isinstance(saved, dict):
        cfg.update(saved)
    cfg["x"] = max(60, min(1020, int(round(float(cfg.get("x", 540))))))
    cfg["y"] = max(60, min(1860, int(round(float(cfg.get("y", 250))))))
    cfg["font_size"] = max(28, min(90, int(round(float(cfg.get("font_size", 52))))))
    cfg["outline_width"] = max(0, min(10, int(round(float(cfg.get("outline_width", 4))))))
    cfg["duration_sec"] = round(max(1.0, min(5.0, float(cfg.get("duration_sec", 2.3)))), 2)
    cfg["start_sec"] = round(max(0.0, min(2.0, float(cfg.get("start_sec", 0.15)))), 2)
    if cfg.get("animation") not in ("none", "fade", "pop", "slide_down"):
        cfg["animation"] = "fade"
    if not OVERLAY_FILE.exists():
        _write_json(OVERLAY_FILE, cfg, create_backup=False)
    return cfg


def save_overlay_config(updates: dict[str, Any]) -> dict[str, Any]:
    current = load_overlay_config()
    for k in DEFAULT_OVERLAY_CONFIG:
        if k in updates and updates[k] is not None:
            current[k] = updates[k]
    _write_json(OVERLAY_FILE, current, create_backup=True)
    return load_overlay_config()
