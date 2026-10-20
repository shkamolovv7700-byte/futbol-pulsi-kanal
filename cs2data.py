"""
CS2 ma'lumotlari:
- o'yinlar (kutilayotgan, jonli, tugagan) - Liquipedia (CC-BY-SA, API qoidalariga rioya qilinadi: gzip,
  bot nomi ko'rsatilgan User-Agent, sahifa 60 soniyada ko'pi bilan bir marta so'raladi);
- jamoalar reytingi - Valve'ning rasmiy reytingi (GitHub, ValveSoftware/counter-strike_regional_standings);
- yangiliklar - Cybersport.ru RSS, o'zbekchaga bepul tarjima (MyMemory, ishlamasa Google).
"""

import gzip
import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger("cs2data")
TASHKENT = timezone(timedelta(hours=5))
UA = "CS2PulsiBot/1.0 (https://t.me/cs2pulsi_uz; Telegram kanal uchun statistika)"

# Katta turnirlar (S/A darajasi) - shu turnirlar yoki TOP-20 jamoa o'ynasa, o'yin "muhim"
BIG_EVENTS = re.compile(r"major|iem|intel extreme|blast|esl pro league|esl one|pgl|thunderpick|fissure|"
                        r"starladder|esports world cup|ewc|cct global|perfect world|betboom dacha|skyesports",
                        re.IGNORECASE)


def _get(url: str, headers: dict | None = None, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return gzip.decompress(data) if r.headers.get("Content-Encoding") == "gzip" else data


# ---------- Liquipedia: o'yinlar ----------

_cache: dict = {"t": 0, "matches": []}


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _parse_block(b: str, finished: bool) -> dict | None:
    ts = re.search(r'data-timestamp="(\d+)"', b)
    names = re.findall(r'<span class="name"[^>]*><a [^>]*title="([^"]+)"', b)
    if not ts or len(names) < 2:
        return None
    # g'olib hisobining klassiga "match-info-header-winner" qo'shiladi
    scores = re.findall(r'match-info-header-scoreholder-score(?: match-info-header-winner)?">([^<]*)<', b)
    bo = re.search(r'scoreholder-lower">\((Bo\d)\)', b)
    tour = re.search(r'match-info-tournament-name"><a [^>]*><span>(.*?)</span>', b)
    return {
        "id": f"{ts.group(1)}-{names[0]}-{names[1]}",
        "when": datetime.fromtimestamp(int(ts.group(1)), TASHKENT),
        "team1": names[0], "team2": names[1],
        "s1": scores[0] if len(scores) > 1 else "", "s2": scores[1] if len(scores) > 1 else "",
        "bo": bo.group(1) if bo else "", "tournament": _clean(tour.group(1)) if tour else "",
        "finished": finished,
    }


def matches() -> list[dict]:
    """Liquipedia'dagi kutilayotgan/jonli va tugagan o'yinlar (60 soniyalik kesh bilan)."""
    if time.time() - _cache["t"] < 60:
        return _cache["matches"]
    raw = json.loads(_get("https://liquipedia.net/counterstrike/api.php?action=parse&page=Liquipedia:Matches"
                          "&format=json&prop=text"))["parse"]["text"]["*"]
    upcoming, _, completed = raw.partition('data-toggle-area-content="2"')
    result = []
    for part, finished in ((upcoming, False), (completed, True)):
        for b in part.split('<div class="match-info">')[1:]:
            m = _parse_block(b, finished)
            if m:
                result.append(m)
    _cache.update(t=time.time(), matches=result)
    return result


def is_live(m: dict) -> bool:
    return not m["finished"] and m["when"] <= datetime.now(TASHKENT) and m["s1"] != ""


# ---------- Valve reytingi ----------

_rank_cache: dict = {"t": 0, "value": None}


def ranking() -> tuple[str, list[dict]]:
    """Eng so'nggi rasmiy global reyting: (sana, [{o'rin, ochko, jamoa, tarkib}]). 6 soatlik kesh."""
    if _rank_cache["value"] and time.time() - _rank_cache["t"] < 6 * 3600:
        return _rank_cache["value"]
    year = datetime.now().year
    files = json.loads(_get(f"https://api.github.com/repos/ValveSoftware/counter-strike_regional_standings/contents/live/{year}"))
    name = sorted(f["name"] for f in files if f["name"].startswith("standings_global"))[-1]
    md = _get(f"https://raw.githubusercontent.com/ValveSoftware/counter-strike_regional_standings/main/live/{year}/{name}").decode()
    rows = []
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0].isdigit():
            rows.append({"rank": int(cells[0]), "points": int(cells[1]), "team": cells[2], "roster": cells[3]})
    date = re.search(r"(\d{4})_(\d{2})_(\d{2})", name)
    _rank_cache.update(t=time.time(), value=(f"{date.group(3)}.{date.group(2)}.{date.group(1)}", rows))
    return _rank_cache["value"]


def top_teams(n: int = 20) -> set[str]:
    try:
        return {norm(r["team"]) for r in ranking()[1][:n]}
    except Exception as e:
        log.warning("Reytingni olib bo'lmadi: %r", e)
        return set()


def norm(team: str) -> str:
    return re.sub(r"^team |[^a-z0-9]", "", team.lower().replace("team ", ""))


def is_important(m: dict, tops: set[str]) -> bool:
    return bool(BIG_EVENTS.search(m["tournament"])) or norm(m["team1"]) in tops or norm(m["team2"]) in tops


# ---------- Yangiliklar va tarjima ----------

# Cybersport.ru havolasida o'yin turi bor: /tags/cs2/... (Dota 2 dagi "Team Spirit" adashtirmasin)
CS_LINK = re.compile(r"/tags/(cs2|cs-go|counter-strike)/", re.IGNORECASE)
# reklama, stavka, bukmeker - kanalga tushmaydi
NEWS_AD = re.compile(r"ставк|букмекер|промокод|бонус|фрибет|выбери своего фаворита|казино|розыгрыш|"
                     r"кейс|скин|партнерск|реклам", re.IGNORECASE)
# Tarjimada buzilib qoladigan CS2 atamalarini tiklash
FIXES = {r"\bMiraj\b": "Mirage", r"\bYadro\b": "Nuke", r"\bQadimgi\b": "Ancient", r"\bPoyezd\b": "Train",
         r"\bKo'prik\b": "Overpass", r"\bChang ?2\b": "Dust2", r"\bVitalik\b": "Vitality", r"\bRuh\b": "Spirit",
         r"\bkontr-zarba\b": "Counter-Strike", r"\bAsosiy\b(?= turnir)": "Major", r"\bMayor\b": "Major",
         # tarjimon "игрок" ni ba'zan "futbolchi" deb o'giradi - CS2 da bu o'yinchi
         r"\b[Ff]utbolchi": "o'yinchi", r"\b[Ff]utbol jamoa": "jamoa", r"\b[Ff]utbol klub": "klub"}


def fetch_news() -> list[dict]:
    x = _get("https://www.cybersport.ru/rss/materials", {"User-Agent": "Mozilla/5.0"}).decode("utf-8", "replace")
    items = []
    for it in re.findall(r"<item>(.*?)</item>", x, re.S):
        def tag(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", it, re.S)
            return _clean(re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S)) if m else ""
        title, desc, link = tag("title"), tag("description"), tag("link")
        if not CS_LINK.search(link) or NEWS_AD.search(f"{title} {desc}"):
            continue
        try:
            ts = parsedate_to_datetime(tag("pubDate")).timestamp()
        except (TypeError, ValueError):
            ts = time.time()
        items.append({"id": link or title, "title": title, "desc": desc[:260], "link": link, "ts": ts})
    return items


LATIN_WORD = re.compile(r"(?<!\w)[A-Za-z][A-Za-z0-9._-]*")


def translate(text: str) -> str | None:
    """Ruschadan o'zbekchaga bepul tarjima. Ikkala xizmat ham ishlamasa - None.

    Rus matnidagi lotin harfli so'zlar - taxalluslar va jamoa nomlari (asap, donk, NAVI, Mirage).
    Tarjimon ularni "tarjima qilib" yubormasligi uchun X1Q, X2Q... bilan almashtirib, keyin qaytaramiz.
    """
    if not text:
        return ""
    names: list[str] = []

    def hide(m):
        names.append(m.group(0))
        return f"X{len(names)}Q"

    q = urllib.parse.quote(LATIN_WORD.sub(hide, text[:480]))
    for name, url in (("MyMemory", f"https://api.mymemory.translated.net/get?langpair=ru|uz&q={q}"),
                      ("Google", f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=ru&tl=uz&dt=t&q={q}")):
        try:
            d = json.loads(_get(url, {"User-Agent": "Mozilla/5.0"}, timeout=15))
            if name == "MyMemory":
                out = html.unescape(d["responseData"]["translatedText"])
                if d.get("responseStatus") != 200 or "MYMEMORY WARNING" in out:
                    continue
            else:
                out = "".join(part[0] for part in d[0])
        except Exception as e:
            log.warning("%s tarjima ishlamadi: %r", name, e)
            continue
        if len(set(re.findall(r"X(\d+)Q", out))) != len(names):
            continue  # tarjimon nomlardan birini buzdi - boshqasini sinaymiz
        # nomdan keyin o'zbekcha qo'shimcha bo'lsa, apostrof: asap'dan, Mirage'da
        out = re.sub(r"X(\d+)Q(?=[a-zʻ'’])", lambda m: names[int(m.group(1)) - 1] + "'", out)
        out = re.sub(r"X(\d+)Q", lambda m: names[int(m.group(1)) - 1], out)
        return _fix(out.replace("''", "'"))
    return None


def _fix(text: str) -> str:
    for pattern, good in FIXES.items():
        text = re.sub(pattern, good, text)
    return text.strip()
