"""
Chempionatlar statistikasi: jadval (TOP 10), to'purarlar (TOP 10), bugungi o'yinlar, kechagi natijalar.
Ma'lumot ESPN ning ochiq API sidan olinadi (kalit kerak emas).
"""

import html
import json
import urllib.request
from datetime import datetime, timedelta, timezone

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


def table_post(league: tuple) -> str | None:
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
    lines = [f"{flag} <b>{name} jadvali — TOP 10</b>",
             f"<i>{played}-turdan keyin · G-D-M: g'alaba, durang, mag'lubiyat</i>", ""]
    for rank, team, s in rows[:10]:
        lines.append(f"{place(rank)} <b>{html.escape(team)}</b> — {s['points']} ochko "
                     f"<i>({s['gamesPlayed']} o'yin, {s['wins']}-{s['ties']}-{s['losses']}, {s['pointDifferential']})</i>")
    lines += ["", "Sizning jamoangiz nechanchi o'rinda? 👇", "", f"#jadval {tag}"]
    return "\n".join(lines)


def scorers_post(league: tuple) -> str | None:
    code, name, flag, tag = league
    data = get(f"{API}/site/v2/sports/soccer/{code}/statistics")
    goals = next((s for s in data.get("stats", []) if s.get("name") == "goalsLeaders"), None)
    if not goals or not goals.get("leaders"):
        return None
    lines = [f"{flag} <b>{name}: to'purarlar — TOP 10</b>", ""]
    for n, leader in enumerate(goals["leaders"][:10], 1):
        athlete = leader["athlete"]
        team = (athlete.get("team") or {}).get("shortDisplayName") or (athlete.get("team") or {}).get("displayName", "")
        team_txt = f" ({html.escape(team)})" if team else ""
        lines.append(f"{place(n)} <b>{html.escape(athlete['displayName'])}</b>{team_txt} — {int(leader['value'])} ⚽️")
    lines += ["", "Kim to'purarlar poygasini yutadi? 🔥", "", f"#toparar {tag}"]
    return "\n".join(lines)


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


def fixtures_post() -> str | None:
    start, end = _day_window(0)
    blocks = []
    for code, name, flag, _ in LEAGUES + CUPS:
        events = [e for e in _events(code, start, end) if e["state"] == "pre"]
        if events:
            rows = [f"🕐 {e['when']:%H:%M}{' (tunda)' if e['when'].date() > start.date() else ''}  "
                    f"{html.escape(e['home'])} — {html.escape(e['away'])}" for e in events[:10]]
            blocks.append(f"{flag} <b>{name}</b>\n" + "\n".join(rows))
    if not blocks:
        return None
    return ("📅 <b>Bugungi o'yinlar</b> <i>(Toshkent vaqti)</i>\n\n" + "\n\n".join(blocks)
            + "\n\nQaysi o'yinni kutyapsiz? 👇\n\n#oyinlar #futbol")


def results_post() -> str | None:
    start, end = _day_window(1)
    blocks = []
    for code, name, flag, _ in LEAGUES + CUPS:
        events = [e for e in _events(code, start, end) if e["state"] == "post"]
        if events:
            rows = [f"{html.escape(e['home'])} <b>{e['hs']}:{e['as']}</b> {html.escape(e['away'])}" for e in events[:10]]
            blocks.append(f"{flag} <b>{name}</b>\n" + "\n".join(rows))
    if not blocks:
        return None
    return "✅ <b>Kechagi natijalar</b>\n\n" + "\n\n".join(blocks) + "\n\nQaysi natija sizni hayratga soldi? 👇\n\n#natijalar #futbol"
