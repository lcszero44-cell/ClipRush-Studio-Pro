"""P2 Performance Analytics & Automatic Ranking Learning Engine (backend/analytics.py).
- Manual metric logging, official TikTok Studio CSV import, and YouTube Analytics / Data API sync.
- Crosses results by Game, Event Category, Duration Bucket (25s vs 36-55s vs 61-90s), and Hook Style.
- Automatically adjusts ranking weights (data/ranking_weights.json) to prioritize high-retention formats.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import DATA_DIR
from backend.errors import XRayError
from backend.storage import list_clips

METRICS_FILE = DATA_DIR / "performance_metrics.json"
WEIGHTS_FILE = DATA_DIR / "ranking_weights.json"
_LOCK = threading.RLock()


def _atomic_write(path: Path, data: Any) -> None:
    with _LOCK:
        raw = json.dumps(data, indent=2, ensure_ascii=False)
        if path.exists():
            try:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(path)


def _read_metrics() -> list[dict[str, Any]]:
    with _LOCK:
        if not METRICS_FILE.exists():
            return []
        try:
            data = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []


def classify_duration_bucket(duration_sec: float) -> str:
    d = float(duration_sec or 0.0)
    if d >= 60.0:
        return "61-90s (Monetizavel)"
    if d >= 35.0:
        return "36-55s (Shorts)"
    return "20-32s (Curto 25s)"


def _performance_score(rec: dict[str, Any]) -> float:
    views = max(0, int(rec.get("views", 0)))
    avg_watch = max(0.0, min(200.0, float(rec.get("avg_watch_pct", 0.0))))
    ret_3s = max(0.0, min(100.0, float(rec.get("retention_3s_pct", 0.0))))
    shares = max(0, int(rec.get("shares", 0)))
    share_rate = min(100.0, (shares / max(1, views)) * 1200.0)
    view_bonus = min(25.0, math.log10(views + 1.0) * 5.0)
    return round(avg_watch * 0.42 + ret_3s * 0.33 + share_rate * 0.15 + view_bonus, 2)


def _aggregate_dimension(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        val = str(r.get(key) or "Nao informado").strip() or "Nao informado"
        buckets.setdefault(val, []).append(r)

    rows: list[dict[str, Any]] = []
    for label, items in buckets.items():
        n = len(items)
        tot_views = sum(int(x.get("views", 0)) for x in items)
        avg_watch = round(sum(float(x.get("avg_watch_pct", 0.0)) for x in items) / n, 1)
        avg_ret3 = round(sum(float(x.get("retention_3s_pct", 0.0)) for x in items) / n, 1)
        tot_shares = sum(int(x.get("shares", 0)) for x in items)
        avg_score = round(sum(_performance_score(x) for x in items) / n, 1)
        rows.append(
            {
                "label": label,
                "count": n,
                "total_views": tot_views,
                "avg_views": round(tot_views / n, 1),
                "avg_watch_pct": avg_watch,
                "avg_retention_3s_pct": avg_ret3,
                "total_shares": tot_shares,
                "perf_score": avg_score,
            }
        )
    rows.sort(key=lambda x: x["perf_score"], reverse=True)
    return rows


def recompute_learned_weights(records: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Compute bounded ranking multipliers (0.82..1.28) from real channel performance."""
    recs = records if records is not None else _read_metrics()
    default = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(recs),
        "category_weights": {},
        "duration_weights": {},
        "game_weights": {},
        "hook_style_weights": {},
        "insights_pt": [
            "Registre metricas manuais ou importe o CSV do TikTok Studio para a IA aprender quais jogos, duracoes e ganchos mais retem sua audiencia."
        ],
    }
    if not recs:
        _atomic_write(WEIGHTS_FILE, default)
        return default

    global_avg = max(10.0, sum(_performance_score(r) for r in recs) / len(recs))

    def _build_map(rows: list[dict[str, Any]]) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in rows:
            ratio = row["perf_score"] / global_avg
            # Shrink gently toward 1.0 when sample size is small so outliers don't overfit
            confidence = min(1.0, row["count"] / 3.0)
            mult = 1.0 + (ratio - 1.0) * 0.45 * confidence
            out[row["label"]] = round(max(0.82, min(1.28, mult)), 3)
        return out

    by_game = _aggregate_dimension(recs, "game_name")
    by_cat = _aggregate_dimension(recs, "category")
    by_dur = _aggregate_dimension(recs, "duration_bucket")
    by_hook = _aggregate_dimension(recs, "hook_style")

    insights: list[str] = []
    if by_game:
        insights.append(f"Melhor jogo em retencao: {by_game[0]['label']} ({by_game[0]['avg_watch_pct']}% assistido, {by_game[0]['avg_retention_3s_pct']}% retencao 3s).")
    if by_dur:
        insights.append(f"Formato de duracao campeao: {by_dur[0]['label']} (score {by_dur[0]['perf_score']}).")
    if by_cat:
        insights.append(f"Tipo de evento que mais engaja: {by_cat[0]['label']} ({by_cat[0]['total_shares']} compartilhamentos).")
    if by_hook:
        insights.append(f"Estilo de gancho mais eficiente: {by_hook[0]['label']}.")

    weights = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(recs),
        "category_weights": _build_map(by_cat),
        "duration_weights": _build_map(by_dur),
        "game_weights": _build_map(by_game),
        "hook_style_weights": _build_map(by_hook),
        "insights_pt": insights,
    }
    _atomic_write(WEIGHTS_FILE, weights)
    return weights


def load_learned_weights() -> dict[str, Any]:
    if WEIGHTS_FILE.exists():
        try:
            data = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"sample_size": 0, "category_weights": {}, "duration_weights": {}, "game_weights": {}, "hook_style_weights": {}}


def get_learned_ranking_boost(category: str, duration_sec: float, game_name: str = "") -> int:
    """Return integer score boost (-10..+12) learned from channel performance for analyzer.py."""
    w = load_learned_weights()
    if int(w.get("sample_size", 0)) < 1:
        return 0
    mult = 1.0
    cat_m = float((w.get("category_weights") or {}).get(category, 1.0))
    dur_m = float((w.get("duration_weights") or {}).get(classify_duration_bucket(duration_sec), 1.0))
    game_m = float((w.get("game_weights") or {}).get(game_name, 1.0)) if game_name else 1.0
    mult = (cat_m * 0.45) + (dur_m * 0.35) + (game_m * 0.20)
    return max(-10, min(12, int(round((mult - 1.0) * 42.0))))


def add_performance_record(payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        recs = _read_metrics()
        dur_sec = float(payload.get("duration_sec") or 28.0)
        rec = {
            "id": f"perf_{uuid.uuid4().hex[:8]}",
            "clip_id": str(payload.get("clip_id") or ""),
            "title": str(payload.get("title") or "Clipe sem titulo").strip()[:120],
            "game_name": str(payload.get("game_name") or "Universal").strip()[:60],
            "category": str(payload.get("category") or "combate_intenso").strip(),
            "platform": str(payload.get("platform") or "tiktok_reels").strip(),
            "duration_sec": round(dur_sec, 1),
            "duration_bucket": str(payload.get("duration_bucket") or classify_duration_bucket(dur_sec)),
            "hook_style": str(payload.get("hook_style") or "Gancho de Pico 1.8s").strip()[:60],
            "views": max(0, int(payload.get("views") or 0)),
            "avg_watch_pct": round(max(0.0, min(200.0, float(payload.get("avg_watch_pct") or 0.0))), 1),
            "retention_3s_pct": round(max(0.0, min(100.0, float(payload.get("retention_3s_pct") or 0.0))), 1),
            "shares": max(0, int(payload.get("shares") or 0)),
            "source": str(payload.get("source") or "manual"),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        rec["perf_score"] = _performance_score(rec)
        recs.insert(0, rec)
        _atomic_write(METRICS_FILE, recs[:500])
        weights = recompute_learned_weights(recs)
        return {"record": rec, "weights": weights}


def delete_performance_record(record_id: str) -> dict[str, Any]:
    with _LOCK:
        recs = [r for r in _read_metrics() if r.get("id") != record_id]
        _atomic_write(METRICS_FILE, recs)
        weights = recompute_learned_weights(recs)
        return {"ok": True, "weights": weights}


def get_analytics_dashboard() -> dict[str, Any]:
    recs = _read_metrics()
    weights = recompute_learned_weights(recs) if recs else load_learned_weights()
    total_views = sum(int(r.get("views", 0)) for r in recs)
    total_shares = sum(int(r.get("shares", 0)) for r in recs)
    avg_watch = round(sum(float(r.get("avg_watch_pct", 0.0)) for r in recs) / len(recs), 1) if recs else 0.0
    avg_ret3 = round(sum(float(r.get("retention_3s_pct", 0.0)) for r in recs) / len(recs), 1) if recs else 0.0
    return {
        "summary": {
            "total_records": len(recs),
            "total_views": total_views,
            "total_shares": total_shares,
            "avg_watch_pct": avg_watch,
            "avg_retention_3s_pct": avg_ret3,
        },
        "by_game": _aggregate_dimension(recs, "game_name"),
        "by_category": _aggregate_dimension(recs, "category"),
        "by_duration": _aggregate_dimension(recs, "duration_bucket"),
        "by_hook_style": _aggregate_dimension(recs, "hook_style"),
        "learned_weights": weights,
        "records": recs[:80],
    }


def _parse_num(val: Any) -> float:
    s = str(val or "0").strip().replace("%", "").replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def import_tiktok_studio_csv(raw_bytes: bytes) -> dict[str, Any]:
    """Parse official TikTok Studio CSV export (PT-BR or EN columns, comma or semicolon)
    and cross-match against Library clips to learn channel retention patterns."""
    text = ""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text.strip():
        raise XRayError("Arquivo CSV vazio ou ilegivel.", category="UPLOAD", status_code=400)

    first_line = text.splitlines()[0]
    delim = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        raise XRayError("Cabecalho CSV do TikTok Studio nao encontrado.", category="UPLOAD", status_code=400)

    clips = list_clips()
    imported = 0

    def _pick(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
        lower_map = {str(k or "").strip().lower(): v for k, v in row.items()}
        for cand in candidates:
            for k, v in lower_map.items():
                if cand in k:
                    return v
        return ""

    for row in reader:
        title = str(_pick(row, ("title", "titulo", "video", "caption", "descricao")) or "").strip()
        views = int(_parse_num(_pick(row, ("views", "visualiz", "total views"))))
        if not title and views <= 0:
            continue
        shares = int(_parse_num(_pick(row, ("shares", "compartilh"))))
        dur = _parse_num(_pick(row, ("duration", "duracao", "length"))) or 28.0
        avg_watch_pct = _parse_num(_pick(row, ("watched full", "avg watch %", "media assistida", "watch %", "completion")))
        if avg_watch_pct <= 0:
            avg_sec = _parse_num(_pick(row, ("average time", "tempo medio")))
            if avg_sec > 0 and dur > 0:
                avg_watch_pct = min(150.0, (avg_sec / dur) * 100.0)
            else:
                avg_watch_pct = 55.0
        ret_3s = _parse_num(_pick(row, ("3s", "3 sec", "retencao 3", "retained")))
        if ret_3s <= 0:
            ret_3s = min(98.0, max(35.0, avg_watch_pct * 0.92))

        # Match against Library clip if present
        matched = next(
            (c for c in clips if title and title.lower()[:24] in str(c.get("title") or "").lower()),
            None,
        )
        add_performance_record(
            {
                "clip_id": matched["id"] if matched else "",
                "title": title or (matched["title"] if matched else "Video TikTok Studio"),
                "game_name": (matched.get("detected_game") if matched else "") or "TikTok Importado",
                "category": (matched.get("category") if matched else "") or "combate_intenso",
                "platform": "tiktok_long" if dur >= 60 else "tiktok_reels",
                "duration_sec": dur,
                "hook_style": "Gancho de Pico 1.8s",
                "views": views,
                "avg_watch_pct": avg_watch_pct,
                "retention_3s_pct": ret_3s,
                "shares": shares,
                "source": "tiktok_csv",
            }
        )
        imported += 1

    return {"ok": True, "imported_count": imported, "dashboard": get_analytics_dashboard()}


def sync_youtube_analytics() -> dict[str, Any]:
    """Pull real metrics from connected YouTube account (YouTube Analytics API v2 with fallback to Data API v3)."""
    from backend.youtube_uploader import _load_valid_credentials

    creds = _load_valid_credentials()
    if creds is None:
        raise XRayError(
            "Conta YouTube nao conectada. Conecte em Configuracoes -> Conexao Oficial YouTube para sincronizar metricas.",
            category="YOUTUBE_AUTH",
            status_code=401,
        )

    from googleapiclient.discovery import build

    synced = 0
    note = ""
    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        ch_resp = yt.channels().list(part="contentDetails", mine=True).execute()
        items = ch_resp.get("items") or []
        uploads_pl = items[0]["contentDetails"]["relatedPlaylists"]["uploads"] if items else ""
        if not uploads_pl:
            return {"ok": True, "synced_count": 0, "note": "Nenhum video publicado encontrado no canal.", "dashboard": get_analytics_dashboard()}

        pl_items = yt.playlistItems().list(part="snippet,contentDetails", playlistId=uploads_pl, maxResults=15).execute()
        vid_ids = [it["contentDetails"]["videoId"] for it in (pl_items.get("items") or []) if it.get("contentDetails", {}).get("videoId")]
        if not vid_ids:
            return {"ok": True, "synced_count": 0, "note": "Canal sem videos recentes.", "dashboard": get_analytics_dashboard()}

        vids_resp = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(vid_ids)).execute()

        # Optional YouTube Analytics API enrichment (if user granted yt-analytics.readonly scope)
        analytics_by_vid: dict[str, dict[str, float]] = {}
        try:
            yta = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
            end_d = datetime.now().strftime("%Y-%m-%d")
            start_d = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            ar = yta.reports().query(
                ids="channel==MINE",
                startDate=start_d,
                endDate=end_d,
                metrics="views,averageViewPercentage,shares",
                dimensions="video",
                filters=f"video=={','.join(vid_ids[:10])}",
            ).execute()
            for row in (ar.get("rows") or []):
                if len(row) >= 4:
                    analytics_by_vid[str(row[0])] = {
                        "views": float(row[1]),
                        "avg_watch_pct": float(row[2]),
                        "shares": float(row[3]),
                    }
            note = "Metricas puxadas via YouTube Analytics API v2."
        except Exception:
            note = "Metricas basicas puxadas via YouTube Data API v3 (ative yt-analytics.readonly para % assistida exata)."

        for v in (vids_resp.get("items") or []):
            vid = v.get("id", "")
            sn = v.get("snippet") or {}
            st = v.get("statistics") or {}
            extra = analytics_by_vid.get(vid) or {}
            views = int(extra.get("views") or st.get("viewCount") or 0)
            avg_watch = float(extra.get("avg_watch_pct") or 68.0)
            shares = int(extra.get("shares") or 0)
            add_performance_record(
                {
                    "clip_id": vid,
                    "title": sn.get("title") or f"YouTube Video {vid}",
                    "game_name": "YouTube Channel",
                    "category": "combate_intenso",
                    "platform": "youtube_shorts",
                    "duration_sec": 42.0,
                    "hook_style": "Gancho de Pico 1.8s",
                    "views": views,
                    "avg_watch_pct": avg_watch,
                    "retention_3s_pct": min(98.0, avg_watch * 0.95),
                    "shares": shares,
                    "source": "youtube_api",
                }
            )
            synced += 1

        return {"ok": True, "synced_count": synced, "note": note, "dashboard": get_analytics_dashboard()}
    except Exception as exc:
        raise XRayError(
            "Falha ao sincronizar metricas com o YouTube Analytics.",
            category="YOUTUBE_AUTH",
            technical_detail=str(exc),
            status_code=500,
        )
