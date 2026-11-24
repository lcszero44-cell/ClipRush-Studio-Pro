"""P2 Smart Agenda (1-3 posts/day), A/B Copy Testing & Monetization Goals Panel (backend/scheduler.py).
- Alternates games and platforms (YouTube Shorts vs TikTok Curto 25s vs TikTok Monetizavel 61-90s).
- Pre-publication checklist per planned slot.
- A/B Title/Hook variant generator + winner tracking.
- Configurable Monetization Goals (TikTok 10k followers / 100k 30d views; YouTube subs / hours / Shorts views).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import DATA_DIR
from backend.copy_engine import generate_multiple_options
from backend.errors import XRayError
from backend.storage import get_clip, list_clips

GROWTH_PLAN_FILE = DATA_DIR / "growth_plan.json"
_LOCK = threading.RLock()

DEFAULT_CHECKLIST: dict[str, bool] = {
    "safe_zone_ok": False,
    "audio_checked": False,
    "hashtags_verified": False,
    "copyright_reviewed": False,
    "pinned_cta_ready": False,
}

DEFAULT_GOALS: dict[str, Any] = {
    "tiktok_followers_current": 0,
    "tiktok_followers_target": 10000,
    "tiktok_views30d_current": 0,
    "tiktok_views30d_target": 100000,
    "youtube_subs_current": 0,
    "youtube_subs_target": 1000,
    "youtube_watch_hours_current": 0,
    "youtube_watch_hours_target": 4000,
    "youtube_shorts_views_current": 0,
    "youtube_shorts_views_target": 10000000,
}


def _read_plan() -> dict[str, Any]:
    with _LOCK:
        base: dict[str, Any] = {
            "posts_per_day": 2,
            "smart_slots": [],
            "ab_tests": [],
            "goals": dict(DEFAULT_GOALS),
        }
        if not GROWTH_PLAN_FILE.exists():
            return base
        try:
            saved = json.loads(GROWTH_PLAN_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                base.update(saved)
                g = dict(DEFAULT_GOALS)
                if isinstance(saved.get("goals"), dict):
                    g.update(saved["goals"])
                base["goals"] = g
        except Exception:
            pass
        return base


def _write_plan(data: dict[str, Any]) -> None:
    with _LOCK:
        raw = json.dumps(data, indent=2, ensure_ascii=False)
        if GROWTH_PLAN_FILE.exists():
            try:
                shutil.copy2(GROWTH_PLAN_FILE, GROWTH_PLAN_FILE.with_suffix(".json.bak"))
            except OSError:
                pass
        tmp = GROWTH_PLAN_FILE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(GROWTH_PLAN_FILE)


def get_growth_state() -> dict[str, Any]:
    plan = _read_plan()
    g = plan["goals"]

    def _pct(cur: float, tgt: float) -> float:
        return round(min(100.0, (max(0.0, float(cur)) / max(1.0, float(tgt))) * 100.0), 1)

    plan["goals_progress"] = {
        "tiktok_followers_pct": _pct(g["tiktok_followers_current"], g["tiktok_followers_target"]),
        "tiktok_views30d_pct": _pct(g["tiktok_views30d_current"], g["tiktok_views30d_target"]),
        "youtube_subs_pct": _pct(g["youtube_subs_current"], g["youtube_subs_target"]),
        "youtube_watch_hours_pct": _pct(g["youtube_watch_hours_current"], g["youtube_watch_hours_target"]),
        "youtube_shorts_views_pct": _pct(g["youtube_shorts_views_current"], g["youtube_shorts_views_target"]),
    }
    return plan


def generate_smart_agenda(posts_per_day: int = 2, days: int = 7) -> dict[str, Any]:
    """Generate a balanced 1-3 posts/day schedule alternating games and platforms
    (YouTube Shorts vs TikTok Curto 25s vs TikTok Monetizavel 61-90s) with pre-publish checklist."""
    ppd = max(1, min(3, int(posts_per_day)))
    n_days = max(1, min(14, int(days)))
    time_slots = {
        1: ["18:30"],
        2: ["12:30", "19:00"],
        3: ["11:30", "17:30", "21:00"],
    }[ppd]
    platform_cycle = [
        ("youtube_shorts", "YouTube Shorts (36–55s)"),
        ("tiktok_reels", "TikTok Curto (25s)"),
        ("tiktok_long", "TikTok Monetizavel (61–90s)"),
    ]

    clips = [c for c in list_clips() if c.get("file_exists")]
    # Group clips by game so consecutive posts alternate games whenever multiple games exist
    by_game: dict[str, list[dict[str, Any]]] = {}
    for c in clips:
        gname = str(c.get("detected_game") or c.get("profile_id") or "Gameplay").strip()
        by_game.setdefault(gname, []).append(c)
    game_keys = list(by_game.keys())

    now = datetime.now()
    slots: list[dict[str, Any]] = []
    slot_idx = 0
    for d in range(n_days):
        day_dt = now + timedelta(days=d + 1)
        day_str = day_dt.strftime("%Y-%m-%d")
        for hm in time_slots:
            plat_id, plat_label = platform_cycle[slot_idx % len(platform_cycle)]
            chosen_clip = None
            if game_keys:
                gkey = game_keys[slot_idx % len(game_keys)]
                pool = by_game.get(gkey) or []
                if pool:
                    # Prefer matching platform clip if available
                    chosen_clip = next((x for x in pool if x.get("platform_target") == plat_id), pool[0])
            slots.append(
                {
                    "id": f"slot_{uuid.uuid4().hex[:8]}",
                    "date": day_str,
                    "time": hm,
                    "platform": plat_id,
                    "platform_label": plat_label,
                    "clip_id": chosen_clip["id"] if chosen_clip else "",
                    "game_name": (chosen_clip.get("detected_game") if chosen_clip else "Definir Jogo") or "Gameplay",
                    "title": (chosen_clip.get("title") if chosen_clip else f"Slot planejado #{slot_idx + 1} — {plat_label}"),
                    "file_path": chosen_clip.get("file_path", "") if chosen_clip else "",
                    "status": "planejado",  # planejado | pronto | publicado
                    "checklist": dict(DEFAULT_CHECKLIST),
                }
            )
            slot_idx += 1

    plan = _read_plan()
    plan["posts_per_day"] = ppd
    plan["smart_slots"] = slots
    _write_plan(plan)
    return get_growth_state()


def update_slot_checklist(slot_id: str, checklist: dict[str, bool], status: Optional[str] = None) -> dict[str, Any]:
    plan = _read_plan()
    found = False
    for s in plan.get("smart_slots", []):
        if s.get("id") == slot_id:
            found = True
            cur = dict(DEFAULT_CHECKLIST)
            cur.update(s.get("checklist") or {})
            for k, v in checklist.items():
                if k in DEFAULT_CHECKLIST:
                    cur[k] = bool(v)
            s["checklist"] = cur
            if status in ("planejado", "pronto", "publicado"):
                s["status"] = status
            elif all(cur.values()) and s.get("status") == "planejado":
                s["status"] = "pronto"
            break
    if not found:
        raise XRayError("Slot da agenda inteligente nao encontrado.", category="SISTEMA", status_code=404)
    _write_plan(plan)
    return get_growth_state()


def create_ab_test(clip_id: str = "", title_hint: str = "", game_name: str = "", platform_target: str = "tiktok_reels") -> dict[str, Any]:
    clip = get_clip(clip_id) if clip_id else None
    eff_game = game_name or (clip.get("detected_game") if clip else "") or "Gameplay PS4"
    eff_prof = (clip.get("profile_id") if clip else "") or "generico"
    eff_cat = (clip.get("category") if clip else "") or "combate_intenso"
    eff_plat = platform_target or (clip.get("platform_target") if clip else "tiktok_reels")

    opts = generate_multiple_options(
        count=2,
        game_name=eff_game,
        profile_id=eff_prof,
        category=eff_cat,
        platform_target=eff_plat,
        exclude_titles=[title_hint] if title_hint else [],
        record_history=True,
    )
    test = {
        "id": f"ab_{uuid.uuid4().hex[:8]}",
        "clip_id": clip_id,
        "clip_title": (clip.get("title") if clip else title_hint) or f"Teste A/B — {eff_game}",
        "game_name": eff_game,
        "platform": eff_plat,
        "variant_a": {
            "hook_2s": opts[0]["hook_2s"],
            "title": opts[0]["title"],
            "hashtags": opts[0]["hashtags"],
        },
        "variant_b": {
            "hook_2s": opts[1]["hook_2s"],
            "title": opts[1]["title"],
            "hashtags": opts[1]["hashtags"],
        },
        "winner": "",  # "" | "A" | "B"
        "notes": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    plan = _read_plan()
    plan.setdefault("ab_tests", []).insert(0, test)
    plan["ab_tests"] = plan["ab_tests"][:50]
    _write_plan(plan)
    return {"test": test, "state": get_growth_state()}


def record_ab_winner(test_id: str, winner: str, notes: str = "") -> dict[str, Any]:
    w = winner.strip().upper()
    if w not in ("A", "B"):
        raise XRayError("Informe o vencedor como 'A' ou 'B'.", category="SISTEMA", status_code=400)
    plan = _read_plan()
    found = False
    for t in plan.get("ab_tests", []):
        if t.get("id") == test_id:
            found = True
            t["winner"] = w
            t["notes"] = notes.strip()[:240]
            t["decided_at"] = datetime.now(timezone.utc).isoformat()
            break
    if not found:
        raise XRayError("Teste A/B nao encontrado.", category="SISTEMA", status_code=404)
    _write_plan(plan)
    return get_growth_state()


def update_goals(updates: dict[str, Any]) -> dict[str, Any]:
    plan = _read_plan()
    goals = plan.setdefault("goals", dict(DEFAULT_GOALS))
    for k in DEFAULT_GOALS:
        if k in updates and updates[k] is not None:
            try:
                goals[k] = max(0, int(float(updates[k])))
            except (ValueError, TypeError):
                pass
    _write_plan(plan)
    return get_growth_state()
