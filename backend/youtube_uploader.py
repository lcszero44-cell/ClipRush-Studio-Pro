"""Official YouTube Data API v3 integration & Daily Shorts Autopilot Scheduler.
Uses google-api-python-client + google-auth-oauthlib with local OAuth2 flow,
automatic token refresh, resumable Shorts upload (#Shorts), and background daily publishing."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import DATA_DIR, load_settings, save_settings
from backend.errors import XRayError

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
TOKEN_FILE = DATA_DIR / "youtube_token.json"
CHANNEL_META_FILE = DATA_DIR / "youtube_channel.json"
AUTOPILOT_FILE = DATA_DIR / "youtube_autopilot.json"

_LOCK = threading.RLock()
_OAUTH_STATE: dict[str, Any] = {
    "in_progress": False,
    "last_error": "",
}
_DAEMON_STARTED = False


def _read_autopilot() -> dict[str, Any]:
    with _LOCK:
        default = {
            "enabled": False,
            "daily_time": "18:30",
            "privacy_status": "public",  # public | unlisted | private
            "last_publish_date": "",
            "queue": [],
        }
        if not AUTOPILOT_FILE.exists():
            return default
        try:
            saved = json.loads(AUTOPILOT_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                default.update(saved)
        except Exception:
            pass
        return default


def _write_autopilot(data: dict[str, Any]) -> None:
    with _LOCK:
        tmp = AUTOPILOT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(AUTOPILOT_FILE)


def get_client_secrets_path() -> Path:
    settings = load_settings()
    raw = (settings.get("youtube_client_secrets_path") or "").strip()
    return Path(raw) if raw else (DATA_DIR / "client_secrets.json")


def _load_valid_credentials():
    """Return valid Google OAuth2 Credentials, auto-refreshing if needed, or None."""
    if not TOKEN_FILE.exists():
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        if creds and creds.valid:
            return creds
    except Exception:
        return None
    return None


def _fetch_channel_info(creds) -> dict[str, str]:
    try:
        from googleapiclient.discovery import build

        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items") or []
        if items:
            ch = items[0]
            info = {
                "channel_id": ch.get("id", ""),
                "channel_title": ch.get("snippet", {}).get("title", "Canal YouTube Conectado"),
            }
            CHANNEL_META_FILE.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
            return info
    except Exception:
        pass
    return {"channel_id": "", "channel_title": "Conta YouTube Conectada"}


def get_youtube_connection_status() -> dict[str, Any]:
    secrets_path = get_client_secrets_path()
    creds = _load_valid_credentials()
    channel_title = ""
    channel_id = ""
    if creds is not None:
        if CHANNEL_META_FILE.exists():
            try:
                meta = json.loads(CHANNEL_META_FILE.read_text(encoding="utf-8"))
                channel_title = meta.get("channel_title", "")
                channel_id = meta.get("channel_id", "")
            except Exception:
                pass
        if not channel_title:
            info = _fetch_channel_info(creds)
            channel_title = info["channel_title"]
            channel_id = info["channel_id"]

    return {
        "connected": creds is not None,
        "channel_title": channel_title,
        "channel_id": channel_id,
        "client_secrets_path": str(secrets_path),
        "client_secrets_exists": secrets_path.exists() and secrets_path.is_file(),
        "oauth_in_progress": bool(_OAUTH_STATE["in_progress"]),
        "last_error": _OAUTH_STATE["last_error"],
    }


def start_oauth_flow(custom_secrets_path: Optional[str] = None) -> dict[str, Any]:
    """Start local OAuth2 InstalledAppFlow in a background thread so the browser opens for login."""
    if custom_secrets_path and custom_secrets_path.strip():
        save_settings({"youtube_client_secrets_path": custom_secrets_path.strip().strip('"')})

    secrets_path = get_client_secrets_path()
    if not secrets_path.exists() or not secrets_path.is_file():
        return {
            "started": False,
            "needs_secrets": True,
            "client_secrets_path": str(secrets_path),
            "console_url": "https://console.cloud.google.com/apis/credentials",
            "message": "Arquivo client_secrets.json ainda nao configurado. Siga o assistente guiado para conectar sua conta YouTube.",
        }

    # Validate basic JSON structure before starting flow
    try:
        parsed = json.loads(secrets_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not ("installed" in parsed or "web" in parsed):
            raise ValueError("JSON nao contem chave 'installed' ou 'web'.")
    except Exception as exc:
        raise XRayError(
            "O arquivo client_secrets.json selecionado e invalido. Baixe o JSON oficial de credenciais OAuth 2.0 (Aplicativo para Computador) no Google Cloud Console.",
            category="YOUTUBE_AUTH",
            technical_detail=str(exc),
            status_code=400,
        )

    if _OAUTH_STATE["in_progress"]:
        return {"started": True, "message": "Autenticacao OAuth2 ja esta aberta no navegador."}

    _OAUTH_STATE["in_progress"] = True
    _OAUTH_STATE["last_error"] = ""

    def _run() -> None:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
            creds = flow.run_local_server(
                port=0,
                open_browser=True,
                success_message="Conta YouTube conectada com sucesso ao ClipRush Studio Pro! Voce ja pode fechar esta aba.",
            )
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            _fetch_channel_info(creds)
            _OAUTH_STATE["last_error"] = ""
        except Exception as exc:
            _OAUTH_STATE["last_error"] = str(exc)
        finally:
            _OAUTH_STATE["in_progress"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {
        "started": True,
        "message": "Navegador aberto para autorizar sua conta Google/YouTube via OAuth2 local.",
    }


def disconnect_youtube() -> dict[str, Any]:
    for fp in (TOKEN_FILE, CHANNEL_META_FILE):
        if fp.exists():
            try:
                fp.unlink()
            except OSError:
                pass
    _OAUTH_STATE["last_error"] = ""
    return {"connected": False, "message": "Conta YouTube desconectada com sucesso."}


def upload_youtube_short(
    file_path: str,
    title: str,
    description: str = "",
    hashtags: Optional[list[str]] = None,
    privacy_status: str = "public",
) -> dict[str, Any]:
    """Upload an MP4 clip as a YouTube Short via official YouTube Data API v3."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise XRayError(
            f"Arquivo MP4 nao encontrado para upload no YouTube: {file_path}",
            category="YOUTUBE_UPLOAD",
            status_code=404,
        )

    creds = _load_valid_credentials()
    if creds is None:
        raise XRayError(
            "Conta YouTube nao conectada ou sessao expirada. Va em Configuracoes -> Conexao Oficial YouTube e clique em 'Conectar Conta YouTube'.",
            category="YOUTUBE_AUTH",
            status_code=401,
        )

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    clean_title = (title or "Gameplay Epica").strip()
    if "#shorts" not in clean_title.lower():
        clean_title = f"{clean_title[:90].rstrip()} #Shorts"
    else:
        clean_title = clean_title[:100]

    tags_list = [str(t).strip() for t in (hashtags or []) if str(t).strip()]
    if not any(t.lower() == "#shorts" for t in tags_list):
        tags_list.append("#Shorts")

    full_desc = (description or "").strip()
    tags_line = " ".join(tags_list)
    if tags_line and tags_line not in full_desc:
        full_desc = f"{full_desc}\n\n{tags_line}".strip()
    if "#shorts" not in full_desc.lower():
        full_desc = f"{full_desc}\n#Shorts".strip()

    api_tags = [t.lstrip("#") for t in tags_list][:15]
    if "Shorts" not in api_tags:
        api_tags.append("Shorts")

    if privacy_status not in ("public", "unlisted", "private"):
        privacy_status = "public"

    body = {
        "snippet": {
            "title": clean_title,
            "description": full_desc[:4900],
            "tags": api_tags,
            "categoryId": "20",  # Gaming
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        media = MediaFileUpload(str(p), mimetype="video/mp4", chunksize=4 * 1024 * 1024, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = response.get("id", "")
        return {
            "video_id": video_id,
            "video_url": f"https://youtube.com/shorts/{video_id}" if video_id else "",
            "title": clean_title,
            "privacy_status": privacy_status,
        }
    except Exception as exc:
        raise XRayError(
            "Falha ao enviar o video para a API oficial do YouTube. Verifique sua cota da YouTube Data API v3 e permissao da conta.",
            category="YOUTUBE_UPLOAD",
            technical_detail=str(exc),
            status_code=500,
            context={"file_path": file_path},
        )


# ---- Daily Autopilot Queue & Background Scheduler ----

def get_autopilot_state() -> dict[str, Any]:
    state = _read_autopilot()
    # Compute estimated publish date/time for each pending 'agendado' item
    now = datetime.now()
    daily_time = state.get("daily_time", "18:30")
    try:
        hh, mm = [int(x) for x in daily_time.split(":")[:2]]
    except Exception:
        hh, mm = 18, 30

    today_str = now.strftime("%Y-%m-%d")
    already_published_today = state.get("last_publish_date") == today_str
    next_slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if already_published_today or now >= next_slot:
        next_slot = next_slot + timedelta(days=1)

    day_offset = 0
    for item in state.get("queue", []):
        if item.get("status") == "agendado":
            est = next_slot + timedelta(days=day_offset)
            item["estimated_publish_at"] = est.strftime("%Y-%m-%d %H:%M")
            day_offset += 1
        else:
            item["estimated_publish_at"] = item.get("published_at") or ""

    state["youtube_status"] = get_youtube_connection_status()
    return state


def update_autopilot_config(
    enabled: Optional[bool] = None,
    daily_time: Optional[str] = None,
    privacy_status: Optional[str] = None,
) -> dict[str, Any]:
    state = _read_autopilot()
    if enabled is not None:
        state["enabled"] = bool(enabled)
    if daily_time is not None:
        # Validate HH:MM
        parts = daily_time.strip().split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            h = max(0, min(23, int(parts[0])))
            m = max(0, min(59, int(parts[1])))
            state["daily_time"] = f"{h:02d}:{m:02d}"
    if privacy_status in ("public", "unlisted", "private"):
        state["privacy_status"] = privacy_status
    _write_autopilot(state)
    return get_autopilot_state()


def enqueue_autopilot_clips(clips: list[dict[str, Any]]) -> dict[str, Any]:
    state = _read_autopilot()
    queue = state.get("queue", [])
    existing_clip_ids = {q.get("clip_id") for q in queue if q.get("clip_id")}
    added = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for c in clips:
        cid = c.get("id") or c.get("clip_id")
        if cid and cid in existing_clip_ids:
            continue
        if not c.get("file_path"):
            continue
        queue.append(
            {
                "id": f"ytq_{uuid.uuid4().hex[:10]}",
                "clip_id": cid or "",
                "title": c.get("title") or "Clipe Gameplay #Shorts",
                "description": c.get("description") or "",
                "hashtags": c.get("hashtags") or ["#Shorts"],
                "file_path": c.get("file_path"),
                "status": "agendado",  # agendado | publicando | publicado | erro
                "video_url": "",
                "video_id": "",
                "error_message": "",
                "published_at": "",
                "added_at": now_iso,
            }
        )
        if cid:
            existing_clip_ids.add(cid)
        added += 1
    state["queue"] = queue
    _write_autopilot(state)
    out = get_autopilot_state()
    out["added_count"] = added
    return out


def remove_autopilot_item(item_id: str) -> dict[str, Any]:
    state = _read_autopilot()
    state["queue"] = [q for q in state.get("queue", []) if q.get("id") != item_id]
    _write_autopilot(state)
    return get_autopilot_state()


def publish_autopilot_item(item_id: str, mark_daily_slot: bool = False) -> dict[str, Any]:
    """Upload a specific queued item via official YouTube API v3 and update its status/link."""
    state = _read_autopilot()
    target = next((q for q in state.get("queue", []) if q.get("id") == item_id), None)
    if not target:
        raise XRayError("Clipe nao encontrado na fila do Piloto Automatico YouTube.", category="YOUTUBE_UPLOAD", status_code=404)

    target["status"] = "publicando"
    target["error_message"] = ""
    _write_autopilot(state)

    try:
        res = upload_youtube_short(
            file_path=target["file_path"],
            title=target["title"],
            description=target.get("description", ""),
            hashtags=target.get("hashtags", []),
            privacy_status=state.get("privacy_status", "public"),
        )
        state = _read_autopilot()
        for q in state.get("queue", []):
            if q.get("id") == item_id:
                q["status"] = "publicado"
                q["video_id"] = res["video_id"]
                q["video_url"] = res["video_url"]
                q["published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                q["error_message"] = ""
                break
        if mark_daily_slot:
            state["last_publish_date"] = datetime.now().strftime("%Y-%m-%d")
        _write_autopilot(state)
        return {"ok": True, "result": res, "state": get_autopilot_state()}
    except Exception as exc:
        err_msg = exc.message_pt if isinstance(exc, XRayError) else str(exc)
        state = _read_autopilot()
        for q in state.get("queue", []):
            if q.get("id") == item_id:
                q["status"] = "erro"
                q["error_message"] = err_msg
                break
        _write_autopilot(state)
        raise


def start_autopilot_daemon() -> None:
    """Start background scheduler thread that publishes 1 fresh YouTube Short per day at daily_time."""
    global _DAEMON_STARTED
    if _DAEMON_STARTED:
        return
    _DAEMON_STARTED = True

    def _loop() -> None:
        while True:
            try:
                state = _read_autopilot()
                if state.get("enabled"):
                    now = datetime.now()
                    today_str = now.strftime("%Y-%m-%d")
                    daily_time = state.get("daily_time", "18:30")
                    now_hm = now.strftime("%H:%M")
                    if state.get("last_publish_date") != today_str and now_hm >= daily_time:
                        next_item = next(
                            (q for q in state.get("queue", []) if q.get("status") == "agendado"),
                            None,
                        )
                        if next_item and _load_valid_credentials() is not None:
                            try:
                                publish_autopilot_item(next_item["id"], mark_daily_slot=True)
                            except Exception:
                                # Error is already recorded on item['status']='erro' & item['error_message']
                                pass
            except Exception:
                pass
            time.sleep(30)

    threading.Thread(target=_loop, daemon=True, name="YouTubeDailyAutopilot").start()
