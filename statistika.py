"""
Chempionatlar statistikasi: jadval (TOP 10), to'purarlar (TOP 10), bugungi o'yinlar, kechagi natijalar.
Ma'lumot ESPN ning ochiq API sidan olinadi (kalit kerak emas).
"""

import html
import json
import urllib.request
from datetime import datetime, timedelta, timezone

import rasm

TASHKENT = timezone(timedelta(hours=5))
API = "https://site.api.espn.com/apis"

# (ESPN kodi, nomi, belgisi, heshteg)
LEAGUES = [
    ("eng.1", "APL", "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "#APL"),
    ("esp.1", "La Liga", "🇪🇸", "#LaLiga"),
    ("ita.1", "Seriya A", "🇮🇹", "#SeriyaA"),
    ("ger.1", "Bundesliga", "🇩🇪", "#Bundesliga"),
    ("fra.1", "Liga 1", "🇫🇷", "#Liga1"),
]
CUPS = [
    ("uefa.champions", "Chempionlar ligasi", "🏆", "#ChempionlarLigasi"),
    ("uefa.europa", "Yevropa ligasi", "🥈", "#YevropaLigasi"),
]
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def place(n: int) -> str:
    return MEDALS.get(n, f"{n}.")


def table_post(league: tuple) -> tuple | None:
    code, name, flag, tag = league
    data = get(f"{API}/v2/sports/soccer/{code}/standings")
    entries = data["children"][0]["standings"]["entries"]
    rows = []
    for e in entries:
        stats = {s["name"]: s.get("displayValue", "") for s in e["stats"]}
        rows.append((int(stats.get("rank") or 99), e["team"]["shortDisplayName"], stats))
    rows.sort(key=lambda r: r[0])
    played = max((int(r[2].get("gamesPlayed") or 0) for r in rows), default=0)
    if not played:
        return None
    image = rasm.table_card(f"{name} — TOP 10", f"{played}-turdan keyin", [
        (rank, team, s["gamesPlayed"], s["wins"], s["ties"], s["losses"], s["pointDifferential"], s["points"])
        for rank, team, s in rows[:10]])
    leader = rows[0]
    caption = (f"{flag} <b>{name} jadvali — TOP 10</b>\n\n"
               f"🥇 Peshqadam: <b>{html.escape(leader[1])}</b> — {leader[2]['points']} ochko\n\n"
               f"Sizning jamoangiz nechanchi o'rinda? 👇\n\n#jadval {tag}")
    return caption, image


def scorers_post(league: tuple) -> tuple | None:
    code, name, flag, tag = league
    data = get(f"{API}/site/v2/sports/soccer/{code}/statistics")
    goals = next((s for s in data.get("stats", []) if s.get("name") == "goalsLeaders"), None)
    if not goals or not goals.get("leaders"):
        return None
    rows = []
    for n, leader in enumerate(goals["leaders"][:10], 1):
        athlete = leader["athlete"]
        team = (athlete.get("team") or {}).get("shortDisplayName") or (athlete.get("team") or {}).get("displayName", "")
        rows.append((n, athlete["displayName"], team, int(leader["value"])))
    image = rasm.scorers_card(f"{name} to'purarlari — TOP 10", rows)
    caption = (f"{flag} <b>{name}: to'purarlar — TOP 10</b>\n\n"
               f"👑 Yetakchi: <b>{html.escape(rows[0][1])}</b> — {rows[0][3]} gol\n\n"
               f"Kim to'purarlar poygasini yutadi? 🔥\n\n#toparar {tag}")
    return caption, image


def _events(code: str, start: datetime, end: datetime) -> list[dict]:
    """Toshkent vaqti bo'yicha [start, end) oralig'idagi o'yinlar."""
    # ESPN kunlarni UTC bo'yicha beradi va oraliq qabul qilmaydi - har kunni alohida so'raymiz
    raw, day = [], start.astimezone(timezone.utc).date()
    while day <= end.astimezone(timezone.utc).date():
        raw += get(f"{API}/site/v2/sports/soccer/{code}/scoreboard?dates={day:%Y%m%d}").get("events", [])
        day += timedelta(days=1)
    result = []
    for ev in raw:
        when = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(TASHKENT)
        if not (start <= when < end):
            continue
        teams = {c["homeAway"]: c for c in ev["competitions"][0]["competitors"]}
        if "home" not in teams or "away" not in teams:
            continue
        result.append({"when": when, "state": ev["status"]["type"]["state"],
                       "home": teams["home"]["team"]["shortDisplayName"], "away": teams["away"]["team"]["shortDisplayName"],
                       "hs": teams["home"].get("score", ""), "as": teams["away"].get("score", "")})
    return sorted(result, key=lambda e: e["when"])


def _day_window(days_back: int) -> tuple[datetime, datetime]:
    """Futbol kuni: 06:00 dan ertasi 06:00 gacha (kechki Yevropa o'yinlari shu kunga kiradi)."""
    now = datetime.now(TASHKENT)
    start = now.replace(hour=6, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    if now.hour < 6:
        start -= timedelta(days=1)
    return start, start + timedelta(days=1)


def fixtures_post() -> tuple | None:
    start, end = _day_window(0)
    blocks = []
    for code, name, flag, _ in LEAGUES + CUPS:
        events = [e for e in _events(code, start, end) if e["state"] == "pre"]
        if events:
            blocks.append((name, [(e["home"], f"{e['when']:%H:%M}", e["away"]) for e in events[:10]]))
    if not blocks:
        return None
    count = sum(len(rows) for _, rows in blocks)
    caption = (f"📅 <b>Bugungi o'yinlar</b> — {count} ta muhim o'yin <i>(Toshkent vaqti)</i>\n\n"
               "00:00 dan keyingi o'yinlar tunda bo'ladi 🌙\n\nQaysi o'yinni kutyapsiz? 👇\n\n#oyinlar #futbol")
    return caption, rasm.matches_card("Bugungi o'yinlar", blocks, "Bugungi o'yinlar")


# Jonli gollar faqat shu klublar o'ynayotgan o'yinlar va Chempionlar ligasi uchun yuboriladi
BIG_CLUBS = [
    "Real Madrid", "Barcelona", "Atlético", "Atletico", "Manchester City", "Manchester United", "Arsenal",
    "Liverpool", "Chelsea", "Tottenham", "Bayern", "Dortmund", "Leverkusen", "Paris Saint-Germain",
    "Internazionale", "Inter Milan", "AC Milan", "Juventus", "Napoli", "AS Roma", "Marseille",
]


def is_big(name: str) -> bool:
    return any(club.lower() in name.lower() for club in BIG_CLUBS)


def live_events() -> list[dict]:
    """Bugungi muhim o'yinlar: hisob, daqiqa va gollar ro'yxati bilan."""
    now = datetime.now(timezone.utc)
    days = {(now - timedelta(hours=10)).strftime("%Y%m%d"), now.strftime("%Y%m%d")}
    events = []
    for league in LEAGUES + CUPS:
        code = league[0]
        for day in sorted(days):
            for ev in get(f"{API}/site/v2/sports/soccer/{code}/scoreboard?dates={day}").get("events", []):
                comp = ev["competitions"][0]
                teams = {c["homeAway"]: c for c in comp["competitors"]}
                if "home" not in teams or "away" not in teams:
                    continue
                home, away = teams["home"], teams["away"]
                names = (home["team"]["displayName"], away["team"]["displayName"])
                if code != "uefa.champions" and not any(is_big(n) for n in names):
                    continue
                goals = []
                for d in comp.get("details", []):
                    if not d.get("scoringPlay"):
                        continue
                    athletes = d.get("athletesInvolved") or [{}]
                    goals.append({"key": f"{d['team']['id']}-{d['clock'].get('value')}",
                                  "minute": d["clock"].get("displayValue", ""), "sec": d["clock"].get("value") or 0,
                                  "team": d["team"]["id"], "scorer": athletes[0].get("displayName", ""),
                                  "pen": d.get("penaltyKick"), "og": d.get("ownGoal")})
                goals.sort(key=lambda g: g["sec"])
                events.append({
                    "id": ev["id"], "league": league, "state": ev["status"]["type"]["state"],
                    "clock": ev["status"].get("displayClock", ""),
                    "kickoff": datetime.fromisoformat(ev["date"].replace("Z", "+00:00")),
                    "home": home["team"]["shortDisplayName"], "away": away["team"]["shortDisplayName"],
                    "home_id": home["team"]["id"], "away_id": away["team"]["id"],
                    "hs": home.get("score", "0"), "as": away.get("score", "0"), "goals": goals,
                })
    return events


def goal_post(ev: dict, goal: dict) -> str:
    _, name, flag, tag = ev["league"]
    # gol paytidagi hisob: shu golgacha bo'lgan gollar soni
    upto = ev["goals"][: ev["goals"].index(goal) + 1]
    hs = sum(g["team"] == ev["home_id"] for g in upto)
    as_ = sum(g["team"] == ev["away_id"] for g in upto)
    scorer = html.escape(goal["scorer"]) if goal["scorer"] else ""
    if goal["og"]:
        scorer += " (avtogol)"
    elif goal["pen"]:
        scorer += " (penalti)"
    team = ev["home"] if goal["team"] == ev["home_id"] else ev["away"]
    return (f"⚽️ <b>GOOOL! {goal['minute']}</b>\n\n"
            f"{flag} {name}\n<b>{html.escape(ev['home'])} {hs}:{as_} {html.escape(ev['away'])}</b>\n\n"
            + (f"👤 {scorer} — {html.escape(team)}\n\n" if scorer else "")
            + f"#jonli {tag}")


def final_post(ev: dict) -> str:
    _, name, flag, tag = ev["league"]
    scorers = []
    for g in ev["goals"]:
        mark = " (a/g)" if g["og"] else " (p)" if g["pen"] else ""
        scorers.append(f"⚽️ {g['minute']} {html.escape(g['scorer'])}{mark}")
    return (f"🏁 <b>O'yin yakunlandi</b>\n\n{flag} {name}\n"
            f"<b>{html.escape(ev['home'])} {ev['hs']}:{ev['as']} {html.escape(ev['away'])}</b>\n\n"
            + ("\n".join(scorers) + "\n\n" if scorers else "")
            + f"Natija sizga yoqdimi? 👇\n\n#natija {tag}")


def results_post() -> tuple | None:
    start, end = _day_window(1)
    blocks = []
    for code, name, flag, _ in LEAGUES + CUPS:
        events = [e for e in _events(code, start, end) if e["state"] == "post"]
        if events:
            blocks.append((name, [(e["home"], f"{e['hs']} : {e['as']}", e["away"]) for e in events[:10]]))
    if not blocks:
        return None
    caption = "✅ <b>Kechagi natijalar</b>\n\nQaysi natija sizni hayratga soldi? 👇\n\n#natijalar #futbol"
    return caption, rasm.matches_card("Kechagi natijalar", blocks, "Natijalar")
