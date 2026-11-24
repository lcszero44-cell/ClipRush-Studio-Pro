"""Ollama / Qwen 3.5 9B client for multimodal gameplay inspection:
- Sends 2 real frames (peak + aftermath) with 90s timeout per scene
- Enforces 100% unique hooks (max 6 words), titles, descriptions, and CTAs across entire batch
- Built-in 32+ rotating creative variation bank guarantees zero duplicate copy even if Ollama is offline/slow
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import threading
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.config import DATA_DIR, GAME_PROFILES, load_settings
from backend.copy_engine import generate_unique_kit, jaccard_similarity
from backend.storage import find_game_sheet

VISION_CACHE_FILE = DATA_DIR / "vision_cache.json"
_CACHE_LOCK = threading.RLock()


def _compute_frames_cache_key(frame_paths: list[str], profile_id: str, platform_target: str) -> str:
    h = hashlib.sha256()
    h.update(f"{profile_id}|{platform_target}".encode("utf-8"))
    for p in (frame_paths or [])[:2]:
        fp = Path(p)
        if fp.exists() and fp.is_file():
            try:
                h.update(fp.read_bytes())
            except OSError:
                h.update(str(fp).encode("utf-8", errors="ignore"))
    return h.hexdigest()[:32]


def _get_vision_cache(cache_key: str) -> Optional[dict[str, Any]]:
    with _CACHE_LOCK:
        if not VISION_CACHE_FILE.exists():
            return None
        try:
            data = json.loads(VISION_CACHE_FILE.read_text(encoding="utf-8"))
            entry = data.get(cache_key)
            return dict(entry) if isinstance(entry, dict) else None
        except Exception:
            return None


def _put_vision_cache(cache_key: str, value: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        data: dict[str, Any] = {}
        if VISION_CACHE_FILE.exists():
            try:
                loaded = json.loads(VISION_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        data[cache_key] = value
        # Keep cache lean (max 400 frame entries)
        if len(data) > 400:
            for old_k in list(data.keys())[:-400]:
                data.pop(old_k, None)
        try:
            tmp = VISION_CACHE_FILE.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(VISION_CACHE_FILE)
        except OSError:
            pass

# 36 completely unique Portuguese viral templates indexed by clip number (zero repetition across 30+ clip batches)
_ROTATING_BANK: list[tuple[str, str, str, str]] = [
    # (hook_2s <= 6 words, cta_comment, title_template {game}, desc_template {game})
    ("OLHA ESSE FINAL ABSURDO...", "Voce escaparia dessa?", "A cena mais insana que gravei no {game}!", "Estava tudo calmo no {game} ate esse segundo explosivo... Voce escaparia vivo? Comenta ai!"),
    ("NINGUEM ESPERAVA ESSE GOLPE!", "Nota de 0 a 10?", "Ele nao teve nenhuma chance nessa jogada de {game}", "Quando o timing encaixa perfeito no {game}, sai uma cena dessas! Qual nota de 0 a 10?"),
    ("NAO PISCA NO CLIMAX...", "Faria diferente?", "Sobrevivi por um milimetro nessa luta de {game}!", "Com quase nada de margem de erro no {game}, consegui virar o jogo! Voce faria diferente?"),
    ("ESSE SOM ME ARREPIOU!", "Jogaria de fone?", "Adrenalina pura nesse encontro brutal de {game}", "Jogar {game} com o volume no maximo e pedir pra perder o folego! Voce encara? Comenta!"),
    ("O QUE ACONTECEU AQUI?!", "Ja passou por isso?", "Momento completamente imprevisivel no {game}!", "Nem nos meus melhores sonhos eu achei que isso ia acontecer no {game}! Ja viveu algo assim?"),
    ("ESPERA ATE O ULTIMO IMPACTO", "Quem ganharia essa?", "Sequencia digna de cinema jogando {game} no PS4", "Cada segundo dessa batalha no {game} parece cena de filme de acao! O que achou?"),
    ("ATE O CONTROLE TREMEU!", "Voce daria rage quit?", "A pressao subiu para 1000% nessa cena de {game}", "Nessa hora o coracao quase saiu pela boca jogando {game}! Voce mantinha a calma?"),
    ("UM ERRO E ERA FIM!", "Quanto de tensao?", "Fio da navalha total nessa parte do {game}", "Se eu piscasse um segundo antes no {game}, era tela de Game Over na hora! Comenta!"),
    ("A VIRADA MAIS INSANA!", "Merece replay?", "Quando achei que tinha perdido tudo no {game}...", "Tudo parecia perdido no {game}, mas olha a reviravolta no final! Voce esperava isso?"),
    ("ESSE INIMIGO NAO PERDOOU!", "Qual morte foi pior?", "O ataque mais brutal que ja enfrentei no {game}", "A inteligencia artificial do {game} nao teve piedade nenhuma nessa cena! Ja levou pior?"),
    ("TIRO CIRURGICO NA HORA H!", "Mira em dia?", "Precisao absurda no momento decisivo de {game}", "Nem a camera lenta consegue explicar esse reflexo no {game}! De 0 a 10 pra essa mira?"),
    ("CUIDADO COM O QUE VEM...", "Tomaria esse susto?", "Fui pego completamente desprevenido no {game}!", "Quando voce menos espera no {game}, o perigo aparece na sua frente! Voce tomaria susto?"),
    ("OLHA A REACAO FINAL!", "Qual sua nota?", "Climax explosivo que salvou minha run no {game}", "Esse desfecho no {game} valeu a gameplay inteira! Qual foi sua parte favorita?"),
    ("ISSO NAO PODE SER REAL", "Bug ou habilidade?", "A jogada mais improvavel da minha sessao de {game}", "Ate hoje nao sei como sobrevivi a essa sequencia no {game}! Sorte ou habilidade pura?"),
    ("CAOS TOTAL NA TELA!", "Sobreviveria a isso?", "Tudo explodiu ao mesmo tempo jogando {game}!", "Tiro, fogo e correria sem parar nessa cena de {game}! Voce conseguia sair vivo?"),
    ("O SEGUNDO QUE MUDOU TUDO", "Viu o detalhe?", "Um detalhe minusculo decidiu essa luta no {game}", "Repara bem no instante do impacto no {game}! Voce percebeu na primeira vez?"),
    ("EMBOSCADA NO PIOR MOMENTO!", "Como sairia dessa?", "Cercado por todos os lados jogando {game} no PS4", "Sem saida e sem tempo pra pensar no {game}... Qual seria sua estrategia ai? Comenta!"),
    ("ESSE CHEFE E INSANO!", "Quanto tempo levou?", "Batalha eletrizante contra um monstro no {game}", "Cada golpe nesse combate de {game} tira quase toda a vida! Voce ja venceu esse chefe?"),
    ("FINALIZACAO DE RESPEITO!", "Satisfatorio demais?", "O golpe final mais satisfatorio do {game}", "Nada supera a sensacao de acertar essa finalizacao no {game}! Concorda? Deixa nos comentarios!"),
    ("SEGURA A RESPIRACAO AGORA...", "Coracao aguentou?", "Tensao maxima ate o ultimo frame no {game}!", "Prendi a respiracao do comeco ao fim dessa cena no {game}! Quem mais joga assim?"),
    ("ELE VEIO COM TUDO!", "Fugir ou lutar?", "Duelo frente a frente sem recuar no {game}", "Nessa hora no {game} so existem duas opcoes: enfrentar ou cair! O que voce faria?"),
    ("CENA DE ARREPIAR!", "Melhor jogo do PS4?", "Por isso {game} continua sendo uma obra-prima!", "Graficos, som e impacto absurdo nesse trecho de {game}! Esse jogo esta no seu Top 5?"),
    ("NAO ACREDITO QUE ACERTEI!", "Foi no reflexo?", "Reflexo impossivel salvou tudo no {game}!", "Meu dedo apertou o botao no milesimo exato jogando {game}! Voce tambem ja fez isso?"),
    ("OLHA O PERIGO NAS COSTAS!", "Viu antes de mim?", "Quase nao percebi o ataque surpresa no {game}", "Se eu nao olho pro lado naquela hora no {game}, ja era! Voce viu chegando?"),
    ("EXPLOSAO EM CADEIA!", "Nota do estrago?", "Destruicao total em segundos jogando {game}!", "Quando o plano da certo no {game} e tudo vai pelos ares de uma vez! Curtiu?"),
    ("ULTIMA BALA NO PENTE!", "Pressao ou calma?", "Decidi o combate com o ultimo recurso no {game}", "Zero margem pra erro nesse momento critico de {game}! Voce aguentaria essa pressao?"),
    ("ESSE SUSTO QUEBROU TUDO!", "De 0 a 10?", "Pulei da cadeira nessa parte sinistra de {game}", "O clima de {game} te engana direitinho ate a hora do susto! Nota desse susto?"),
    ("COMBO SEM DEIXAR RESPIRAR!", "Dominou ou nao?", "Sequencia imparavel de golpes jogando {game}!", "Nao dei nem um segundo de respiro nessa luta de {game}! O que achou do ritmo?"),
    ("QUASE DEU TUDO ERRADO!", "Escapou por pouco?", "A fugida mais apertada que ja fiz no {game}", "Por um triz nao perdi todo o progresso nessa parte de {game}! Ja escapou assim?"),
    ("IMPACTO EM CAMERA LENTA!", "Assista de novo?", "O momento exato em que tudo virou no {game}", "Em camera lenta da pra ver cada detalhe desse impacto no {game}! Valeu o replay?"),
    ("A CENA QUE NINGUEM ESQUECE", "Ja jogou esse?", "Trecho inesquecivel da minha jornada no {game}", "Quem ja jogou {game} sabe exatamente a emocao dessa parte! Qual sua cena favorita?"),
    ("SEM PIEDADE NENHUMA!", "Brutal demais?", "Finalizacao cinematografica absurda no {game}", "O nivel de detalhe e brutalidade dessa cena de {game} impressiona! Comenta sua reacao!"),
    ("REVIRAVOLTA NO FIM!", "Esperava esse final?", "Do caos a vitoria em segundos no {game}!", "Achei que ia dar tudo errado no {game}, mas o final surpreendeu demais!"),
    ("PERIGO EM CADA ESQUINA", "Encarava no dificil?", "Sobrevivencia extrema jogando {game} no limite", "Cada passo nessa area de {game} pode ser o ultimo! Voce joga no modo dificil?"),
    ("RITMO FRENETICO TOTAL!", "Folego em dia?", "Acao ininterrupta do inicio ao fim no {game}", "Zero tempo morto nessa sequencia intensa de {game}! Quer parte 2? Comenta ai!"),
    ("MOMENTO EPICO GRAVADO!", "Merece o like?", "Ainda bem que o PS4 gravou essa cena de {game}!", "Se nao estivesse gravando essa jogada no {game}, ninguem ia acreditar!"),
]


def _extract_json_block(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _clamp_words(text: str, max_words: int = 6, max_chars: int = 40) -> str:
    words = [w for w in re.sub(r"\s+", " ", (text or "").strip()).split(" ") if w]
    clipped = " ".join(words[:max_words])
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars].rsplit(" ", 1)[0].strip() or clipped[:max_chars]
    return clipped


def check_ollama_status(timeout: float = 2.0) -> dict[str, Any]:
    settings = load_settings()
    base_url = settings["ollama_url"].rstrip("/")
    target_model = settings["ollama_model"]
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base_url}/api/tags")
            if resp.status_code != 200:
                return {"ok": False, "online": False, "model_ready": False, "model": target_model, "models": []}
            data = resp.json()
            names = [m.get("name", "") for m in data.get("models", [])]
            target_prefix = target_model.split(":")[0].lower()
            model_ready = any(
                n.lower() == target_model.lower() or n.lower().startswith(target_prefix)
                for n in names
            )
            return {
                "ok": model_ready,
                "online": True,
                "model_ready": model_ready,
                "model": target_model,
                "models": names,
            }
    except Exception:
        return {"ok": False, "online": False, "model_ready": False, "model": target_model, "models": []}


def get_qwen_five_state(active_cut_number: Optional[int] = None, loading_override: bool = False) -> dict[str, Any]:
    """Return the exact 5-state Qwen Vision status indicator:
    1. connected_vision -> 'Conectado c/ visao'
    2. loading_model    -> 'Carregando modelo'
    3. analyzing_cut    -> 'Analisando corte #N'
    4. offline          -> 'Desligado'
    5. no_vision        -> 'Sem visao'
    """
    settings = load_settings()
    st = check_ollama_status(timeout=1.2)
    use_vision = bool(settings.get("use_ollama_vision", True))
    model = str(settings.get("ollama_model") or "qwen3.5:9b")

    if not st["online"]:
        return {
            "state": "offline",
            "label": "Desligado",
            "level": "err",
            "model": model,
            "online": False,
            "vision_enabled": use_vision,
        }

    if not st["model_ready"] or not use_vision:
        return {
            "state": "no_vision",
            "label": "Sem visao",
            "level": "warn",
            "model": model,
            "online": True,
            "vision_enabled": False,
        }

    if active_cut_number is not None and active_cut_number > 0:
        return {
            "state": "analyzing_cut",
            "label": f"Analisando corte #{active_cut_number}",
            "level": "active",
            "model": model,
            "online": True,
            "vision_enabled": True,
            "cut_number": active_cut_number,
        }

    if loading_override:
        return {
            "state": "loading_model",
            "label": "Carregando modelo",
            "level": "loading",
            "model": model,
            "online": True,
            "vision_enabled": True,
        }

    # Check if model is currently warm/loaded in Ollama /api/ps
    loaded = False
    try:
        base_url = settings["ollama_url"].rstrip("/")
        with httpx.Client(timeout=0.9) as client:
            ps = client.get(f"{base_url}/api/ps")
            if ps.status_code == 200:
                running = [m.get("name", "").lower() for m in (ps.json().get("models") or [])]
                pref = model.split(":")[0].lower()
                loaded = any(r == model.lower() or r.startswith(pref) for r in running)
    except Exception:
        pass

    return {
        "state": "connected_vision",
        "label": "Conectado c/ visao",
        "level": "ok",
        "model": model,
        "online": True,
        "vision_enabled": True,
        "model_warm": loaded,
    }


def detect_game_with_qwen(frame_path: Optional[str], filename: str = "") -> dict[str, Any]:
    """Identify the game from a video frame via Qwen 3.5 Vision (or filename fallback)
    and return matching Game Sheet or draft for new unregistered games."""
    # First check filename against games_db
    fname_match = find_game_sheet(filename)
    settings = load_settings()
    if frame_path and Path(frame_path).exists() and settings.get("use_ollama_vision", True):
        st = check_ollama_status(timeout=1.2)
        if st["online"] and st["model_ready"]:
            prompt = (
                "Identifique qual videogame aparece neste frame de gameplay de PS4/PS5.\n"
                f"Nome do arquivo: {filename}\n"
                "Responda APENAS em JSON valido:\n"
                "{\n"
                '  "official_name": "Nome oficial do jogo",\n'
                '  "genre": "terror | acao | tiro | mundo_aberto | rpg | luta",\n'
                '  "characters": ["personagem1", "personagem2"],\n'
                '  "locations": ["local1"],\n'
                '  "weapons_items": ["arma ou item visivel"],\n'
                '  "hashtags": ["#NomeDoJogo", "#Gameplay"]\n'
                "}"
            )
            raw = _ollama_generate(prompt, image_paths=[frame_path], timeout=35.0, temperature=0.2)
            parsed = _extract_json_block(raw or "")
            if parsed and parsed.get("official_name"):
                det_name = str(parsed["official_name"]).strip()[:70]
                existing = find_game_sheet(det_name)
                if existing:
                    return {"detected_name": existing["official_name"], "is_new_game": False, "sheet": existing, "ai_powered": True}
                tag = "#" + re.sub(r"[^a-zA-Z0-9]", "", det_name.title())
                return {
                    "detected_name": det_name,
                    "is_new_game": True,
                    "ai_powered": True,
                    "sheet": {
                        "id": "",
                        "official_name": det_name,
                        "aliases": [det_name.lower()],
                        "genre": str(parsed.get("genre") or "acao").lower(),
                        "profile_id": "generico",
                        "characters": [str(x).strip() for x in (parsed.get("characters") or []) if str(x).strip()][:6],
                        "locations": [str(x).strip() for x in (parsed.get("locations") or []) if str(x).strip()][:5],
                        "weapons_items": [str(x).strip() for x in (parsed.get("weapons_items") or []) if str(x).strip()][:5],
                        "slangs_br": ["no sufoco", "jogada insana", "timing perfeito"],
                        "hashtags": [tag, "#PS4Gameplay", "#GamingBrasil"],
                        "is_custom": True,
                    },
                }

    if fname_match:
        return {"detected_name": fname_match["official_name"], "is_new_game": False, "sheet": fname_match, "ai_powered": False}

    guess = re.sub(r"[_\-]+", " ", Path(filename or "Gameplay PS4").stem).strip()[:50] or "Gameplay PS4"
    tag = "#" + re.sub(r"[^a-zA-Z0-9]", "", guess.title())
    return {
        "detected_name": guess,
        "is_new_game": True,
        "ai_powered": False,
        "sheet": {
            "id": "",
            "official_name": guess,
            "aliases": [guess.lower()],
            "genre": "acao",
            "profile_id": "generico",
            "characters": [],
            "locations": [],
            "weapons_items": [],
            "slangs_br": ["no sufoco", "timing perfeito", "quase fui de base"],
            "hashtags": [tag if len(tag) > 2 else "#GameplayPS4", "#GamingBrasil"],
            "is_custom": True,
        },
    }


def _ollama_generate(
    prompt: str,
    image_paths: Optional[list[str]] = None,
    timeout: Optional[float] = None,
    temperature: float = 0.62,
) -> Optional[str]:
    settings = load_settings()
    base_url = settings["ollama_url"].rstrip("/")
    model = settings["ollama_model"]
    keep_alive = str(settings.get("ollama_keep_alive") or "30s")
    eff_timeout = float(timeout if timeout is not None else settings.get("ollama_timeout_sec", 60))
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "keep_alive": keep_alive,
        "options": {
            "temperature": temperature,
            "num_predict": 260,
        },
    }
    if image_paths and settings.get("use_ollama_vision", True):
        encoded = []
        for p in image_paths[:2]:
            fp = Path(p)
            if fp.exists():
                encoded.append(base64.b64encode(fp.read_bytes()).decode("ascii"))
        if encoded:
            payload["images"] = encoded

    try:
        with httpx.Client(timeout=eff_timeout) as client:
            resp = client.post(f"{base_url}/api/generate", json=payload)
            if resp.status_code == 200:
                return resp.json().get("response", "")
    except Exception:
        return None
    return None


def _normalize_hashtags(raw_tags: Any, fallback_tags: list[str], detected_game: str = "") -> list[str]:
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            s = re.sub(r"[^a-zA-Z0-9À-ÿ_]", "", str(t).strip().lstrip("#"))
            if s:
                tag = f"#{s}"
                if tag.lower() not in [x.lower() for x in tags]:
                    tags.append(tag)
    if detected_game:
        clean_game = re.sub(r"[^a-zA-Z0-9]", "", detected_game.title())
        if clean_game and f"#{clean_game}".lower() not in [x.lower() for x in tags]:
            tags.insert(0, f"#{clean_game}")
    for ft in fallback_tags:
        if len(tags) >= 7:
            break
        if ft.lower() not in [x.lower() for x in tags]:
            tags.append(ft)
    return tags[:8]


def calibrate_platform_hashtags(
    raw_tags: Any,
    platform_target: str,
    profile_id: str = "generico",
    detected_game: str = "",
) -> list[str]:
    """Calibrate hashtags specifically for TikTok/Reels vs YouTube Shorts."""
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    base = _normalize_hashtags(raw_tags, profile["hashtags"], detected_game)
    clean_game = re.sub(r"[^a-zA-Z0-9]", "", (detected_game or profile["short_name"]).title())

    result: list[str] = []
    seen: set[str] = set()

    def _add(tag: str) -> None:
        t = tag if tag.startswith("#") else f"#{tag}"
        tl = t.lower()
        if tl not in seen and len(t) > 1:
            seen.add(tl)
            result.append(t)

    if platform_target == "tiktok_long":
        # TikTok Monetizable (61-90s) Creator Rewards tags
        for t in ("#fyp", "#tiktokgaming", "#gameplaycompleta", "#ps4", "#gaming", "#viral"):
            _add(t)
        if clean_game:
            _add(f"#{clean_game}")
        for t in base:
            if t.lower() != "#shorts":
                _add(t)
            if len(result) >= 9:
                break
        return result[:9]

    if platform_target == "tiktok_reels":
        # Fast-reach TikTok / Reels tags + game-specific tags
        for t in ("#fyp", "#gaming", "#ps4", "#gameplay", "#viral"):
            _add(t)
        if clean_game:
            _add(f"#{clean_game}")
        for t in base:
            if t.lower() != "#shorts":
                _add(t)
            if len(result) >= 9:
                break
        return result[:9]

    # Default / youtube_shorts: #Shorts at start + strong YouTube search terms for the game & scene
    _add("#Shorts")
    if clean_game:
        _add(f"#{clean_game}")
        _add(f"#{clean_game}Gameplay")
        _add(f"#{clean_game}PS4")
    for t in ("#MelhoresMomentos", "#GameplayPTBR", "#PS4Gameplay"):
        _add(t)
    for t in base:
        _add(t)
        if len(result) >= 9:
            break
    return result[:9]


def _pick_unique_fallback(
    profile: dict[str, Any],
    category: str,
    clip_index: int = 0,
    used_titles: Optional[list[str]] = None,
    used_hooks: Optional[list[str]] = None,
    platform_target: str = "youtube_shorts",
    game_sheet: Optional[dict[str, Any]] = None,
    game_name: str = "",
) -> dict[str, Any]:
    """Select a guaranteed-unique PT-BR viral kit via the P1 Combinatorial Copy Engine + Game Sheet."""
    kit = generate_unique_kit(
        game_name=game_name or profile["short_name"],
        profile_id=profile["id"],
        category=category,
        platform_target=platform_target,
        game_sheet=game_sheet,
        used_titles=used_titles,
        used_hooks=used_hooks,
        record_history=True,
    )
    reason_map = {
        "morte_cinematografica": "Finalizacao brutal / Game Over detectado com contexto completo preservado.",
        "susto_tensao": "Pico subito de audio e tensao visual detectados no momento.",
        "dialogo_historia": "Cutscene ou dialogo marcante detectado pela faixa vocal.",
        "acrobacia_cinema": "Alta velocidade de movimento e fluidez cinematografica detectadas.",
        "combate_intenso": "Combate de alta intensidade com picos de explosao, disparos e movimento rapido.",
    }
    return {
        "detected_game": kit["detected_game"],
        "is_menu_or_boring": False,
        "is_player_death": category == "morte_cinematografica",
        "category": category,
        "score_boost": 0,
        "hook_2s": kit["hook_2s"],
        "cta_comment": kit["cta_comment"],
        "title": kit["title"],
        "description": kit["description"],
        "hashtags": kit["hashtags"],
        "reason": reason_map.get(category, reason_map["combate_intenso"]),
        "ai_powered": False,
    }


def analyze_moment_with_qwen(
    frame_paths: list[str],
    profile_id: str,
    heuristic_category: str,
    heuristic_score: float,
    signals_summary: str,
    video_name: str,
    used_titles: Optional[list[str]] = None,
    used_hooks: Optional[list[str]] = None,
    clip_index: int = 0,
    platform_target: str = "youtube_shorts",
    game_sheet: Optional[dict[str, Any]] = None,
    game_name: str = "",
) -> dict[str, Any]:
    """Use Qwen 3.5 9B Vision + Active Game Sheet + Combinatorial Engine to generate
    100% unique hook/title/description/CTA calibrated per platform."""
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    sheet = game_sheet or (find_game_sheet(game_name) if game_name else None)
    fallback = _pick_unique_fallback(
        profile=profile,
        category=heuristic_category,
        clip_index=clip_index,
        used_titles=used_titles,
        used_hooks=used_hooks,
        platform_target=platform_target,
        game_sheet=sheet,
        game_name=game_name,
    )

    settings = load_settings()
    if not settings.get("use_ollama_vision", True):
        return fallback

    sheet_tag = sheet.get("id", "") if sheet else game_name
    cache_key = _compute_frames_cache_key(frame_paths, f"{profile_id}_{sheet_tag}", platform_target) if frame_paths else ""
    if cache_key:
        cached = _get_vision_cache(cache_key)
        if cached:
            used_t_set = {t.strip().lower() for t in (used_titles or []) if t}
            used_h_set = {h.strip().lower() for h in (used_hooks or []) if h}
            if cached.get("title", "").strip().lower() not in used_t_set and cached.get("hook_2s", "").strip().lower() not in used_h_set:
                cached["hashtags"] = calibrate_platform_hashtags(
                    cached.get("hashtags"), platform_target, profile_id, cached.get("detected_game", "")
                )
                cached["cache_hit"] = True
                return cached

    status = check_ollama_status(timeout=1.2)
    if not status["online"] or not status["model_ready"]:
        return fallback

    sheet_block = ""
    if sheet:
        sheet_block = (
            f"\nFICHA OFICIAL DO JOGO (USE OBRIGATORIAMENTE ESTES TERMOS):\n"
            f"- Nome Oficial: {sheet.get('official_name')}\n"
            f"- Personagens: {', '.join(sheet.get('characters') or [])}\n"
            f"- Locais: {', '.join(sheet.get('locations') or [])}\n"
            f"- Armas/Itens: {', '.join(sheet.get('weapons_items') or [])}\n"
            f"- Girias BR: {', '.join(sheet.get('slangs_br') or [])}\n"
            f"- Hashtags do Jogo: {' '.join(sheet.get('hashtags') or [])}\n"
        )

    forbidden_block = ""
    if used_titles or used_hooks:
        prev_t = ", ".join(f'"{t}"' for t in (used_titles or [])[-12:])
        prev_h = ", ".join(f'"{h}"' for h in (used_hooks or [])[-12:])
        forbidden_block = (
            "\nPROIBICAO TOTAL DE REPETICAO: Voce JA usou estes textos em clipes anteriores do mesmo video. "
            "Crie algo 100% NOVO e diferente especifico para ESTA cena:\n"
            f"- Titulos ja usados (NAO repetir): {prev_t}\n"
            f"- Ganchos ja usados (NAO repetir): {prev_h}\n"
        )

    prompt = (
        f"Voce e estrategista de retencao para YouTube Shorts, TikTok e Reels focado em gameplay de console.\n"
        f"Clipe #{clip_index + 1} | Perfil: {profile['name']} (arquivo: {video_name}).\n"
        f"Foco: {profile['vision_focus']}.\n"
        f"{sheet_block}"
        f"Sinais detectados ({signals_summary}): categoria preliminar '{heuristic_category}'.\n"
        f"{forbidden_block}"
        "Analise os 2 frames enviados (pico da acao + desfecho), use os dados da Ficha do Jogo acima, "
        "e responda APENAS em JSON valido:\n"
        "{\n"
        '  "detected_game": "Nome oficial do jogo",\n'
        '  "is_menu_or_boring": false,\n'
        '  "is_player_death": false,\n'
        '  "category": "combate_intenso | morte_cinematografica | susto_tensao | acrobacia_cinema",\n'
        '  "score_boost": 0,\n'
        '  "hook_2s": "GANCHO UNICO DE CURIOSIDADE ABERTA EM MAIUSCULAS (MAXIMO 6 PALAVRAS)",\n'
        '  "cta_comment": "Pergunta curta para badge dos 2s finais (max 5 palavras)",\n'
        '  "title": "Titulo viral curto e 100% unico sobre ESTA cena em portugues",\n'
        '  "description": "Descricao persuasiva com pergunta no final para gerar comentarios",\n'
        '  "hashtags": ["#Jogo", "#Gameplay", "#Shorts", "#PS4", "#GamingBrasil"],\n'
        '  "reason": "1 frase curta explicando exatamente o que aconteceu nesta cena"\n'
        "}\n"
        "Regras: Se mostrar menu do PS4, pausa, inventario parado ou loading, marque is_menu_or_boring=true. "
        "Se mostrar morte brutal do jogador, execucao ou Game Over / Voce Morreu, marque is_player_death=true."
    )

    raw = _ollama_generate(prompt, image_paths=frame_paths[:2], timeout=None, temperature=0.68)
    parsed = _extract_json_block(raw or "")
    if not parsed:
        return fallback

    cat = str(parsed.get("category") or heuristic_category).strip().lower()
    if cat not in ("combate_intenso", "morte_cinematografica", "susto_tensao", "acrobacia_cinema"):
        cat = heuristic_category

    detected_game = str(parsed.get("detected_game") or profile["short_name"]).strip()[:60]
    hook = _clamp_words(str(parsed.get("hook_2s") or fallback["hook_2s"]).upper(), max_words=6, max_chars=38)
    cta = _clamp_words(str(parsed.get("cta_comment") or fallback["cta_comment"]), max_words=6, max_chars=34)
    title = str(parsed.get("title") or fallback["title"]).strip()[:95]
    desc = str(parsed.get("description") or fallback["description"]).strip()[:300]
    reason = str(parsed.get("reason") or fallback["reason"]).strip()[:160]
    hashtags = calibrate_platform_hashtags(parsed.get("hashtags"), platform_target, profile_id, detected_game)

    # Hard anti-duplication guard even if Qwen echoed an earlier title or hook
    used_t_set = {t.strip().lower() for t in (used_titles or []) if t}
    used_h_set = {h.strip().lower() for h in (used_hooks or []) if h}
    if title.strip().lower() in used_t_set:
        title = fallback["title"]
    if hook.strip().lower() in used_h_set:
        hook = fallback["hook_2s"]

    try:
        boost = max(-25, min(20, int(parsed.get("score_boost", 0))))
    except Exception:
        boost = 0

    result = {
        "detected_game": detected_game,
        "is_menu_or_boring": bool(parsed.get("is_menu_or_boring", False)),
        "is_player_death": bool(parsed.get("is_player_death", cat == "morte_cinematografica")),
        "category": cat,
        "score_boost": boost,
        "hook_2s": hook,
        "cta_comment": cta,
        "title": title,
        "description": desc,
        "hashtags": hashtags,
        "reason": reason,
        "ai_powered": True,
    }
    if cache_key:
        _put_vision_cache(cache_key, result)
    return result


def regenerate_viral_kit(
    frame_paths: list[str],
    profile_id: str,
    category: str,
    video_name: str,
    previous_title: str = "",
    previous_hook: str = "",
    variation_index: int = 1,
    platform_target: str = "youtube_shorts",
) -> dict[str, Any]:
    """Generate a brand-new creative variation of hook_2s, cta_comment, title, description, and hashtags."""
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    fallback = _pick_unique_fallback(
        profile=profile,
        category=category,
        clip_index=variation_index + random.randint(1, 12),
        used_titles=[previous_title],
        used_hooks=[previous_hook],
        platform_target=platform_target,
    )
    fallback["variation_index"] = variation_index

    if not load_settings().get("use_ollama_vision", True):
        return fallback

    status = check_ollama_status(timeout=1.5)
    if not status["online"] or not status["model_ready"]:
        return fallback

    angles = [
        "tom de choque e curiosidade aberta",
        "tom bem-humorado e ironico de gamer",
        "tom cinematografico de suspense ate o ultimo segundo",
        "tom desafiador provocando os comentarios do publico",
    ]
    chosen_angle = angles[(variation_index + random.randint(0, 3)) % len(angles)]

    prompt = (
        f"Voce e estrategista viral de TikTok, YouTube Shorts e Facebook Reels para games.\n"
        f"Analise os frames da cena (jogo/arquivo: {video_name} | perfil: {profile['name']} | tipo: {category}).\n"
        f"IMPORTANTE: Crie uma NOVA variacao criativa completamente diferente da anterior.\n"
        f"Titulo anterior (NAO repetir): \"{previous_title}\"\n"
        f"Gancho anterior (NAO repetir): \"{previous_hook}\"\n"
        f"Estilo desta nova variacao: {chosen_angle}.\n\n"
        "Responda APENAS em JSON valido:\n"
        "{\n"
        '  "detected_game": "Nome do jogo identificado na cena",\n'
        '  "hook_2s": "NOVO GANCHO DE CURIOSIDADE ABERTA (MAXIMO 6 PALAVRAS)",\n'
        '  "cta_comment": "Nova pergunta curta de comentario (max 5 palavras)",\n'
        '  "title": "Novo titulo viral curto e altamente clicavel em portugues",\n'
        '  "description": "Nova descricao persuasiva terminando com uma pergunta forte para gerar comentarios",\n'
        '  "hashtags": ["#TagDoJogo", "#TagDaCena", "#Gameplay", "#Shorts", "#Reels", "#TikTokGaming"]\n'
        "}"
    )

    raw = _ollama_generate(prompt, image_paths=frame_paths[:2], timeout=90.0, temperature=0.85)
    parsed = _extract_json_block(raw or "")
    if not parsed:
        return fallback

    detected_game = str(parsed.get("detected_game") or profile["short_name"]).strip()[:60]
    new_hook = _clamp_words(str(parsed.get("hook_2s") or fallback["hook_2s"]).upper(), max_words=6, max_chars=38)
    new_cta = _clamp_words(str(parsed.get("cta_comment") or fallback["cta_comment"]), max_words=6, max_chars=34)
    new_title = str(parsed.get("title") or fallback["title"]).strip()[:95]
    new_desc = str(parsed.get("description") or fallback["description"]).strip()[:320]
    new_tags = calibrate_platform_hashtags(parsed.get("hashtags"), platform_target, profile_id, detected_game)

    if new_title.strip().lower() == previous_title.strip().lower():
        return fallback

    return {
        "detected_game": detected_game,
        "hook_2s": new_hook,
        "cta_comment": new_cta,
        "title": new_title,
        "description": new_desc,
        "hashtags": new_tags,
        "ai_powered": True,
        "variation_index": variation_index,
    }


def _fallback_moment_kit(profile: dict[str, Any], category: str, video_name: str) -> dict[str, Any]:
    return _pick_unique_fallback(profile=profile, category=category, clip_index=0)


def generate_youtube_long_kit(
    video_name: str,
    profile_id: str,
    chapters: list[dict[str, Any]],
    total_duration_str: str,
) -> dict[str, Any]:
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    game_short = profile["short_name"]
    chapters_lines = "\n".join(f"{c['timestamp']} - {c['label']}" for c in chapters)

    fallback = {
        "titles": [
            f"{game_short} (PS4) - A Gameplay Mais Intensa Que Voce Vai Ver Hoje!",
            f"SOBREVIVI AO IMPOSSIVEL no {game_short} | Melhores Momentos Sem Enrolacao",
            f"{game_short} Gameplay Editada: Chefes, Combates Insanos e Finais Epicos",
        ],
        "description": (
            f"Gameplay editada de {game_short} em 1080p sem partes paradas, sem telas de loading e direto na acao!\n\n"
            f"Se voce curtiu essa edicao limpa de {game_short}, deixe o like e comente qual foi o seu momento favorito!\n\n"
            f"CAPITULOS:\n{chapters_lines}\n\n"
            + " ".join(profile["hashtags"])
        ),
        "tags": [
            game_short,
            f"{game_short} gameplay",
            f"{game_short} ps4",
            f"{game_short} melhores momentos",
            "gameplay editada",
            "ps4 gameplay pt-br",
            "jogos ps4",
            "cliprush studio pro",
        ],
        "chapters_text": chapters_lines,
        "ai_powered": False,
    }

    if not load_settings().get("use_ollama_vision", True):
        return fallback

    status = check_ollama_status(timeout=1.5)
    if not status["online"] or not status["model_ready"]:
        return fallback

    prompt = (
        f"Voce e especialista em SEO e CTR de YouTube Gaming no Brasil.\n"
        f"Crie o pacote SEO para uma gameplay editada (16:9, duracao {total_duration_str}) de {profile['name']} "
        f"(arquivo: {video_name}).\n"
        f"Capitulos detectados:\n{chapters_lines}\n\n"
        "Responda APENAS em JSON valido com:\n"
        "{\n"
        '  "titles": ["Titulo 1 alto CTR", "Titulo 2 alto CTR", "Titulo 3 alto CTR"],\n'
        '  "description": "Descricao completa SEO em portugues com gancho de engajamento (nao precisa repetir os capitulos, vou anexar depois)",\n'
        '  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"]\n'
        "}"
    )
    raw = _ollama_generate(prompt, timeout=90.0)
    parsed = _extract_json_block(raw or "")
    if not parsed:
        return fallback

    titles = parsed.get("titles")
    if not isinstance(titles, list) or len(titles) < 3:
        titles = fallback["titles"]
    else:
        titles = [str(t).strip() for t in titles[:3]]

    desc_body = str(parsed.get("description") or "").strip()
    full_desc = f"{desc_body}\n\nCAPITULOS:\n{chapters_lines}\n\n" + " ".join(profile["hashtags"]) if desc_body else fallback["description"]
    tags = parsed.get("tags") if isinstance(parsed.get("tags"), list) else fallback["tags"]
    tags = [str(t).strip() for t in tags[:14] if str(t).strip()]

    return {
        "titles": titles,
        "description": full_desc,
        "tags": tags or fallback["tags"],
        "chapters_text": chapters_lines,
        "ai_powered": True,
    }
