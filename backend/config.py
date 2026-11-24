"""Configuration, paths, FFmpeg/Ollama discovery, and calibrated PS4 game profiles."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

BASE_DIR = Path(r"D:\ClipRush_Pro_V2")
ASSETS_DIR = BASE_DIR / "assets"
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
TEMP_DIR = DATA_DIR / "temp"
THUMBS_DIR = DATA_DIR / "thumbs"
OUTPUT_DIR = BASE_DIR / "saida"
SETTINGS_FILE = DATA_DIR / "settings.json"
CONFIG_FILE = DATA_DIR / "config.json"

PREFERRED_FFMPEG_BIN = Path(r"D:\ClipRush_Universal\ffmpeg-9.0.2-full_build\bin")

for _dir in (ASSETS_DIR, DATA_DIR, UPLOADS_DIR, TEMP_DIR, THUMBS_DIR, OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except OSError:
        return False


def discover_ffmpeg() -> dict[str, Any]:
    """Locate ffmpeg and ffprobe, prioritizing D:\\ClipRush_Universal\\ffmpeg-9.0.2-full_build\\bin."""
    preferred_ffmpeg = PREFERRED_FFMPEG_BIN / "ffmpeg.exe"
    preferred_ffprobe = PREFERRED_FFMPEG_BIN / "ffprobe.exe"
    if _safe_exists(preferred_ffmpeg):
        return {
            "ok": True,
            "ffmpeg": str(preferred_ffmpeg),
            "ffprobe": str(preferred_ffprobe) if _safe_exists(preferred_ffprobe) else (shutil.which("ffprobe") or "ffprobe"),
            "source": "ClipRush_Universal",
        }
    which_ffmpeg = shutil.which("ffmpeg")
    which_ffprobe = shutil.which("ffprobe")
    if which_ffmpeg:
        return {
            "ok": True,
            "ffmpeg": which_ffmpeg,
            "ffprobe": which_ffprobe or "ffprobe",
            "source": "PATH",
        }
    return {
        "ok": False,
        "ffmpeg": "",
        "ffprobe": "",
        "source": "not_found",
    }


DEFAULT_SETTINGS: dict[str, Any] = {
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen3.5:9b",
    "use_ollama_vision": True,
    "ffmpeg_path": "",
    "output_dir": str(OUTPUT_DIR),
    "whisper_model": "base",
    "whisper_language": "auto",  # auto detects PT/EN seamlessly
    "subtitles_default_on": True,
    "hardware_encoder": "auto",  # auto | libx264 | h264_nvenc
    "default_clip_min_sec": 25,
    "default_clip_max_sec": 55,
    "default_clip_target_sec": 38,
    # Multi-Platform & TikTok Monetizable (61-90s) configurable limits
    "tiktok_short_min_sec": 20,
    "tiktok_short_max_sec": 32,
    "tiktok_long_min_sec": 61,
    "tiktok_long_max_sec": 90,
    "tiktok_long_target_sec": 75,
    "youtube_shorts_min_sec": 36,
    "youtube_shorts_max_sec": 55,
    # P0 Peak Hook (1.5-2.0s) + Top Hook Text (2.0-2.5s) + Precision Zoom (1.12x-1.15x)
    "peak_hook_duration_sec": 1.8,
    "hook_text_duration_sec": 2.3,
    "impact_zoom_intensity": 1.14,
    "zoom_min_interval_sec": 6.0,
    "zoom_max_per_clip": 3,
    # P0 Ryzen 5 2500U lightweight 2-stage analysis & CPU protection
    "analysis_detailed_mode": False,
    "cpu_pause_threshold_pct": 80,
    "ollama_keep_alive": "30s",
    "ollama_timeout_sec": 60,
    # P0 YouTube Account Protection
    "youtube_require_human_approval": True,
    "youtube_daily_upload_limit": 2,
    # P1 Theme, Trending Tags, Watch Folder & Scene Mode
    "theme": "dark",  # dark | light | auto
    "scene_mode": "keep_dialogue",  # keep_dialogue | prioritize_gameplay | ignore_cutscenes
    "trending_hashtags": ["#GameplayBR", "#PS4Clips", "#GamerBrasil"],
    "watch_folder_enabled": False,
    "watch_folder_path": r"E:\PS4\SHARE\Video Clips",
    "sensitivity": 0.55,
    "enable_impact_zoom": True,
    "enable_hook_banner": True,
    "enable_preview_flash": True,
    "enable_infinite_loop": True,
    "enable_cta_comment": True,
    "enable_impact_sfx": True,
    "enable_smart_pacing": True,
    "enable_progress_bar": True,
    "enable_audio_norm": True,
    "enable_action_tracking": True,
    "enable_slowmo": True,
    "youtube_client_secrets_path": str(DATA_DIR / "client_secrets.json"),
    "port": 8765,
}


def _atomic_write_config(path: Path, data: dict[str, Any]) -> None:
    raw = json.dumps(data, indent=2, ensure_ascii=False)
    if path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(path)


def load_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    # Load settings.json first, then overlay config.json if user edited config.json directly
    for cfg_path in (SETTINGS_FILE, CONFIG_FILE):
        if cfg_path.exists():
            try:
                saved = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    if (saved.get("default_clip_min_sec"), saved.get("default_clip_max_sec")) in ((12, 45), (22, 38)):
                        saved["default_clip_min_sec"] = 25
                        saved["default_clip_max_sec"] = 55
                        saved["default_clip_target_sec"] = 38
                    settings.update(saved)
            except Exception:
                pass
    # Clamp critical P0 safety ranges
    settings["tiktok_long_min_sec"] = max(61, min(85, int(settings.get("tiktok_long_min_sec", 61))))
    settings["tiktok_long_max_sec"] = max(settings["tiktok_long_min_sec"] + 5, min(120, int(settings.get("tiktok_long_max_sec", 90))))
    settings["peak_hook_duration_sec"] = round(max(1.5, min(2.0, float(settings.get("peak_hook_duration_sec", 1.8)))), 2)
    settings["hook_text_duration_sec"] = round(max(2.0, min(2.5, float(settings.get("hook_text_duration_sec", 2.3)))), 2)
    settings["impact_zoom_intensity"] = round(max(1.06, min(1.25, float(settings.get("impact_zoom_intensity", 1.14)))), 2)
    settings["zoom_min_interval_sec"] = round(max(6.0, float(settings.get("zoom_min_interval_sec", 6.0))), 2)
    settings["zoom_max_per_clip"] = max(1, min(3, int(settings.get("zoom_max_per_clip", 3))))
    settings["cpu_pause_threshold_pct"] = max(65, min(95, int(settings.get("cpu_pause_threshold_pct", 80))))
    settings["youtube_daily_upload_limit"] = max(1, min(10, int(settings.get("youtube_daily_upload_limit", 2))))

    if not settings.get("ffmpeg_path"):
        ff = discover_ffmpeg()
        if ff["ok"]:
            settings["ffmpeg_path"] = ff["ffmpeg"]
    if not CONFIG_FILE.exists():
        try:
            _atomic_write_config(CONFIG_FILE, settings)
        except OSError:
            pass
    return settings


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    for key, val in updates.items():
        if key in DEFAULT_SETTINGS:
            current[key] = val
    out_dir = Path(current.get("output_dir") or str(OUTPUT_DIR))
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_config(SETTINGS_FILE, current)
    _atomic_write_config(CONFIG_FILE, current)
    return load_settings()


def get_ffmpeg_bins() -> tuple[str, str]:
    settings = load_settings()
    custom_ff = settings.get("ffmpeg_path", "").strip()
    if custom_ff and _safe_exists(Path(custom_ff)):
        ff_path = Path(custom_ff)
        probe_candidate = ff_path.parent / "ffprobe.exe"
        return str(ff_path), str(probe_candidate) if _safe_exists(probe_candidate) else (shutil.which("ffprobe") or "ffprobe")
    discovered = discover_ffmpeg()
    return discovered["ffmpeg"], discovered["ffprobe"]


def get_output_dir() -> Path:
    settings = load_settings()
    p = Path(settings.get("output_dir") or str(OUTPUT_DIR))
    p.mkdir(parents=True, exist_ok=True)
    return p


# Calibrated PS4/PS5 Game Profiles
GAME_PROFILES: dict[str, dict[str, Any]] = {
    "sniper_elite": {
        "id": "sniper_elite",
        "name": "Sniper Elite / Tiro Tatico",
        "short_name": "Sniper Elite",
        "accent": "#F59E0B",
        "description": "Prioriza tiros de longa distancia, camera de raio-X, explosoes de veiculos e mortes furtivas.",
        "keywords": ["sniper", "elite", "ghost", "recon", "hitman", "metal gear", "tatico", "headshot"],
        "weights": {
            "audio_peak": 0.36,
            "audio_rms": 0.14,
            "motion": 0.18,
            "scene_cut": 0.14,
            "red_flash": 0.12,
            "death_signal": 0.06,
        },
        "pre_pad": 8.0,
        "post_pad": 7.0,
        "death_pre_pad": 9.0,
        "death_post_pad": 8.0,
        "min_gap": 24.0,
        "hashtags": ["#SniperElite", "#Headshot", "#RaioX", "#PS4Gameplay", "#GamingBrasil", "#Shorts"],
        "vision_focus": "tiros de sniper a longa distancia, camera de raio-X mostrando ossos/orgaos, explosoes de veiculos, headshots e mortes furtivas",
    },
    "spider_man": {
        "id": "spider_man",
        "name": "Spider-Man / Acao & Acrobacias",
        "short_name": "Spider-Man",
        "accent": "#EF4444",
        "description": "Prioriza balancos em alta velocidade, combos aereos, finalizacoes rapidas e cenas cinematograficas.",
        "keywords": ["spider", "homem aranha", "miles", "morales", "batman", "arkham", "uncharted", "infamous"],
        "weights": {
            "audio_peak": 0.22,
            "audio_rms": 0.18,
            "motion": 0.34,
            "scene_cut": 0.14,
            "red_flash": 0.06,
            "death_signal": 0.06,
        },
        "pre_pad": 8.0,
        "post_pad": 7.0,
        "death_pre_pad": 9.0,
        "death_post_pad": 8.0,
        "min_gap": 24.0,
        "hashtags": ["#SpiderManPS4", "#HomemAranha", "#MarvelsSpiderMan", "#PS4", "#Combos", "#GamingBR"],
        "vision_focus": "balancos em alta velocidade pela cidade, combos aereos, finalizacoes acrobaticas, lutas contra viloes e cenas cinematograficas",
    },
    "rdr2": {
        "id": "rdr2",
        "name": "Red Dead Redemption 2 / Mundo Aberto",
        "short_name": "Red Dead 2",
        "accent": "#DC2626",
        "description": "Prioriza duelos, Dead Eye, tiroteios intensos, eventos aleatorios e quedas/mortes engracadas.",
        "keywords": ["red dead", "rdr", "rdr2", "arthur", "morgan", "gta", "western", "cowboy", "cyberpunk"],
        "weights": {
            "audio_peak": 0.30,
            "audio_rms": 0.16,
            "motion": 0.20,
            "scene_cut": 0.12,
            "red_flash": 0.10,
            "death_signal": 0.12,
        },
        "pre_pad": 8.0,
        "post_pad": 7.0,
        "death_pre_pad": 9.5,
        "death_post_pad": 8.0,
        "min_gap": 24.0,
        "hashtags": ["#RedDeadRedemption2", "#RDR2", "#ArthurMorgan", "#DeadEye", "#PS4Gameplay", "#Shorts"],
        "vision_focus": "duelos de velho oeste, modo Dead Eye com filtro ambar/sepia, tiroteios intensos, eventos aleatorios de mundo aberto e quedas ou mortes engracadas/brutais",
    },
    "resident_evil": {
        "id": "resident_evil",
        "name": "Resident Evil / Terror & Sobrevivencia",
        "short_name": "Resident Evil",
        "accent": "#10B981",
        "description": "Prioriza sustos (jump scares), encontros com monstros/chefes, pouca vida/tensao e mortes brutais (serra eletrica, agarramentos).",
        "keywords": ["resident", "re2", "re3", "re4", "re7", "re8", "village", "biohazard", "last of us", "tlou", "outlast", "silent hill", "terror", "horror"],
        "weights": {
            "audio_peak": 0.30,
            "audio_rms": 0.12,
            "motion": 0.16,
            "scene_cut": 0.12,
            "red_flash": 0.14,
            "death_signal": 0.16,
        },
        "pre_pad": 8.0,
        "post_pad": 7.5,
        "death_pre_pad": 9.5,
        "death_post_pad": 8.5,
        "min_gap": 24.0,
        "hashtags": ["#ResidentEvil", "#RE4Remake", "#Terror", "#SurvivalHorror", "#JumpScare", "#PS4Gameplay"],
        "vision_focus": "sustos repentinos (jump scares), encontros com monstros ou chefes, tensao de pouca vida, ataques de serra eletrica, agarramentos e mortes cinematograficas com tela Voce Morreu / Game Over",
    },
    "god_of_war": {
        "id": "god_of_war",
        "name": "God of War / Hack & Slash",
        "short_name": "God of War",
        "accent": "#38BDF8",
        "description": "Prioriza execucoes brutais (R3), ativacao de Furia, combos com Machado/Laminas e lutas contra Valkyrias/Chefes.",
        "keywords": ["god of war", "gow", "kratos", "ragnarok", "valkyrie", "devil may cry", "elden", "souls", "bloodborne", "sekiro", "ghost of tsushima"],
        "weights": {
            "audio_peak": 0.28,
            "audio_rms": 0.20,
            "motion": 0.24,
            "scene_cut": 0.10,
            "red_flash": 0.10,
            "death_signal": 0.08,
        },
        "pre_pad": 8.0,
        "post_pad": 7.0,
        "death_pre_pad": 9.0,
        "death_post_pad": 8.0,
        "min_gap": 24.0,
        "hashtags": ["#GodOfWar", "#Kratos", "#PS4Gameplay", "#BossFight", "#R3Execution", "#GamingBR"],
        "vision_focus": "execucoes brutais com botao R3, ativacao da Furia Espartana com brilho vermelho/fogo, combos com Machado Leviata ou Laminas do Caos e lutas epicas contra Valkyrias ou chefes",
    },
    "generico": {
        "id": "generico",
        "name": "Generico / Qualquer Jogo",
        "short_name": "Universal",
        "accent": "#6366F1",
        "description": "Perfil universal hibrido calibrado para qualquer jogo de PS4, PS5 ou PC.",
        "keywords": [],
        "weights": {
            "audio_peak": 0.28,
            "audio_rms": 0.18,
            "motion": 0.24,
            "scene_cut": 0.12,
            "red_flash": 0.10,
            "death_signal": 0.08,
        },
        "pre_pad": 8.0,
        "post_pad": 7.0,
        "death_pre_pad": 9.0,
        "death_post_pad": 8.0,
        "min_gap": 24.0,
        "hashtags": ["#GameplayPS4", "#GamingBrasil", "#MelhoresMomentos", "#ClipesDeJogos", "#Shorts", "#TikTokGaming"],
        "vision_focus": "combate intenso, explosoes, combos, chefes, sustos, jogadas epicas e mortes cinematograficas ou engracadas com tela de Game Over",
    },
}


def detect_game_profile_from_name(filename: str) -> str:
    lower = (filename or "").lower()
    for profile_id, profile in GAME_PROFILES.items():
        if profile_id == "generico":
            continue
        for kw in profile["keywords"]:
            if kw in lower:
                return profile_id
    return "generico"
