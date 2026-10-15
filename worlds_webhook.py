"""
Worlds LoL -> Discord por webhook (sin servidor).

Se ejecuta una vez y termina (pensado para GitHub Actions cada ~10 min):
  1. Descarga las partidas del Worlds desde Leaguepedia (Cargo).
  2. Publica por webhook las partidas nuevas, con contadores acumulados de
     cada campeón: elegido 🎯, baneado 🚫 y winrate.
  3. Una vez al día publica el resumen: más elegidos y más baneados.
  4. Guarda lo ya publicado en state.json.
"""
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
TOURNAMENT = os.getenv("TOURNAMENT_PREFIX", "2026 Season World Championship")
BACKFILL = os.getenv("BACKFILL", "false").lower() == "true"
SUMMARY_HOUR_UTC = int(os.getenv("SUMMARY_HOUR_UTC", "6"))  # 08:00 en Madrid (verano)

API = "https://lol.fandom.com/api.php"
HEADERS = {"User-Agent": "WorldsDiscordBot/1.0 (uso personal, webhook de Discord)"}
STATE_FILE = Path("state.json")
ROLE_ORDER = {"Top": 0, "Jungle": 1, "Mid": 2, "Bot": 3, "Support": 4}
GOLD = 0xF1C40F


def new_stats() -> dict:
    return {"picks": 0, "wins": 0, "bans": 0}


def cargo_query(**params) -> list[dict]:
    rows, offset = [], 0
    while True:
        q = {"action": "cargoquery", "format": "json", "limit": "500",
             "offset": str(offset), **params}
        r = requests.get(API, params=q, headers=HEADERS, timeout=30)
        r.raise_for_status()
        batch = [x["title"] for x in r.json().get("cargoquery", [])]
        rows.extend(batch)
        if len(batch) < 500:
            return rows
        offset += 500


def fetch_data():
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
        fields="GameId=GameId,Team1Bans=B1,Team2Bans=B2",
        where=where,
    )
    bans = {}
    for g in games:
        lst = []
        for key in ("B1", "B2"):
            for champ in (g.get(key) or "").split(","):
                champ = champ.strip()
                if champ and champ.lower() != "none":
                    lst.append(champ)
        bans[g["GameId"]] = lst
    return players, bans


def group_games(rows):
    games, order = defaultdict(list), []
    for r in rows:
        if r["GameId"] not in games:
            order.append(r["GameId"])
        games[r["GameId"]].append(r)
    return [games[g] for g in order if len(games[g]) >= 10]


def add_game(stats, game, bans):
    for p in game:
        s = stats[p["Champion"]]
        s["picks"] += 1
        s["wins"] += p["Win"] == "Yes"
    for champ in bans:
        stats[champ]["bans"] += 1


def wr_text(s):
    if not s["picks"]:
        return "WR —"
    return f"WR {round(100 * s['wins'] / s['picks'])}% ({s['wins']}-{s['picks'] - s['wins']})"


def counters(champ, stats):
    s = stats[champ]
    return f"🎯 {s['picks']} · 🚫 {s['bans']} · {wr_text(s)}"


def game_embed(game, game_bans, stats) -> dict:
    teams = defaultdict(list)
    for p in game:
        teams[p["Team"]].append(p)
    winner = next((t for t, ps in teams.items() if ps[0]["Win"] == "Yes"), "?")
    fields = []
    for team, players in teams.items():
        players.sort(key=lambda p: ROLE_ORDER.get(p["Role"], 9))
        lines = [
            f"`{p['Role'][:3]:<3}` **{p['Champion']}** · {p['Name']} "
            f"({p['K']}/{p['D']}/{p['A']})\n└ {counters(p['Champion'], stats)}"
            for p in players
        ]
        fields.append({"name": team, "value": "\n".join(lines)[:1024]})
    if game_bans:
        lines = [f"**{c}** · 🎯 {stats[c]['picks']} · 🚫 {stats[c]['bans']}" for c in game_bans]
        fields.append({"name": "Bans de la partida", "value": "\n".join(lines)[:1024]})
    return {
        "title": " vs ".join(teams),
        "description": f"🏆 Victoria de **{winner}**",
        "color": GOLD,
        "fields": fields,
        "footer": {"text": "🎯 elegido · 🚫 baneado · WR acumulado en este Worlds"},
    }


def summary_embed(stats) -> dict:
    picked = sorted((c for c in stats if stats[c]["picks"]),
                    key=lambda c: (-stats[c]["picks"], c))[:10]
    banned = sorted((c for c in stats if stats[c]["bans"]),
                    key=lambda c: (-stats[c]["bans"], c))[:10]
    return {
        "title": "📊 Resumen del Worlds",
        "color": GOLD,
        "fields": [
            {"name": "Más elegidos",
             "value": "\n".join(f"{i}. **{c}** — {counters(c, stats)}"
                                for i, c in enumerate(picked, 1)) or "—"},
            {"name": "Más baneados",
             "value": "\n".join(f"{i}. **{c}** — {counters(c, stats)}"
                                for i, c in enumerate(banned, 1)) or "—"},
        ],
    }


def post_embed(embed: dict) -> None:
    for _ in range(5):
        r = requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=30)
        if r.status_code == 429:  # rate limit de Discord
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        time.sleep(1.5)
        return
    raise RuntimeError("Discord sigue limitando el webhook")


def main() -> int:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
    first_run = state is None
    seen = set(state["seen"]) if state else set()
    last_summary = state.get("last_summary", "") if state else ""

    rows, bans = fetch_data()
    games = group_games(rows)

    running = defaultdict(new_stats)
    posted = 0
    for g in games:
        gid = g[0]["GameId"]
        game_bans = bans.get(gid, [])
        add_game(running, g, game_bans)
        if gid in seen:
            continue
        seen.add(gid)
        if first_run and not BACKFILL:
            continue
        post_embed(game_embed(g, game_bans, running))
        posted += 1
        # guardamos tras cada envío para no repetir si algo falla a mitad
        STATE_FILE.write_text(json.dumps({"seen": sorted(seen), "last_summary": last_summary}))

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if running and not first_run and now.hour >= SUMMARY_HOUR_UTC and last_summary != today:
        post_embed(summary_embed(running))
        last_summary = today
    elif first_run:
        last_summary = today  # no mandar resumen en el primer arranque

    STATE_FILE.write_text(json.dumps({"seen": sorted(seen), "last_summary": last_summary}))
    print(f"Partidas nuevas publicadas: {posted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
