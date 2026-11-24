"""Official YouTube Data API v3 integration & Protected Daily Shorts Autopilot Scheduler.
P0 Security & Account Protection Upgrades:
- Credentials & OAuth2 tokens encrypted at rest via Windows DPAPI (win32crypt.CryptProtectData)
  with transparent migration from legacy plaintext youtube_token.json / client_secrets.json.
- Clear warning for unverified Google Cloud API projects (videos uploaded as PRIVATE by default).
- Mandatory explicit human approval before any upload by default (require_human_approval=True).
- Configurable daily upload cap (default: 2 uploads/day) enforced on both manual & autopilot uploads.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import DATA_DIR, load_settings, save_settings
from backend.errors import XRayError

try:
    import win32crypt
except Exception:
    win32crypt = None  # type: ignore[assignment]

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
LEGACY_TOKEN_FILE = DATA_DIR / "youtube_token.json"
TOKEN_DPAPI_FILE = DATA_DIR / "youtube_token.dpapi"
LEGACY_SECRETS_FILE = DATA_DIR / "client_secrets.json"
SECRETS_DPAPI_FILE = DATA_DIR / "client_secrets.dpapi"
CHANNEL_META_FILE = DATA_DIR / "youtube_channel.json"
AUTOPILOT_FILE = DATA_DIR / "youtube_autopilot.json"

_DPAPI_ENTROPY = b"ClipRushStudioPro_P0_DPAPI_Vault_v1"

YOUTUBE_AUDIT_WARNING = (
    "Projetos de API sem auditoria do Google enviam videos como PRIVADOS por padrao — "
    "teste 1 video antes de agendar em lote."
)

_LOCK = threading.RLock()
_OAUTH_STATE: dict[str, Any] = {
    "in_progress": False,
    "last_error": "",
}
_DAEMON_STARTED = False


# ---- Windows DPAPI Native Vault Helpers ----

def dpapi_available() -> bool:
    return os.name == "nt" and win32crypt is not None


def dpapi_encrypt_bytes(plaintext: bytes, label: str = "ClipRushVault") -> bytes:
    if not dpapi_available():
        raise XRayError(
            "Cofre nativo do Windows (DPAPI / win32crypt) indisponivel neste ambiente.",
            category="SEGURANCA",
            status_code=500,
        )
    return win32crypt.CryptProtectData(plaintext, label, _DPAPI_ENTROPY, None, None, 0)


def dpapi_decrypt_bytes(ciphertext: bytes) -> bytes:
    if not dpapi_available():
        raise XRayError(
            "Cofre nativo do Windows (DPAPI / win32crypt) indisponivel para descriptografar credenciais.",
            category="SEGURANCA",
            status_code=500,
        )
    _, data = win32crypt.CryptUnprotectData(ciphertext, _DPAPI_ENTROPY, None, None, 0)
    return bytes(data)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _save_protected_token(token_json_str: str) -> None:
    enc = dpapi_encrypt_bytes(token_json_str.encode("utf-8"), label="ClipRushYouTubeToken")
    _atomic_write_bytes(TOKEN_DPAPI_FILE, enc)
    # Remove legacy plaintext token file once safely vaulted
    if LEGACY_TOKEN_FILE.exists():
        try:
            LEGACY_TOKEN_FILE.unlink()
        except OSError:
            pass


def _migrate_legacy_token_if_needed() -> bool:
    """Transparently migrate legacy plaintext youtube_token.json -> DPAPI encrypted youtube_token.dpapi."""
    if LEGACY_TOKEN_FILE.exists() and not TOKEN_DPAPI_FILE.exists():
        try:
            raw = LEGACY_TOKEN_FILE.read_text(encoding="utf-8")
            json.loads(raw)  # Validate JSON
            _save_protected_token(raw)
            return True
        except Exception:
            return False
    return False


def _load_protected_token_json() -> Optional[dict[str, Any]]:
    _migrate_legacy_token_if_needed()
    if TOKEN_DPAPI_FILE.exists():
        try:
            plain = dpapi_decrypt_bytes(TOKEN_DPAPI_FILE.read_bytes())
            return json.loads(plain.decode("utf-8"))
        except Exception:
            return None
    return None


def save_protected_client_secrets(raw_bytes: bytes) -> dict[str, Any]:
    """Validate and encrypt client_secrets.json into Windows DPAPI vault (no plaintext left behind)."""
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(parsed, dict) or not ("installed" in parsed or "web" in parsed):
            raise ValueError("JSON sem estrutura 'installed' ou 'web'.")
    except Exception as exc:
        raise XRayError(
            "O JSON enviado nao parece um arquivo client_secrets.json valido do Google Cloud.",
            category="YOUTUBE_AUTH",
            technical_detail=str(exc),
            status_code=400,
        )

    enc = dpapi_encrypt_bytes(raw_bytes, label="ClipRushYouTubeClientSecrets")
    _atomic_write_bytes(SECRETS_DPAPI_FILE, enc)
    if LEGACY_SECRETS_FILE.exists():
        try:
            LEGACY_SECRETS_FILE.unlink()
        except OSError:
            pass
    save_settings({"youtube_client_secrets_path": str(SECRETS_DPAPI_FILE)})
    return {"ok": True, "path": str(SECRETS_DPAPI_FILE), "vault": "Windows DPAPI"}


def _migrate_legacy_secrets_if_needed() -> None:
    if LEGACY_SECRETS_FILE.exists() and not SECRETS_DPAPI_FILE.exists():
        try:
            raw = LEGACY_SECRETS_FILE.read_bytes()
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict) and ("installed" in parsed or "web" in parsed):
                enc = dpapi_encrypt_bytes(raw, label="ClipRushYouTubeClientSecrets")
                _atomic_write_bytes(SECRETS_DPAPI_FILE, enc)
                LEGACY_SECRETS_FILE.unlink()
                save_settings({"youtube_client_secrets_path": str(SECRETS_DPAPI_FILE)})
        except Exception:
            pass


def _load_client_secrets_dict() -> Optional[dict[str, Any]]:
    _migrate_legacy_secrets_if_needed()
    if SECRETS_DPAPI_FILE.exists():
        try:
            raw = dpapi_decrypt_bytes(SECRETS_DPAPI_FILE.read_bytes())
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict) and ("installed" in parsed or "web" in parsed):
                return parsed
        except Exception:
            pass

    custom = get_client_secrets_path()
    if custom.exists() and custom.is_file() and custom.suffix.lower() == ".json":
        try:
            raw = custom.read_bytes()
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict) and ("installed" in parsed or "web" in parsed):
                return parsed
        except Exception:
            pass
    return None


# ---- Autopilot Persistence & Account Safety ----

def _read_autopilot() -> dict[str, Any]:
    with _LOCK:
        settings = load_settings()
        default: dict[str, Any] = {
            "enabled": False,
            "daily_time": "18:30",
            "privacy_status": "private",  # safe default for unverified Google Cloud API projects
            "require_human_approval": bool(settings.get("youtube_require_human_approval", True)),
            "daily_upload_limit": int(settings.get("youtube_daily_upload_limit", 2)),
            "last_publish_date": "",
            "daily_uploads_log": {},
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
        default["daily_upload_limit"] = max(1, min(10, int(default.get("daily_upload_limit", 2))))
        if not isinstance(default.get("daily_uploads_log"), dict):
            default["daily_uploads_log"] = {}
        return default


def _write_autopilot(data: dict[str, Any]) -> None:
    with _LOCK:
        raw = json.dumps(data, indent=2, ensure_ascii=False)
        if AUTOPILOT_FILE.exists():
            try:
                shutil.copy2(AUTOPILOT_FILE, AUTOPILOT_FILE.with_suffix(".json.bak"))
            except OSError:
                pass
        tmp = AUTOPILOT_FILE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(AUTOPILOT_FILE)


def get_client_secrets_path() -> Path:
    _migrate_legacy_secrets_if_needed()
    if SECRETS_DPAPI_FILE.exists():
        return SECRETS_DPAPI_FILE
    settings = load_settings()
    raw = (settings.get("youtube_client_secrets_path") or "").strip()
    return Path(raw) if raw else LEGACY_SECRETS_FILE


def _load_valid_credentials():
    """Return valid Google OAuth2 Credentials decrypted from Windows DPAPI vault, auto-refreshing if needed."""
    info = _load_protected_token_json()
    if not info:
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(info, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_protected_token(creds.to_json())
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
    _migrate_legacy_token_if_needed()
    _migrate_legacy_secrets_if_needed()
    secrets_path = get_client_secrets_path()
    secrets_ok = _load_client_secrets_dict() is not None
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
        "client_secrets_exists": secrets_ok,
        "vault_protected": dpapi_available(),
        "vault_engine": "Windows DPAPI (CryptProtectData)" if dpapi_available() else "Indisponivel",
        "audit_warning": YOUTUBE_AUDIT_WARNING,
        "oauth_in_progress": bool(_OAUTH_STATE["in_progress"]),
        "last_error": _OAUTH_STATE["last_error"],
    }


def start_oauth_flow(custom_secrets_path: Optional[str] = None) -> dict[str, Any]:
    """Start local OAuth2 InstalledAppFlow in a background thread using DPAPI-protected credentials."""
    if custom_secrets_path and custom_secrets_path.strip():
        p = Path(custom_secrets_path.strip().strip('"'))
        if p.exists() and p.is_file() and p.suffix.lower() == ".json":
            save_protected_client_secrets(p.read_bytes())
        else:
            save_settings({"youtube_client_secrets_path": str(p)})

    client_config = _load_client_secrets_dict()
    if not client_config:
        return {
            "started": False,
            "needs_secrets": True,
            "client_secrets_path": str(get_client_secrets_path()),
            "console_url": "https://console.cloud.google.com/apis/credentials",
            "audit_warning": YOUTUBE_AUDIT_WARNING,
            "message": "Arquivo client_secrets.json ainda nao configurado. Siga o assistente guiado para conectar sua conta YouTube.",
        }

    if _OAUTH_STATE["in_progress"]:
        return {"started": True, "message": "Autenticacao OAuth2 ja esta aberta no navegador."}

    _OAUTH_STATE["in_progress"] = True
    _OAUTH_STATE["last_error"] = ""

    def _run() -> None:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(
                port=0,
                open_browser=True,
                success_message="Conta YouTube conectada com seguranca (DPAPI) ao ClipRush Studio Pro! Voce ja pode fechar esta aba.",
            )
            _save_protected_token(creds.to_json())
            _fetch_channel_info(creds)
            _OAUTH_STATE["last_error"] = ""
        except Exception as exc:
            _OAUTH_STATE["last_error"] = str(exc)
        finally:
            _OAUTH_STATE["in_progress"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {
        "started": True,
        "audit_warning": YOUTUBE_AUDIT_WARNING,
        "message": "Navegador aberto para autorizar sua conta Google/YouTube via OAuth2 local (token protegido com Windows DPAPI).",
    }


def disconnect_youtube() -> dict[str, Any]:
    for fp in (TOKEN_DPAPI_FILE, LEGACY_TOKEN_FILE, CHANNEL_META_FILE):
        if fp.exists():
            try:
                fp.unlink()
            except OSError:
                pass
    _OAUTH_STATE["last_error"] = ""
    return {"connected": False, "message": "Conta YouTube desconectada e tokens DPAPI removidos com sucesso."}


def upload_youtube_short(
    file_path: str,
    title: str,
    description: str = "",
    hashtags: Optional[list[str]] = None,
    privacy_status: str = "private",
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
        privacy_status = "private"

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


# ---- Protected Daily Autopilot Queue & Background Scheduler ----

def _uploads_today(state: dict[str, Any]) -> int:
    today_str = datetime.now().strftime("%Y-%m-%d")
    return int((state.get("daily_uploads_log") or {}).get(today_str, 0))


def get_autopilot_state() -> dict[str, Any]:
    state = _read_autopilot()
    now = datetime.now()
    daily_time = state.get("daily_time", "18:30")
    try:
        hh, mm = [int(x) for x in daily_time.split(":")[:2]]
    except Exception:
        hh, mm = 18, 30

    today_str = now.strftime("%Y-%m-%d")
    uploads_today = _uploads_today(state)
    daily_limit = int(state.get("daily_upload_limit", 2))
    next_slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if uploads_today >= daily_limit or state.get("last_publish_date") == today_str or now >= next_slot:
        next_slot = next_slot + timedelta(days=1)

    day_offset = 0
    for item in state.get("queue", []):
        if item.get("status") in ("agendado", "aguardando_aprovacao"):
            est = next_slot + timedelta(days=day_offset)
            item["estimated_publish_at"] = est.strftime("%Y-%m-%d %H:%M")
            day_offset += 1
        else:
            item["estimated_publish_at"] = item.get("published_at") or ""

    state["uploads_today"] = uploads_today
    state["daily_remaining"] = max(0, daily_limit - uploads_today)
    state["audit_warning"] = YOUTUBE_AUDIT_WARNING
    state["youtube_status"] = get_youtube_connection_status()
    return state


def update_autopilot_config(
    enabled: Optional[bool] = None,
    daily_time: Optional[str] = None,
    privacy_status: Optional[str] = None,
    require_human_approval: Optional[bool] = None,
    daily_upload_limit: Optional[int] = None,
) -> dict[str, Any]:
    state = _read_autopilot()
    if enabled is not None:
        state["enabled"] = bool(enabled)
    if daily_time is not None:
        parts = daily_time.strip().split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            h = max(0, min(23, int(parts[0])))
            m = max(0, min(59, int(parts[1])))
            state["daily_time"] = f"{h:02d}:{m:02d}"
    if privacy_status in ("public", "unlisted", "private"):
        state["privacy_status"] = privacy_status
    if require_human_approval is not None:
        state["require_human_approval"] = bool(require_human_approval)
        save_settings({"youtube_require_human_approval": bool(require_human_approval)})
    if daily_upload_limit is not None:
        lim = max(1, min(10, int(daily_upload_limit)))
        state["daily_upload_limit"] = lim
        save_settings({"youtube_daily_upload_limit": lim})
    _write_autopilot(state)
    return get_autopilot_state()


def enqueue_autopilot_clips(clips: list[dict[str, Any]]) -> dict[str, Any]:
    state = _read_autopilot()
    queue = state.get("queue", [])
    existing_clip_ids = {q.get("clip_id") for q in queue if q.get("clip_id")}
    require_approval = bool(state.get("require_human_approval", True))
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
                "status": "aguardando_aprovacao" if require_approval else "agendado",
                "approved": not require_approval,
                "approved_at": "" if require_approval else now_iso,
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


def approve_autopilot_item(item_id: str, approved: bool = True) -> dict[str, Any]:
    """Explicit human approval gate before any queued video is allowed to upload."""
    state = _read_autopilot()
    found = False
    for q in state.get("queue", []):
        if q.get("id") == item_id:
            found = True
            q["approved"] = bool(approved)
            if approved:
                q["approved_at"] = datetime.now(timezone.utc).isoformat()
                if q.get("status") == "aguardando_aprovacao":
                    q["status"] = "agendado"
            else:
                q["approved_at"] = ""
                if q.get("status") == "agendado":
                    q["status"] = "aguardando_aprovacao"
            break
    if not found:
        raise XRayError("Clipe nao encontrado na fila do YouTube para aprovacao.", category="YOUTUBE_UPLOAD", status_code=404)
    _write_autopilot(state)
    return get_autopilot_state()


def remove_autopilot_item(item_id: str) -> dict[str, Any]:
    state = _read_autopilot()
    state["queue"] = [q for q in state.get("queue", []) if q.get("id") != item_id]
    _write_autopilot(state)
    return get_autopilot_state()


def publish_autopilot_item(
    item_id: str,
    mark_daily_slot: bool = False,
    human_approved: bool = False,
) -> dict[str, Any]:
    """Upload a specific queued item via official YouTube API v3 with human approval & daily cap enforcement."""
    state = _read_autopilot()
    target = next((q for q in state.get("queue", []) if q.get("id") == item_id), None)
    if not target:
        raise XRayError("Clipe nao encontrado na fila do Piloto Automatico YouTube.", category="YOUTUBE_UPLOAD", status_code=404)

    # 1. Enforce mandatory human approval when active
    require_approval = bool(state.get("require_human_approval", True))
    if require_approval and not (target.get("approved") or human_approved):
        raise XRayError(
            "Aprovacao humana obrigatoria ativa para proteger sua conta YouTube. "
            "Clique em 'Aprovar para Publicacao' antes de enviar este video.",
            category="YOUTUBE_PROTECTION",
            status_code=403,
        )

    # 2. Enforce daily upload limit (default: 2 uploads/day)
    daily_limit = int(state.get("daily_upload_limit", 2))
    uploads_today = _uploads_today(state)
    if uploads_today >= daily_limit:
        raise XRayError(
            f"Limite diario de protecao da conta atingido ({uploads_today}/{daily_limit} uploads hoje). "
            "Aguarde amanha ou ajuste o limite em Seguranca/Agendador.",
            category="YOUTUBE_PROTECTION",
            status_code=429,
        )

    if human_approved and not target.get("approved"):
        target["approved"] = True
        target["approved_at"] = datetime.now(timezone.utc).isoformat()

    target["status"] = "publicando"
    target["error_message"] = ""
    _write_autopilot(state)

    try:
        res = upload_youtube_short(
            file_path=target["file_path"],
            title=target["title"],
            description=target.get("description", ""),
            hashtags=target.get("hashtags", []),
            privacy_status=state.get("privacy_status", "private"),
        )
        state = _read_autopilot()
        today_str = datetime.now().strftime("%Y-%m-%d")
        log = state.setdefault("daily_uploads_log", {})
        log[today_str] = int(log.get(today_str, 0)) + 1
        for q in state.get("queue", []):
            if q.get("id") == item_id:
                q["status"] = "publicado"
                q["video_id"] = res["video_id"]
                q["video_url"] = res["video_url"]
                q["published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                q["error_message"] = ""
                break
        if mark_daily_slot:
            state["last_publish_date"] = today_str
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
    """Start background scheduler thread respecting human approval & daily upload limit."""
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
                    daily_limit = int(state.get("daily_upload_limit", 2))
                    uploads_today = _uploads_today(state)
                    require_approval = bool(state.get("require_human_approval", True))
                    if (
                        state.get("last_publish_date") != today_str
                        and uploads_today < daily_limit
                        and now_hm >= daily_time
                    ):
                        next_item = next(
                            (
                                q
                                for q in state.get("queue", [])
                                if q.get("status") == "agendado"
                                and (not require_approval or bool(q.get("approved")))
                            ),
                            None,
                        )
                        if next_item and _load_valid_credentials() is not None:
                            try:
                                publish_autopilot_item(next_item["id"], mark_daily_slot=True)
                            except Exception:
                                pass
            except Exception:
                pass
            time.sleep(30)

    threading.Thread(target=_loop, daemon=True, name="YouTubeDailyAutopilot").start()
