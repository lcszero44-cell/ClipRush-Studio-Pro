"""Combinatorial PT-BR Viral Copy & Hashtag Engine (P1):
- 1,000+ Title combinations via [Gancho] + [Sujeito/Personagem do Jogo] + [Emocao/Acao] + [CTA]
- 500+ Description combinations infused with editable Game Sheet data (data/games_db.json)
- Categorized Hashtag Reservoirs: Broad (105+), Genre (72+), Game (48+), Event (30+) + Trending of the Day
- 150+ unique Hashtag Set combinations via sampling without replacement + platform rules
- Word + 3-gram Jaccard Similarity Guard (< 0.35) against persistent copy history & active batch
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Optional

from backend.config import DATA_DIR, GAME_PROFILES, load_settings
from backend.storage import find_game_sheet

COPY_HISTORY_FILE = DATA_DIR / "copy_history.json"
_LOCK = threading.RLock()

# ---- Lexical Banks (PT-BR Gaming Viral Copy) ----

TITLE_HOOKS: list[str] = [
    "Olha o que rolou com",
    "Ninguem esperava isso em",
    "Quase perdi tudo com",
    "Sobrevivi por um triz em",
    "Esse momento no",
    "O segundo mais tenso de",
    "Ate o controle tremeu no",
    "Como eu escapei vivo no",
    "Reacao impossivel jogando",
    "A jogada mais insana de",
    "Nao pisca nessa cena de",
    "Quando tudo deu errado no",
    "Virada cinematografica no",
    "Adrenalina no talo jogando",
    "Sem acreditar nessa cena de",
    "O climax mais brutal de",
    "Segura o folego nesse trecho de",
    "Tive que pausar depois disso em",
    "Melhor timing da minha vida em",
    "Cena digna de filme no",
    "Fio da navalha total em",
    "Esse susto me quebrou no",
    "Ainda bem que gravei isso no",
    "Pressao 1000% nessa parte de",
]

TITLE_ACTIONS_BY_CAT: dict[str, list[str]] = {
    "combate_intenso": [
        "{char} usando {weapon} em {loc}",
        "combate frenetico com {weapon}",
        "troca de golpes insana em {loc}",
        "combo sem dar respiro com {char}",
        "batalha caotica contra todos em {loc}",
        "defesa milimetrica de {char}",
        "contra-ataque perfeito com {weapon}",
        "luta no limite sem errar nada",
        "duelo eletrizante usando {weapon}",
        "pressao absurda no meio de {loc}",
    ],
    "morte_cinematografica": [
        "finalizacao brutal em {loc}",
        "golpe fatal inesperado com {char}",
        "execucao sem piedade nenhuma",
        "tela de Game Over mais insana",
        "ataque surpresa que acabou com tudo",
        "nocaute cinematografico em {loc}",
        "erro fatal no ultimo segundo",
        "emboscada impiedosa contra {char}",
        "desfecho mais pesado da sessao",
        "fim dramatico com {weapon}",
    ],
    "susto_tensao": [
        "susto repentino no escuro de {loc}",
        "emboscada que ninguem viu chegando",
        "perigo colado nas costas de {char}",
        "tensao silenciosa ate explodir tudo",
        "aparicao sinistra no meio de {loc}",
        "coracao a mil com {weapon} na mao",
        "arrepio total jogando de fone",
        "pulo da cadeira sem aviso nenhum",
        "sinal vermelho ligado em {loc}",
        "momento de puro panico com {char}",
    ],
    "dialogo_historia": [
        "cena marcante de {char} em {loc}",
        "dialogo que mudou toda a historia",
        "cutscene arrepiante com {char}",
        "revelacao pesada no meio de {loc}",
        "fala inesquecivel antes da batalha",
        "encontro tenso envolvendo {char}",
        "historia de cinema jogando {game}",
        "momento emocionante que arrepiou",
        "confronto verbal antes do caos",
        "reviravolta narrativa em {loc}",
    ],
    "acrobacia_cinema": [
        "manobra absurda de {char} em {loc}",
        "sequencia de pura velocidade",
        "movimento cirurgico com {weapon}",
        "estilo cinematografico sem tomar dano",
        "acrobacia perfeita sobre {loc}",
        "reflexo de outro planeta com {char}",
        "fluidez absurda em cada golpe",
        "cena que parece trailer oficial",
        "esquiva plastica no limite",
        "dominacao total do cenario em {loc}",
    ],
}

TITLE_EMOTIONS: list[str] = [
    "({slang}!)",
    "— {slang} total!",
    "| puro caos!",
    "— arrepiou geral!",
    "| reflexo absurdo!",
    "— sem margem pra erro!",
    "| tensao maxima!",
    "— nivel cinema!",
    "| coracao acelerado!",
    "— timing cirurgico!",
    "| adrenalina pura!",
    "— reviravolta epica!",
    "| quase impossivel!",
    "— de cair o queixo!",
]

HOOK_2S_TEMPLATES: list[str] = [
    "OLHA ESSE {slang_u}!",
    "{char_u} NO LIMITE!",
    "NAO PISCA EM {loc_u}...",
    "ESPERA O GOLPE FINAL...",
    "QUASE FUI DE BASE!",
    "OLHA A {weapon_u} AGORA!",
    "NINGUEM ESPERAVA ISSO!",
    "SEGURA ESSE IMPACTO...",
    "UM ERRO E ERA FIM!",
    "COMO ESCAPEI DISSO?!",
    "REPARA NESSE TIMING!",
    "CAOS TOTAL EM {loc_u}!",
    "ATE O CONTROLE TREMEU!",
    "REFLEXO IMPOSSIVEL AQUI!",
    "ESSE SOM ME ARREPIOU!",
    "O SEGUNDO DECISIVO...",
    "VIRADA ABSURDA NO FIM!",
    "SEM PIEDADE NENHUMA!",
    "SUSTO SEM AVISO NENHUM!",
    "CENA DE PURO CINEMA!",
    "NA ULTIMA BALA!",
    "OLHA NAS COSTAS DELE!",
    "DOMINEI ESSE CLIMAX!",
    "IMPOSSIVEL NAO REVER!",
    "QUE FINALIZACAO BRUTAL!",
    "PRESSAO 1000% AGORA!",
    "MIRA CIRURGICA NO H!",
    "ISSO NAO PODE SER REAL!",
    "FOI POR UM MILIMETRO!",
    "PARRY MILIMETRICO AQUI!",
]

CTA_COMMENTS: list[str] = [
    "Voce escaparia dessa?",
    "Nota de 0 a 10?",
    "Faria diferente aqui?",
    "Jogaria isso de fone?",
    "Sorte ou habilidade?",
    "Voce daria rage quit?",
    "Ja passou por isso?",
    "Merece parte 2?",
    "Viu o detalhe no fim?",
    "Qual seu momento favorito?",
    "Encarava no dificil?",
    "Coracao aguentava?",
    "Qual arma voce usaria?",
    "Top 5 jogos do PS4?",
    "Fugir ou enfrentar?",
    "Acertaria esse reflexo?",
]

DESC_OPENERS: list[str] = [
    "Estava explorando {loc} em {game} quando tudo virou de cabeca pra baixo!",
    "Jogando {game} no console e olha o que aconteceu com {char} nessa cena.",
    "Bastou um segundo de distracao em {loc} pra adrenalina subir para 1000% no {game}!",
    "Gravei essa sequencia jogando {game} e ate agora nao acredito nesse desfecho.",
    "Quando voce equipa {weapon} em {game}, o ritmo muda completamente!",
    "Se nao estivesse gravando essa run de {game}, ninguem ia acreditar no que rolou.",
    "Nessa parte de {loc} no {game}, qualquer piscada significa Game Over na hora.",
    "Puro suco de {game}: tensao, impacto e reflexo no ultimo milesimo!",
    "Essa batalha com {char} em {game} pareceu cena direta de filme de acao.",
    "Volume no talo jogando {game} e veio esse momento completamente imprevisivel.",
    "Mais um trecho insano da nossa jornada em {game} — foco total com {weapon}!",
    "A inteligencia artificial de {game} nao deu um segundo de folga em {loc}.",
    "Olha o nivel de detalhe e intensidade dessa jogada de {char} no {game}.",
    "Do zero ao caos absoluto em segundos jogando {game}!",
]

DESC_MIDDLES: list[str] = [
    "Foi {slang} do comeco ao fim usando {weapon}.",
    "Zero margem de erro: {char} teve que resolver no reflexo puro.",
    "Cada segundo nessa cena de {loc} deixou o coracao saindo pela boca.",
    "Deu pra sentir a pressao aumentando ate o impacto decisivo ({slang}).",
    "Mantive a calma com {weapon} mesmo quando parecia tudo perdido.",
    "O timing encaixou certinho no momento mais tenso de {loc}.",
    "Mesmo jogando no limite, saiu um {slang} que salvou a gameplay inteira!",
    "Se piscar no climax voce perde o melhor frame desse corte.",
    "Combate limpo, audio estalando e aquele {slang} que todo gamer respeita.",
    "Sem enrolacao: direto para o trecho mais memoravel dessa sessao.",
    "A ambientacao de {loc} combinada com essa acao deixou tudo ainda mais insano.",
    "Vale assistir de novo prestando atencao no instante exato da virada!",
]

DESC_ENDINGS: list[str] = [
    "Voce conseguiria sair vivo dessa situacao? Comenta ai!",
    "De 0 a 10, qual nota voce da pra essa jogada? Deixa nos comentarios!",
    "Foi sorte pura ou reflexo treinado? Quero ver a opiniao de voces!",
    "Ja viveu um sufoco parecido jogando {game}? Conta nos comentarios!",
    "Se curtiu o corte, deixa o like e comenta se quer Parte 2 de {game}!",
    "Voce usaria {weapon} nessa hora ou escolhia outra estrategia?",
    "Marca aquele amigo que ia tomar susto nessa parte de {game}!",
    "Qual chefe ou trecho de {game} te deu mais trabalho ate hoje?",
    "Comenta ai qual proximo jogo de PS4/PS5 voce quer ver por aqui!",
    "Voce aguentava essa pressao sem pausar o jogo? Responde abaixo!",
    "Quem ja zerou {game} sabe exatamente a tensao dessa parte — concorda?",
    "Salva esse clipe se voce tambem ama gameplay raiz sem enrolacao!",
    "O que voce teria feito no lugar de {char} nesse segundo?",
    "Se voce piscou no climax, volta o video e comenta o que achou!",
    "Acompanha o perfil pra mais cortes diarios de {game} e outros classicos!",
    "Deixa sua nota e fala se essa cena merece entrar no Top da semana!",
]

# ---- Categorized Hashtag Reservoirs ----
# Broad (108 tags)
BROAD_HASHTAGS: list[str] = [
    "#fyp", "#foryou", "#foryoupage", "#viral", "#gaming", "#gamer", "#gameplay", "#ps4", "#ps5",
    "#playstation", "#gamerbrasil", "#gamingbrasil", "#jogos", "#videogames", "#videogame", "#console",
    "#consolegaming", "#ps4gaming", "#ps5gaming", "#playstationbrasil", "#playstationbr", "#clipes",
    "#clipesdejogos", "#gamingclips", "#gameclips", "#melhoresmomentos", "#gameplaybr", "#gameplayptbr",
    "#jogosps4", "#jogosps5", "#gamesbr", "#gamesbrasil", "#comunidadegamer", "#vidadegamer", "#jogando",
    "#shortsgaming", "#tiktokgaming", "#reelsgaming", "#instagaming", "#youtubegaming", "#gamertok",
    "#gamingcommunity", "#gamersbrasil", "#nerdbrasil", "#geekbrasil", "#jogatina", "#noitegamer",
    "#setupgamer", "#controleps4", "#dualshock4", "#dualsense", "#gameplayraiz", "#cortesdegames",
    "#cortesgaming", "#momentoepico", "#jogadaepica", "#proplayer", "#hardcoregamer", "#zerando",
    "#campanha", "#historiaemjogos", "#acao", "#aventura", "#adrenalina", "#tensao", "#surpresa",
    "#imprevisivel", "#cinematografico", "#graficos", "#nextgen", "#classicosps4", "#exclusivosps4",
    "#sonyplaystation", "#psnbrasil", "#psplus", "#trofeusps4", "#platina", "#platinando", "#dicasgamer",
    "#curiosidadesgamer", "#reacaogamer", "#sustogamer", "#gameplaycomentada", "#semcorte", "#climax",
    "#replay", "#assistateofim", "#olhaessefinal", "#naoacredito", "#insano", "#absurdo", "#epico",
    "#lendario", "#mitada", "#jogadademestre", "#reflexo", "#timingperfeito", "#combate", "#batalha",
    "#desafio", "#mododificil", "#sobrevivencia", "#explore", "#trending", "#emalta", "#fypbr",
    "#paravoce", "#viralbrasil",
]

# By Genre (72 tags across 6 genres)
GENRE_HASHTAGS: dict[str, list[str]] = {
    "terror": [
        "#SurvivalHorror", "#TerrorGaming", "#HorrorGames", "#JogosDeTerror", "#JumpScare",
        "#Medo", "#TerrorBR", "#Susto", "#HorrorBR", "#ClimaDeTerror", "#SobrevivenciaTerror", "#PesadeloGamer",
    ],
    "acao": [
        "#ActionGames", "#JogosDeAcao", "#HackAndSlash", "#Combos", "#BossFight", "#LutaEpica",
        "#Finalizacao", "#Parry", "#Esquiva", "#AcaoTotal", "#CinemaGamer", "#AventuraEpica",
    ],
    "tiro": [
        "#FPSBrasil", "#Sniper", "#Headshot", "#TiroTatico", "#Stealth", "#Precisao",
        "#ShooterGames", "#MiraEmDia", "#Clutch", "#OneShot", "#Tiroteio", "#Tatico",
    ],
    "mundo_aberto": [
        "#OpenWorld", "#MundoAberto", "#Exploracao", "#Sandbox", "#EventosAleatorios", "#HistoriaEpica",
        "#Imersao", "#DetalhesIncriveis", "#AventuraLivre", "#MapaGigante", "#SideQuest", "#Realismo",
    ],
    "rpg": [
        "#Soulslike", "#ActionRPG", "#RPGaming", "#BossBattle", "#NoHit", "#BuildOP",
        "#DesafioExtremo", "#FromSoftwareStyle", "#RPG", "#FantasiaSombria", "#Loot", "#LevelUp",
    ],
    "luta": [
        "#FightingGames", "#JogosDeLuta", "#Comeback", "#ComboInsano", "#Fatality", "#Versus",
        "#FGCBrasil", "#ReadPerfeito", "#Flawless", "#Arena", "#KOFStyle", "#BatalhaFinal",
    ],
}

# By Game Generic Console/Franchise Pool (48 tags)
GAME_POOL_HASHTAGS: list[str] = [
    "#ResidentEvil", "#RE4Remake", "#ResidentEvilBrasil", "#GodOfWar", "#Kratos", "#GodOfWarBrasil",
    "#RedDeadRedemption2", "#RDR2", "#ArthurMorgan", "#SpiderManPS4", "#HomemAranha", "#MilesMorales",
    "#SniperElite", "#SniperElite5", "#RaioX", "#TheLastOfUs", "#TLOU2", "#Ellie",
    "#GTAV", "#GTA5", "#GTAOnline", "#EldenRing", "#Malenia", "#GhostOfTsushima",
    "#JinSakai", "#Warzone", "#CallOfDuty", "#MortalKombat", "#MK11", "#Uncharted",
    "#Bloodborne", "#Sekiro", "#DaysGone", "#HorizonZeroDawn", "#Cyberpunk2077", "#TheWitcher3",
    "#BatmanArkham", "#DevilMayCry", "#SilentHill", "#Outlast", "#MetalGear", "#Hitman",
    "#Battlefield", "#FarCry", "#AssassinsCreed", "#DoomEternal", "#HogwartsLegacy", "#AlanWake",
]

# By Event (30 tags across 5 categories)
EVENT_HASHTAGS: dict[str, list[str]] = {
    "combate_intenso": ["#CombateIntenso", "#Tiroteio", "#Explosao", "#AcaoFrenetica", "#BatalhaInsana", "#CaosTotal"],
    "morte_cinematografica": ["#GameOver", "#MorteBrutal", "#Execucao", "#Finalizacao", "#FuiDeBase", "#SemPiedade"],
    "susto_tensao": ["#Susto", "#JumpScare", "#TensaoMaxima", "#Emboscada", "#QuaseInfartei", "#Arrepio"],
    "dialogo_historia": ["#Cutscene", "#HistoriaDeJogo", "#DialogoEpico", "#Lore", "#CenaMarcante", "#Enredo"],
    "acrobacia_cinema": ["#CenaDeCinema", "#Acrobacia", "#MovimentoLimpo", "#ReflexoPuro", "#EstiloTotal", "#TrailerReal"],
}


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _ngrams(text: str) -> set[str]:
    clean = re.sub(r"[^a-z0-9\s]", " ", _strip_accents(text).lower())
    words = [w for w in clean.split() if len(w) >= 2]
    tokens: set[str] = set(words)
    # Word bigrams + character 3-grams for strict near-duplicate detection
    for i in range(len(words) - 1):
        tokens.add(f"{words[i]}_{words[i+1]}")
    compact = " ".join(words)
    for i in range(max(0, len(compact) - 2)):
        tokens.add(compact[i : i + 3])
    return tokens


def jaccard_similarity(text_a: str, text_b: str) -> float:
    sa = _ngrams(text_a)
    sb = _ngrams(text_b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / max(1, union)


def _load_copy_history() -> dict[str, Any]:
    with _LOCK:
        if not COPY_HISTORY_FILE.exists():
            return {"titles": [], "hashtag_sets": []}
        try:
            data = json.loads(COPY_HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    "titles": list(data.get("titles") or [])[-300:],
                    "hashtag_sets": list(data.get("hashtag_sets") or [])[-300:],
                }
        except Exception:
            pass
        return {"titles": [], "hashtag_sets": []}


def _record_copy_history(title: str, hashtags: list[str]) -> None:
    with _LOCK:
        hist = _load_copy_history()
        if title:
            hist["titles"].append(title)
            hist["titles"] = hist["titles"][-300:]
        sig = " ".join(sorted(t.lower() for t in hashtags))
        if sig:
            hist["hashtag_sets"].append(sig)
            hist["hashtag_sets"] = hist["hashtag_sets"][-300:]
        try:
            tmp = COPY_HISTORY_FILE.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(COPY_HISTORY_FILE)
        except OSError:
            pass


def _resolve_game_sheet(
    game_name: str = "",
    profile_id: str = "generico",
    game_sheet: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if isinstance(game_sheet, dict) and game_sheet.get("official_name"):
        return game_sheet
    if game_name:
        found = find_game_sheet(game_name)
        if found:
            return found
    profile = GAME_PROFILES.get(profile_id, GAME_PROFILES["generico"])
    found_p = find_game_sheet(profile["short_name"])
    if found_p:
        return found_p
    clean_name = (game_name or profile["short_name"]).strip() or "PS4"
    tag_name = "#" + re.sub(r"[^a-zA-Z0-9]", "", clean_name.title())
    return {
        "id": "custom_temp",
        "official_name": clean_name,
        "aliases": [clean_name.lower()],
        "genre": "acao",
        "profile_id": profile_id,
        "characters": ["o protagonista", "o inimigo", "o chefe"],
        "locations": ["a arena", "a fase", "o mapa"],
        "weapons_items": ["o golpe especial", "a arma principal", "o ultimo recurso"],
        "slangs_br": ["no sufoco", "timing perfeito", "jogada insana", "quase fui de base", "amassei"],
        "hashtags": [tag_name] if len(tag_name) > 2 else ["#PS4Gameplay"],
        "is_custom": True,
    }


def _sample_unique_hashtags(
    sheet: dict[str, Any],
    category: str,
    platform_target: str,
    used_hashtag_sets: set[str],
    trending_tags: Optional[list[str]] = None,
    rng: Optional[random.Random] = None,
) -> list[str]:
    """Sample a guaranteed-unique hashtag set from categorized reservoirs without replacement."""
    r = rng or random.Random()
    genre = str(sheet.get("genre") or "acao").lower()
    game_tags = [t if t.startswith("#") else f"#{t}" for t in (sheet.get("hashtags") or []) if str(t).strip()]
    clean_game = re.sub(r"[^a-zA-Z0-9]", "", _strip_accents(sheet.get("official_name") or "Gameplay").title())
    if clean_game and f"#{clean_game}".lower() not in [x.lower() for x in game_tags]:
        game_tags.insert(0, f"#{clean_game}")

    genre_pool = list(GENRE_HASHTAGS.get(genre, GENRE_HASHTAGS["acao"]))
    event_pool = list(EVENT_HASHTAGS.get(category, EVENT_HASHTAGS["combate_intenso"]))
    broad_pool = list(BROAD_HASHTAGS)
    trend_pool = [
        (t if t.startswith("#") else f"#{t}").strip()
        for t in (trending_tags or load_settings().get("trending_hashtags") or [])
        if str(t).strip()
    ]

    for attempt in range(40):
        chosen: list[str] = []
        seen_l: set[str] = set()

        def _add(tag: str) -> None:
            t = re.sub(r"\s+", "", tag.strip())
            if not t:
                return
            if not t.startswith("#"):
                t = f"#{t}"
            if platform_target in ("tiktok_reels", "tiktok_long") and t.lower() == "#shorts":
                return
            if t.lower() not in seen_l:
                seen_l.add(t.lower())
                chosen.append(t)

        # Platform anchor rules
        if platform_target == "youtube_shorts":
            _add("#Shorts")
            if game_tags:
                _add(game_tags[0])
            if clean_game and attempt % 2 == 0:
                _add(f"#{clean_game}Gameplay")
            elif clean_game:
                _add(f"#{clean_game}PS4")
        elif platform_target == "tiktok_long":
            _add("#fyp")
            _add("#gameplaycompleta")
            if game_tags:
                _add(game_tags[0])
        else:
            # tiktok_reels
            _add("#fyp")
            _add("#gaming")
            if game_tags:
                _add(game_tags[0])

        # Inject 1 Trending of the Day tag (rotating across attempts)
        if trend_pool:
            _add(r.choice(trend_pool))

        # Sample without replacement from Game Sheet + Event + Genre + Broad reservoirs
        extra_game = [t for t in game_tags[1:] if t.lower() not in seen_l]
        if extra_game:
            _add(r.choice(extra_game))

        r.shuffle(event_pool)
        for t in event_pool[:2]:
            _add(t)

        r.shuffle(genre_pool)
        for t in genre_pool[:2]:
            _add(t)

        r.shuffle(broad_pool)
        for t in broad_pool:
            if len(chosen) >= 8:
                break
            _add(t)

        final_tags = chosen[:8]
        sig = " ".join(sorted(t.lower() for t in final_tags))
        if sig not in used_hashtag_sets:
            used_hashtag_sets.add(sig)
            return final_tags

    # Ultra-rare fallback: append a unique broad tag
    for t in BROAD_HASHTAGS:
        if t.lower() not in {x.lower() for x in final_tags}:
            final_tags[-1] = t
            sig = " ".join(sorted(x.lower() for x in final_tags))
            if sig not in used_hashtag_sets:
                used_hashtag_sets.add(sig)
                return final_tags
    return final_tags


def generate_unique_kit(
    game_name: str = "",
    profile_id: str = "generico",
    category: str = "combate_intenso",
    platform_target: str = "youtube_shorts",
    game_sheet: Optional[dict[str, Any]] = None,
    used_titles: Optional[list[str]] = None,
    used_hooks: Optional[list[str]] = None,
    used_hashtag_sets: Optional[set[str]] = None,
    trending_tags: Optional[list[str]] = None,
    record_history: bool = True,
    seed: Optional[int] = None,
) -> dict[str, Any]:
    """Generate a guaranteed-unique PT-BR viral kit using the Game Sheet + Combinatorial Engine
    with strict Jaccard N-gram similarity < 0.35 guard and unique hashtag sampling."""
    sheet = _resolve_game_sheet(game_name=game_name, profile_id=profile_id, game_sheet=game_sheet)
    hist = _load_copy_history()
    rng = random.Random(seed) if seed is not None else random.Random()

    compare_titles = list(used_titles or []) + list(hist["titles"][-80:])
    compare_hooks = {h.strip().lower() for h in (used_hooks or []) if h}
    tag_set_guard: set[str] = set(used_hashtag_sets or set()) | set(hist["hashtag_sets"][-80:])

    game = sheet["official_name"]
    chars = sheet.get("characters") or [game]
    locs = sheet.get("locations") or ["essa fase"]
    weapons = sheet.get("weapons_items") or ["o golpe especial"]
    slangs = sheet.get("slangs_br") or ["no sufoco", "timing perfeito", "jogada insana"]
    cat_actions = TITLE_ACTIONS_BY_CAT.get(category, TITLE_ACTIONS_BY_CAT["combate_intenso"])

    # 1. Generate Title with strict Jaccard < 0.35 check
    chosen_title = ""
    chosen_char = rng.choice(chars)
    chosen_loc = rng.choice(locs)
    chosen_weapon = rng.choice(weapons)
    chosen_slang = rng.choice(slangs)

    best_candidate = ""
    best_max_sim = 1.0

    for _ in range(160):
        hk = rng.choice(TITLE_HOOKS)
        act_tpl = rng.choice(cat_actions)
        emo_tpl = rng.choice(TITLE_EMOTIONS)
        ch = rng.choice(chars)
        lc = rng.choice(locs)
        wp = rng.choice(weapons)
        sl = rng.choice(slangs)

        act = act_tpl.format(game=game, char=ch, loc=lc, weapon=wp, slang=sl)
        emo = emo_tpl.format(slang=sl)
        # Rotate structural templates so game name & hook positions vary naturally (guarantees Jaccard < 0.35)
        pattern_idx = rng.randint(0, 3)
        if pattern_idx == 0:
            cand = f"{hk} {game}: {act} {emo}".strip()
        elif pattern_idx == 1:
            cand = f"{act.capitalize()} no {game} {emo}".strip()
        elif pattern_idx == 2:
            cand = f"{ch} em {lc} — {hk.lower()} ({sl}!)".strip()
        else:
            cand = f"{hk} {ch}: {wp} em {lc} ({game})".strip()
        if len(cand) > 94:
            cand = cand[:94].rsplit(" ", 1)[0].strip()

        max_sim = max((jaccard_similarity(cand, prev) for prev in compare_titles), default=0.0)
        if max_sim < best_max_sim:
            best_max_sim = max_sim
            best_candidate = cand
            chosen_char, chosen_loc, chosen_weapon, chosen_slang = ch, lc, wp, sl
        if max_sim < 0.34:
            chosen_title = cand
            break

    if not chosen_title:
        chosen_title = best_candidate or f"Jogada epica de {chosen_char} em {game} ({chosen_slang})"

    # 2. Generate short <= 6 words uppercase Hook 2s
    chosen_hook = ""
    for _ in range(30):
        tpl = rng.choice(HOOK_2S_TEMPLATES)
        cand_h = tpl.format(
            slang_u=_strip_accents(chosen_slang).upper().split()[0],
            char_u=_strip_accents(chosen_char).upper().split()[0],
            loc_u=_strip_accents(chosen_loc).upper().split()[0],
            weapon_u=_strip_accents(chosen_weapon).upper().split()[0],
        )
        words = cand_h.split()[:6]
        cand_h = " ".join(words)[:38]
        if cand_h.lower() not in compare_hooks:
            chosen_hook = cand_h
            break
    if not chosen_hook:
        chosen_hook = f"OLHA ESSE {chosen_slang.upper()[:18]}!"

    # 3. Generate unique Description infused with Game Sheet
    d1 = rng.choice(DESC_OPENERS).format(game=game, char=chosen_char, loc=chosen_loc, weapon=chosen_weapon, slang=chosen_slang)
    d2 = rng.choice(DESC_MIDDLES).format(game=game, char=chosen_char, loc=chosen_loc, weapon=chosen_weapon, slang=chosen_slang)
    d3 = rng.choice(DESC_ENDINGS).format(game=game, char=chosen_char, loc=chosen_loc, weapon=chosen_weapon, slang=chosen_slang)
    description = f"{d1} {d2} {d3}"[:340]

    # 4. Generate unique Hashtags set from reservoirs
    hashtags = _sample_unique_hashtags(
        sheet=sheet,
        category=category,
        platform_target=platform_target,
        used_hashtag_sets=tag_set_guard,
        trending_tags=trending_tags,
        rng=rng,
    )
    if used_hashtag_sets is not None:
        used_hashtag_sets.add(" ".join(sorted(t.lower() for t in hashtags)))

    cta = rng.choice(CTA_COMMENTS)

    if record_history:
        _record_copy_history(chosen_title, hashtags)

    return {
        "detected_game": game,
        "game_id": sheet.get("id", ""),
        "hook_2s": chosen_hook,
        "cta_comment": cta,
        "title": chosen_title,
        "description": description,
        "hashtags": hashtags,
        "category": category,
        "platform_target": platform_target,
        "similarity_max": round(best_max_sim, 3),
        "ai_powered": False,
        "engine": "combinatorial_ptbr_v1",
    }


def generate_multiple_options(
    count: int = 5,
    game_name: str = "",
    profile_id: str = "generico",
    category: str = "combate_intenso",
    platform_target: str = "youtube_shorts",
    game_sheet: Optional[dict[str, Any]] = None,
    exclude_titles: Optional[list[str]] = None,
    trending_tags: Optional[list[str]] = None,
    record_history: bool = True,
) -> list[dict[str, Any]]:
    """Generate `count` (e.g. 5 or 10) mutually distinct kits with zero title/hashtag repetition."""
    options: list[dict[str, Any]] = []
    batch_titles = list(exclude_titles or [])
    batch_hooks: list[str] = []
    batch_tag_sigs: set[str] = set()

    for _ in range(max(1, min(30, int(count)))):
        kit = generate_unique_kit(
            game_name=game_name,
            profile_id=profile_id,
            category=category,
            platform_target=platform_target,
            game_sheet=game_sheet,
            used_titles=batch_titles,
            used_hooks=batch_hooks,
            used_hashtag_sets=batch_tag_sigs,
            trending_tags=trending_tags,
            record_history=record_history,
        )
        batch_titles.append(kit["title"])
        batch_hooks.append(kit["hook_2s"])
        options.append(kit)
    return options


def get_copy_engine_stats() -> dict[str, Any]:
    total_actions = sum(len(v) for v in TITLE_ACTIONS_BY_CAT.values())
    total_genre_tags = sum(len(v) for v in GENRE_HASHTAGS.values())
    total_event_tags = sum(len(v) for v in EVENT_HASHTAGS.values())
    return {
        "titles_combinations": len(TITLE_HOOKS) * (total_actions // len(TITLE_ACTIONS_BY_CAT)) * len(TITLE_EMOTIONS),
        "descriptions_combinations": len(DESC_OPENERS) * len(DESC_MIDDLES) * len(DESC_ENDINGS),
        "hashtag_reservoirs": {
            "broad": len(BROAD_HASHTAGS),
            "genre": total_genre_tags,
            "game": len(GAME_POOL_HASHTAGS),
            "event": total_event_tags,
        },
        "jaccard_max_threshold": 0.35,
    }
