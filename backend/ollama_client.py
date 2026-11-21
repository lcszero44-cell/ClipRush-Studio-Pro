"""Ollama / Qwen 3.5 9B client for multimodal gameplay inspection, automatic game/scene
detection, open-loop curiosity hooks (max 6 words), comment CTA triggers,
creative metadata regeneration, and YouTube long-form SEO."""

from __future__ import annotations

import base64
import json
import random
import re
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.config import GAME_PROFILES, load_settings


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


def _ollama_generate(
    prompt: str,
    image_paths: Optional[list[str]] = None,
    timeout: float = 35.0,
    temperature: float = 0.45,
) -> Optional[str]:
    settings = load_settings()
    base_url = settings["ollama_url"].rstrip("/")
    model = settings["ollama_model"]
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": 480,
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
        with httpx.Client(timeout=timeout) as client:
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


def analyze_moment_with_qwen(
    frame_paths: list[str],
    profile_id: str,
    heuristic_category: str,
    heuristic_score: float,
    signals_summary: str,
    video_name: str,
) -> dict[str, Any]:
    """Use Qwen 3.5 9B Vision to auto-detect game + scene, write open-loop hook (max 6 words),
    final 2s comment trigger question (cta_comment), and full viral kit."""
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    fallback = _fallback_moment_kit(profile, heuristic_category, video_name)

    status = check_ollama_status(timeout=1.2)
    if not status["online"] or not status["model_ready"]:
        return fallback

    prompt = (
        f"Voce e estrategista de retencao para YouTube Shorts, TikTok e Reels focado em gameplay de console.\n"
        f"Perfil selecionado: {profile['name']} (arquivo: {video_name}).\n"
        f"Foco: {profile['vision_focus']}.\n"
        f"Sinais detectados ({signals_summary}): categoria preliminar '{heuristic_category}'.\n"
        "Olhe os frames enviados, identifique AUTOMATICAMENTE o jogo e o que aconteceu, e responda APENAS em JSON valido:\n"
        "{\n"
        '  "detected_game": "Nome do jogo identificado",\n'
        '  "is_menu_or_boring": false,\n'
        '  "is_player_death": false,\n'
        '  "category": "combate_intenso | morte_cinematografica | susto_tensao | acrobacia_cinema",\n'
        '  "score_boost": 0,\n'
        '  "hook_2s": "GANCHO DE CURIOSIDADE ABERTA EM MAIUSCULAS (MAXIMO 6 PALAVRAS)",\n'
        '  "cta_comment": "Pergunta curta para badge dos 2s finais (ex: Voce escaparia dessa? | Nota de 0 a 10?)",\n'
        '  "title": "Titulo viral curto e altamente clicavel em portugues",\n'
        '  "description": "Descricao persuasiva com pergunta no final para gerar comentarios",\n'
        '  "hashtags": ["#Jogo", "#Gameplay", "#Shorts", "#PS4", "#GamingBrasil"],\n'
        '  "reason": "1 frase curta explicando o que aconteceu na cena"\n'
        "}\n"
        "Regras: hook_2s DEVE ter no maximo 6 palavras criando curiosidade aberta (ex: OLHA O FINAL DISSO...). "
        "cta_comment DEVE ser uma pergunta curtissima (max 5 palavras) para estimular comentarios nos 2s finais. "
        "Se mostrar menu do PS4, pausa, inventario parado ou loading, marque is_menu_or_boring=true. "
        "Se mostrar morte brutal do jogador, execucao ou Game Over / Voce Morreu, marque is_player_death=true."
    )

    raw = _ollama_generate(prompt, image_paths=frame_paths, timeout=28.0, temperature=0.45)
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
    hashtags = _normalize_hashtags(parsed.get("hashtags"), profile["hashtags"], detected_game)
    try:
        boost = max(-25, min(20, int(parsed.get("score_boost", 0))))
    except Exception:
        boost = 0

    return {
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


def regenerate_viral_kit(
    frame_paths: list[str],
    profile_id: str,
    category: str,
    video_name: str,
    previous_title: str = "",
    previous_hook: str = "",
    variation_index: int = 1,
) -> dict[str, Any]:
    """Generate a brand-new creative variation of hook_2s (max 6 words), cta_comment,
    title, description, and hashtags."""
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    fallback = _fallback_variation_kit(
        profile=profile,
        category=category,
        video_name=video_name,
        previous_title=previous_title,
        variation_index=variation_index,
    )

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
        f"Analise o frame da cena (jogo/arquivo: {video_name} | perfil: {profile['name']} | tipo: {category}).\n"
        f"IMPORTANTE: Crie uma NOVA variacao criativa completamente diferente da anterior.\n"
        f"Titulo anterior (NAO repetir): \"{previous_title}\"\n"
        f"Gancho anterior (NAO repetir): \"{previous_hook}\"\n"
        f"Estilo desta nova variacao: {chosen_angle}.\n\n"
        "Responda APENAS em JSON valido:\n"
        "{\n"
        '  "detected_game": "Nome do jogo identificado na cena",\n'
        '  "hook_2s": "NOVO GANCHO DE CURIOSIDADE ABERTA (MAXIMO 6 PALAVRAS)",\n'
        '  "cta_comment": "Nova pergunta curta de comentario (max 5 palavras, ex: Nota de 0 a 10?)",\n'
        '  "title": "Novo titulo viral curto e altamente clicavel em portugues",\n'
        '  "description": "Nova descricao persuasiva terminando com uma pergunta forte para gerar comentarios",\n'
        '  "hashtags": ["#TagDoJogo", "#TagDaCena", "#Gameplay", "#Shorts", "#Reels", "#TikTokGaming"]\n'
        "}"
    )

    raw = _ollama_generate(prompt, image_paths=frame_paths, timeout=30.0, temperature=0.85)
    parsed = _extract_json_block(raw or "")
    if not parsed:
        return fallback

    detected_game = str(parsed.get("detected_game") or profile["short_name"]).strip()[:60]
    new_hook = _clamp_words(str(parsed.get("hook_2s") or fallback["hook_2s"]).upper(), max_words=6, max_chars=38)
    new_cta = _clamp_words(str(parsed.get("cta_comment") or fallback["cta_comment"]), max_words=6, max_chars=34)
    new_title = str(parsed.get("title") or fallback["title"]).strip()[:95]
    new_desc = str(parsed.get("description") or fallback["description"]).strip()[:320]
    new_tags = _normalize_hashtags(parsed.get("hashtags"), profile["hashtags"], detected_game)

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
    game_short = profile["short_name"]
    presets = {
        "morte_cinematografica": {
            "hook_2s": "OLHA ESSE FINAL BRUTAL...",
            "cta_comment": "Voce escaparia dessa?",
            "title": f"A morte mais brutal que ja levei no {game_short}!",
            "description": (
                f"Estava tudo sob controle no {game_short} ate esse segundo final... "
                "Voce ja morreu de um jeito pior que esse? Comenta ai!"
            ),
            "reason": "Finalizacao brutal / Game Over detectado com contexto completo preservado.",
        },
        "susto_tensao": {
            "hook_2s": "NAO PISCA NESSE SEGUNDO...",
            "cta_comment": "Tomaria esse susto? 0 a 10?",
            "title": f"Esse susto no {game_short} quase me derrubou da cadeira",
            "description": (
                f"Tensao no limite jogando {game_short}! "
                "De 0 a 10, quanto voce tomaria esse susto? Comenta abaixo!"
            ),
            "reason": "Pico subito de audio e tensao visual detectados no momento.",
        },
        "acrobacia_cinema": {
            "hook_2s": "ESSE LANCE PARECEU FILME...",
            "cta_comment": "Nota de 0 a 10?",
            "title": f"Jogada digna de filme no {game_short}",
            "description": (
                f"Essa sequencia no {game_short} saiu mais cinematografica que cutscene! "
                "Qual nota voce da pra esse lance? Comenta ai!"
            ),
            "reason": "Alta velocidade de movimento e fluidez cinematografica detectadas.",
        },
        "combate_intenso": {
            "hook_2s": "ESPERA ATE O ULTIMO IMPACTO...",
            "cta_comment": "Voce sobreviveria a isso?",
            "title": f"Combate insano no {game_short} — ele nao esperava por isso!",
            "description": (
                f"Acao pura no {game_short} sem cortes! "
                "Voce conseguiria sobreviver a essa luta? Deixa nos comentarios!"
            ),
            "reason": "Combate de alta intensidade com picos de explosao, disparos e movimento rapido.",
        },
    }
    chosen = presets.get(category, presets["combate_intenso"])
    return {
        "detected_game": game_short,
        "is_menu_or_boring": False,
        "is_player_death": category == "morte_cinematografica",
        "category": category,
        "score_boost": 0,
        "hook_2s": chosen["hook_2s"],
        "cta_comment": chosen["cta_comment"],
        "title": chosen["title"],
        "description": chosen["description"],
        "hashtags": profile["hashtags"],
        "reason": chosen["reason"],
        "ai_powered": False,
    }


def _fallback_variation_kit(
    profile: dict[str, Any],
    category: str,
    video_name: str,
    previous_title: str,
    variation_index: int,
) -> dict[str, Any]:
    game_short = profile["short_name"]
    pools = {
        "morte_cinematografica": [
            (
                "ATE O CONTROLE TREMEU NESSA",
                "Voce daria rage quit?",
                f"Ninguem merece morrer assim no {game_short}...",
                f"Quando achei que tinha escapado no {game_short}, veio o golpe final! Voce dava rage quit ou tentava de novo? Comenta ai!",
            ),
            (
                "ESSE FINAL ME PEGOU DESPREVENIDO",
                "Qual morte foi pior?",
                f"O Game Over mais cinematografico do {game_short}!",
                f"Essa animacao de finalizacao no {game_short} e insana demais! Qual morte te marcou mais nesse jogo?",
            ),
            (
                "NAO PISCA NO FINAL...",
                "Voce escaparia dessa?",
                f"Fui completamente destruido nessa cena de {game_short}",
                f"Pensei que dava pra tankar no {game_short}, mas o inimigo nao perdoou! Ja passou por isso?",
            ),
        ],
        "susto_tensao": [
            (
                "SUSTO QUE TRAVA A ALMA",
                "Teria coragem de jogar?",
                f"Quase joguei o controle longe nessa parte de {game_short}!",
                f"Jogar {game_short} de fone no maximo e pedir pra perder anos de vida... Voce teria coragem? Comenta!",
            ),
            (
                "OLHA O QUE VEIO DO NADA",
                "Nota desse susto: 0 a 10?",
                f"Tensao 1000% no {game_short} — impossivel nao tomar susto",
                f"Esse momento do {game_short} pegou todo mundo desprevenido! De 0 a 10, qual foi o nivel desse susto?",
            ),
        ],
        "combate_intenso": [
            (
                "ESSE GOLPE FOI CIRURGICO...",
                "Nota de 0 a 10?",
                f"Jogada absurda no {game_short} que pareceu roteirizada!",
                f"Quando tudo encaixa perfeitamente no {game_short}, o resultado e esse! Qual nota de 0 a 10 voce da?",
            ),
            (
                "NINGUEM ESPERAVA ESSA VIRADA",
                "Voce faria melhor?",
                f"Sobrevivi por um fio nessa batalha insana de {game_short}",
                f"Com pouquissima margem de erro no {game_short}, consegui virar o jogo! Voce faria diferente? Comenta ai!",
            ),
            (
                "ESPERA O IMPACTO FINAL...",
                "Qual seu momento favorito?",
                f"O momento mais satisfatorio que gravei no {game_short}!",
                f"Adrenalina pura do inicio ao fim no {game_short}! Voce tambem ama cenas assim nos games?",
            ),
        ],
    }
    pool = pools.get(category, pools["combate_intenso"])
    candidates = [item for item in pool if item[2].lower() != previous_title.strip().lower()]
    pick = candidates[variation_index % len(candidates)] if candidates else pool[variation_index % len(pool)]
    extra_tags = ["#ClipesVirais", "#MelhoresMomentos", "#ReelsGaming", "#Shorts"]
    merged_tags = list(dict.fromkeys(profile["hashtags"][:4] + extra_tags[:3]))
    return {
        "detected_game": game_short,
        "hook_2s": _clamp_words(pick[0], max_words=6, max_chars=38),
        "cta_comment": pick[1],
        "title": pick[2],
        "description": pick[3],
        "hashtags": merged_tags,
        "ai_powered": False,
        "variation_index": variation_index,
    }


def generate_youtube_long_kit(
    video_name: str,
    profile_id: str,
    chapters: list[dict[str, Any]],
    total_duration_str: str,
) -> dict[str, Any]:
    """Generate 3 CTR titles, full SEO description, tags, and formatted timestamps for Mode 2 (16:9)."""
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
    raw = _ollama_generate(prompt, timeout=30.0)
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
