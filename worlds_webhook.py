"""
Worlds LoL -> Discord por webhook (sin servidor).

Se ejecuta una vez y termina (pensado para GitHub Actions cada ~10 min).
Por cada partida terminada publica dos bloques:
  1. Elegidos (10) y baneados (10) de la partida, cada campeón con su
     acumulado en el Worlds contando esa partida (elegido ×N, baneado ×N, WR).
  2. Global del Worlds: más elegidos, más baneados y presencia
     (presencia = veces elegido + veces baneado).

Bans: se leen de ScoreboardGames y, si vienen vacíos, de PicksAndBansS7. Si una
partida aún no tiene bans en Leaguepedia, el bot ESPERA (hasta BANS_WAIT_HOURS)
antes de publicarla, para no mostrar contadores de bans incorrectos.
"""
import html
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
TOURNAMENT = os.getenv("TOURNAMENT_PREFIX", "2026 Season World Championship")
BACKFILL = os.getenv("BACKFILL", "false").lower() == "true"
BANS_WAIT_HOURS = float(os.getenv("BANS_WAIT_HOURS", "3"))
TOP_N = int(os.getenv("TOP_N", "10"))

API = "https://lol.fandom.com/api.php"
HEADERS = {"User-Agent": "WorldsDiscordBot/1.0 (uso personal, webhook de Discord)"}
STATE_FILE = Path("state.json")
ROLE_ORDER = {"Top": 0, "Jungle": 1, "Mid": 2, "Bot": 3, "Support": 4}
ROLE_LABEL = {"Top": "Top", "Jungle": "Jungla", "Mid": "Mid", "Bot": "ADC", "Support": "Support"}
GOLD = 0xF1C40F

# clave normalizada de campeón -> nombre para mostrar
display: dict[str, str] = {}


def canon(name: str) -> str:
    """Normaliza un nombre de campeón (Kai'Sa == KaiSa == Kai&#039;Sa)."""
    clean = html.unescape(name or "").strip()
    key = re.sub(r"[^a-z0-9]", "", clean.lower())
    display.setdefault(key, clean)
    return key


def new_stats() -> dict:
    return {"picks": 0, "wins": 0, "bans": 0}


# ----------------------------------------------------------------- datos
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def login() -> None:
    """Inicia sesión en Fandom con una bot password (límites mucho más altos).

    Usa los secretos FANDOM_USER (formato Usuario@NombreBot) y FANDOM_PASS.
    Si no existen o falla, se sigue de forma anónima. Nunca se imprime la clave.
    """
    user, pw = os.getenv("FANDOM_USER"), os.getenv("FANDOM_PASS")
    if not (user and pw):
        print("[login] sin credenciales de Fandom: consultas anónimas")
        return
    try:
        r = SESSION.get(API, params={"action": "query", "meta": "tokens",
                                     "type": "login", "format": "json"}, timeout=30)
        token = r.json()["query"]["tokens"]["logintoken"]
        r = SESSION.post(API, data={"action": "login", "lgname": user, "lgpassword": pw,
                                    "lgtoken": token, "format": "json"}, timeout=30)
        res = r.json().get("login", {})
        if res.get("result") == "Success":
            print(f"[login] sesión iniciada como {res.get('lgusername')}")
        else:
            print(f"[login] no se pudo iniciar sesión ({res.get('result')}: {res.get('reason', '')}); "
                  "se sigue de forma anónima")
    except Exception as e:
        print(f"[login] error al iniciar sesión ({type(e).__name__}); se sigue de forma anónima")


class RateLimited(Exception):
    """Fandom limita las peticiones anónimas por IP (las de GitHub se comparten)."""


RETRY_WAITS = [20, 40, 60]       # segundos entre reintentos
wait_budget = [140]              # tope total de espera por ejecución (el job dura 5 min)


def api_get(q: dict) -> dict:
    for attempt in range(len(RETRY_WAITS) + 1):
        r = SESSION.get(API, params=q, timeout=30)
        if r.status_code != 429:
            r.raise_for_status()
            data = r.json()
            err = data.get("error")
            if not err:
                return data
            if err.get("code") != "ratelimited":
                raise RuntimeError(f"Leaguepedia devolvió un error: {err}")
        if attempt == len(RETRY_WAITS) or wait_budget[0] < RETRY_WAITS[attempt]:
            raise RateLimited()
        wait = RETRY_WAITS[attempt]
        wait_budget[0] -= wait
        print(f"[limite] Leaguepedia limita las peticiones; reintento en {wait}s")
        time.sleep(wait)


def cargo_query(**params) -> list[dict]:
    rows, offset = [], 0
    while True:
        q = {"action": "cargoquery", "format": "json", "limit": "500",
             "offset": str(offset), **params}
        data = api_get(q)
        time.sleep(2)  # cortesía entre peticiones
        batch = [{k: html.unescape(v) if isinstance(v, str) else v for k, v in x["title"].items()}
                 for x in data.get("cargoquery", [])]
        rows.extend(batch)
        if len(batch) < 500:
            return rows
        offset += 500


def split_bans(raw) -> list[str]:
    out = []
    for c in (raw or "").split(","):
        c = html.unescape(c).strip()
        if c and c.lower() not in ("none", "null", "-"):
            out.append(c)
    return out


def fetch_data():
    """Devuelve (filas de jugadores, metadatos por partida con sus bans)."""
    where = f"OverviewPage LIKE '{TOURNAMENT}%'"
    players = cargo_query(
        tables="ScoreboardPlayers",
        fields=("GameId=GameId,Name=Name,Champion=Champion,Team=Team,"
                "Role=Role,Kills=K,Deaths=D,Assists=A,PlayerWin=Win,"
                "DateTime_UTC=DT"),
        where=where,
        order_by="DateTime_UTC ASC, GameId ASC",
    )
    games = cargo_query(
        tables="ScoreboardGames",
        fields="GameId=GameId,Team1=T1,Team2=T2,Team1Bans=B1,Team2Bans=B2",
        where=where,
    )
    meta = {}
    for g in games:
        meta[g["GameId"]] = {
            "t1": g.get("T1") or "Equipo 1",
            "t2": g.get("T2") or "Equipo 2",
            "b1": split_bans(g.get("B1")),
            "b2": split_bans(g.get("B2")),
        }

    # Fuente alternativa de bans: tabla PicksAndBansS7 (un campo por ban).
    if any(not (m["b1"] or m["b2"]) for m in meta.values()):
        try:
            fields = ["GameId=GameId", "Team1=T1", "Team2=T2"]
            fields += [f"Team1Ban{i}=A{i}" for i in range(1, 6)]
            fields += [f"Team2Ban{i}=B{i}" for i in range(1, 6)]
            pb = cargo_query(tables="PicksAndBansS7", fields=",".join(fields), where=where)
            used = 0
            for row in pb:
                m = meta.get(row.get("GameId"))
                if m and not (m["b1"] or m["b2"]):
                    m["b1"] = split_bans(",".join(row.get(f"A{i}", "") for i in range(1, 6)))
                    m["b2"] = split_bans(",".join(row.get(f"B{i}", "") for i in range(1, 6)))
                    used += bool(m["b1"] or m["b2"])
            print(f"[bans] PicksAndBansS7: {len(pb)} filas, {used} partidas completadas con esta fuente")
        except Exception as e:
            print(f"[bans] fuente alternativa no disponible: {e}")
    return players, meta


def group_games(rows):
    games, order = defaultdict(list), []
    for r in rows:
        if r["GameId"] not in games:
            order.append(r["GameId"])
        games[r["GameId"]].append(r)
    return [games[g] for g in order if len(games[g]) >= 10]


def add_game(stats, game, bans):
    for p in game:
        s = stats[canon(p["Champion"])]
        s["picks"] += 1
        s["wins"] += p["Win"] == "Yes"
    for champ in bans:
        stats[canon(champ)]["bans"] += 1


def game_age_hours(game) -> float:
    """Horas desde el inicio de la partida (inf si no se puede leer la fecha)."""
    try:
        dt = datetime.strptime(game[0]["DT"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return float("inf")


# ----------------------------------------------------------------- formato
def wr(s) -> str:
    if not s["picks"]:
        return "WR —"
    return f"WR {round(100 * s['wins'] / s['picks'])}% ({s['wins']}-{s['picks'] - s['wins']})"


def cut(lines: list[str]) -> str:
    text = "\n".join(lines) or "—"
    return text if len(text) <= 1024 else text[:1020] + "…"


def game_embed(n, game, m, stats, bans_missing) -> dict:
    teams = defaultdict(list)
    for p in game:
        teams[p["Team"]].append(p)
    winner = next((t for t, ps in teams.items() if ps[0]["Win"] == "Yes"), "?")

    fields = []
    for team, players in teams.items():
        players.sort(key=lambda p: ROLE_ORDER.get(p["Role"], 9))
        lines = []
        for p in players:
            k = canon(p["Champion"])
            role = ROLE_LABEL.get(p["Role"], p["Role"])
            lines.append(f"`{role:<7}` **{display[k]}** ×{stats[k]['picks']} · {wr(stats[k])}")
        fields.append({"name": f"🎯 Elegidos · {team}", "value": cut(lines)})

    if bans_missing:
        fields.append({"name": "🚫 Baneados",
                       "value": "⚠️ Leaguepedia aún no tiene los bans de esta partida."})
    else:
        for team, bans in ((m["t1"], m["b1"]), (m["t2"], m["b2"])):
            lines = [f"**{display[canon(c)]}** ×{stats[canon(c)]['bans']}" for c in bans]
            fields.append({"name": f"🚫 Baneados · {team}", "value": cut(lines)})

    return {
        "title": " vs ".join(teams),
        "description": f"🏆 Victoria de **{winner}**",
        "color": GOLD,
        "fields": fields,
        "footer": {"text": f"Partida nº{n} del Worlds · ×N = veces en el Worlds, contando esta partida"},
    }


def global_embed(stats) -> dict:
    def name(k):
        return display[k]

    picked = sorted((k for k in stats if stats[k]["picks"]),
                    key=lambda k: (-stats[k]["picks"], name(k).lower()))[:TOP_N]
    banned = sorted((k for k in stats if stats[k]["bans"]),
                    key=lambda k: (-stats[k]["bans"], name(k).lower()))[:TOP_N]
    present = sorted((k for k in stats if stats[k]["picks"] + stats[k]["bans"]),
                     key=lambda k: (-(stats[k]["picks"] + stats[k]["bans"]), name(k).lower()))[:TOP_N]

    return {
        "title": "📊 Global del Worlds tras esta partida",
        "color": GOLD,
        "fields": [
            {"name": "🎯 Más elegidos",
             "value": cut([f"{i}. **{name(k)}** ×{stats[k]['picks']} · {wr(stats[k])}"
                           for i, k in enumerate(picked, 1)])},
            {"name": "🚫 Más baneados",
             "value": cut([f"{i}. **{name(k)}** ×{stats[k]['bans']}"
                           for i, k in enumerate(banned, 1)])},
            {"name": "🔥 Presencia del campeón en Worlds (elegido + baneado)",
             "value": cut([f"{i}. **{name(k)}** — {stats[k]['picks'] + stats[k]['bans']} "
                           f"(🎯{stats[k]['picks']} + 🚫{stats[k]['bans']})"
                           for i, k in enumerate(present, 1)])},
        ],
    }


def post_embeds(embeds: list[dict]) -> None:
    for _ in range(5):
        r = requests.post(WEBHOOK_URL, json={"embeds": embeds}, timeout=30)
        if r.status_code == 429:  # rate limit de Discord
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        time.sleep(1.5)
        return
    raise RuntimeError("Discord sigue limitando el webhook")


# ----------------------------------------------------------------- principal
def main() -> int:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
    first_run = state is None
    seen = set(state["seen"]) if state else set()

    login()
    try:
        rows, meta = fetch_data()
    except RateLimited:
        # No es un error del bot: se reintenta solo en la próxima ejecución.
        print("[aviso] Leaguepedia sigue limitando las peticiones desde esta IP. "
              "No se publica nada ahora; se reintenta en la próxima ejecución.")
        return 0
    for r in rows:  # los nombres de los picks mandan como nombre para mostrar
        canon(r["Champion"])
    games = group_games(rows)

    with_bans = sum(1 for g in games if meta.get(g[0]["GameId"], {}).get("b1")
                    or meta.get(g[0]["GameId"], {}).get("b2"))
    print(f"[info] partidas completas: {len(games)} · con bans: {with_bans} · ya publicadas: {len(seen)}")

    stats = defaultdict(new_stats)
    posted = 0
    for n, g in enumerate(games, 1):
        gid = g[0]["GameId"]
        m = meta.get(gid) or {"t1": "Equipo 1", "t2": "Equipo 2", "b1": [], "b2": []}
        add_game(stats, g, m["b1"] + m["b2"])
        if gid in seen:
            continue
        if first_run and not BACKFILL:
            seen.add(gid)  # no inundar el canal con partidas antiguas
            continue

        bans_missing = not (m["b1"] or m["b2"])
        if bans_missing and game_age_hours(g) < BANS_WAIT_HOURS:
            print(f"[espera] {gid}: aún sin bans en Leaguepedia, se reintenta en la próxima ejecución")
            break  # mantiene el orden: las siguientes esperan a esta

        post_embeds([game_embed(n, g, m, stats, bans_missing), global_embed(stats)])
        seen.add(gid)
        posted += 1
        STATE_FILE.write_text(json.dumps({"seen": sorted(seen)}))  # por si algo falla a mitad

    STATE_FILE.write_text(json.dumps({"seen": sorted(seen)}))
    print(f"Partidas nuevas publicadas: {posted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
