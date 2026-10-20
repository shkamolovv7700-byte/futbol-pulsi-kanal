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
import urllib.error
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
    page = re.search(r'match-info-tournament-name"><a href="/counterstrike/([^"#]+)', b)
    return {
        "id": f"{ts.group(1)}-{names[0]}-{names[1]}",
        "when": datetime.fromtimestamp(int(ts.group(1)), TASHKENT),
        "team1": names[0], "team2": names[1],
        "s1": scores[0] if len(scores) > 1 else "", "s2": scores[1] if len(scores) > 1 else "",
        "bo": bo.group(1) if bo else "", "tournament": _clean(tour.group(1)) if tour else "",
        "page": urllib.parse.unquote(page.group(1)) if page else "", "ts": int(ts.group(1)),
        "finished": finished,
    }


def match_maps(m: dict) -> list[dict]:
    """Tugagan o'yinning xaritalari: [{xarita, hisob1, hisob2}] - turnir sahifasidagi o'yin oynasidan.

    O'yin vaqti (timestamp) va ikkala jamoa nomi bo'yicha topiladi. Topilmasa - bo'sh ro'yxat.
    """
    if not m.get("page"):
        return []
    h = _parse_page(m["page"])
    for popup in h.split("brkts-popup brkts-popup-container")[1:]:
        ts = re.search(r'data-timestamp="(\d+)"', popup)
        head = popup.split("brkts-popup-body")[0]
        names = list(dict.fromkeys(t for t in re.findall(r'title="([^"]+)"', head) if "(UTC" not in t))
        if not ts or int(ts.group(1)) != m["ts"] or len(names) < 2 or {names[0], names[1]} != {m["team1"], m["team2"]}:
            continue
        flipped = names[0] != m["team1"]
        maps = []
        for row in popup.split('class="brkts-popup-body-grid-row"')[1:]:
            scores = re.findall(r'detailed-scores-main-score">([^<]*)<', row)
            name = re.search(r'brkts-popup-spaced"><a [^>]*>([^<]+)</a>', row)
            if name and len(scores) == 2 and scores[0].strip().isdigit() and scores[1].strip().isdigit():
                s1, s2 = int(scores[0]), int(scores[1])
                maps.append({"map": html.unescape(name.group(1)).replace("Dust II", "Dust2"),
                             "s1": s2 if flipped else s1, "s2": s1 if flipped else s2})
        return maps
    return []


def matches() -> list[dict]:
    """Liquipedia'dagi kutilayotgan/jonli va tugagan o'yinlar (2,5 daqiqalik kesh bilan).

    GitHub serverlari IP manzilini boshqalar bilan bo'lishadi, shuning uchun Liquipedia "429 Too Many Requests"
    qaytarsa, 15 daqiqa kutamiz va oxirgi ma'lumot bilan ishlaymiz.
    """
    if time.time() - _cache["t"] < 150 or time.time() < _cache.get("backoff", 0):
        return _cache["matches"]
    try:
        raw = _parse_page("Liquipedia:Matches")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _cache["backoff"] = time.time() + 900
            log.warning("Liquipedia 429: 15 daqiqa kutamiz")
            return _cache["matches"]
        raise
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


# ---------- Liquipedia: turnirlar taqvimi va qatnashchilar ----------

_last_parse = [0.0]
_tour_cache: dict = {"t": 0, "value": []}
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
UZ_MONTHS = dict(zip(MONTHS, ("yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr",
                              "oktabr", "noyabr", "dekabr")))


def _parse_page(page: str) -> str:
    """Liquipedia sahifasi HTML. Qoidaga ko'ra parse so'rovlari orasida kamida 30 soniya."""
    wait = 31 - (time.time() - _last_parse[0])
    if wait > 0:
        time.sleep(wait)
    _last_parse[0] = time.time()
    return json.loads(_get(f"https://liquipedia.net/counterstrike/api.php?action=parse&page="
                           f"{urllib.parse.quote(page)}&format=json&prop=text"))["parse"]["text"]["*"]


def _dates(text: str) -> tuple:
    """'Nov 24 – Dec 13, 2026' / 'Nov 16–22, 2026' -> (boshlanish, tugash) sanalari."""
    m = re.match(r"(\w{3})\w* (\d+)\s*[–-]\s*(?:(\w{3})\w* )?(\d+), (\d{4})", text)
    if not m:
        return None, None
    m1, d1, m2, d2, y = m.groups()
    start = datetime(int(y), MONTHS[m1.lower()], int(d1), tzinfo=TASHKENT)
    end = datetime(int(y), MONTHS[(m2 or m1).lower()], int(d2), tzinfo=TASHKENT)
    if end < start:  # yil oxiridan keyingi yilga o'tadigan turnir
        start = start.replace(year=start.year - 1)
    return start, end


def tournaments() -> list[dict]:
    """S-darajali turnirlar (eng kattalari): nom, sahifa, sanalar, mukofot, joy, jamoalar soni. 6 soatlik kesh."""
    if _tour_cache["value"] and time.time() - _tour_cache["t"] < 6 * 3600:
        return _tour_cache["value"]
    h = _parse_page("S-Tier_Tournaments")
    result = []
    for row in re.findall(r'<tr class="table2&#95;&#95;row--body[^"]*">(.*?)</tr>', h, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 9:
            continue
        link = re.search(r'<a href="/counterstrike/([^"#]+)" title="([^"]+)"', cells[2])
        start, end = _dates(_clean(cells[3]))
        if not link or not start:
            continue
        result.append({"name": _clean(cells[2]), "page": urllib.parse.unquote(link.group(1)), "start": start,
                       "end": end, "prize": _clean(cells[4]), "location": _clean(cells[5]),
                       "teams": _clean(cells[6]), "winner": _clean(cells[7]),
                       "cancelled": "cancel" in _clean(cells[7]).lower()})
    _tour_cache.update(t=time.time(), value=result)
    return result


def _how(text: str) -> str:
    """'Invited VRS South America (Aug. 2026)' -> o'zbekcha qisqa izoh."""
    t = re.sub(r"\s+", " ", text).strip()
    rest = re.sub(r"^(invited|qualified)\s*", "", t, flags=re.I)
    rest = (rest.replace("VRS", "Valve reytingi").replace("Global Qualifier", "global saralash")
            .replace("Asian Qualifier", "Osiyo saralashi").replace("European Qualifier", "Yevropa saralashi")
            .replace("Qualifier", "saralash").replace("South America", "Janubiy Amerika")
            .replace("North America", "Shimoliy Amerika").replace("Americas", "Amerika").replace("Asia", "Osiyo")
            .replace("Europe", "Yevropa").replace("Global", "global"))
    rest = re.sub(r"\((\w{3})\. (\d{4})\)", lambda m: f"({UZ_MONTHS.get(m.group(1).lower(), m.group(1))} {m.group(2)})", rest)
    if t.lower().startswith("invited"):
        return "Taklif: " + (rest or "tashkilotchilardan")
    if t.lower().startswith("qualified") or "saralash" in rest:
        return "Saralashdan o'tdi: " + rest
    return rest


def participants(page: str) -> tuple[list[dict], str]:
    """Turnir qatnashchilari: [{jamoa, qanday kirgan}] va sahifaning oddiy matni (izohlarni qidirish uchun)."""
    h = _parse_page(page)
    section = h[h.find('id="Participants"'):]
    teams = []
    for card in section.split("general-collapsible collapsed team-participant-card\"")[1:]:
        name = re.search(r'title="([^"]+)"', card)
        label = re.search(r'card&#95;&#95;label"><span>(.*?)</span>', card, re.S)  # Invited / Qualified
        details = re.search(r'qualifier-details">(.*?)</span>', card, re.S)  # VRS Global / Asian Qualifier
        how = " ".join(_clean(x.group(1)) for x in (label, details) if x)
        if name:
            teams.append({"team": html.unescape(name.group(1)), "how": _how(how)})
    plain = re.sub(r"\s+", " ", _clean(re.sub(r"<(br|/div|/p|/li)[^>]*>", "\n", h)))
    return teams, plain


def absence_reason(team: str, page_text: str) -> str:
    """Sahifada jamoa haqida "rad etdi / chiqib ketdi" degan gap bo'lsa - o'sha gapni qaytaradi."""
    for sentence in re.split(r"(?<=[.!?\]])\s+", page_text):
        if team.lower() in sentence.lower() and re.search(r"declin|withdr|replac|disband|visa|forfeit", sentence, re.I):
            return sentence.strip()[:200]
    return ""


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
    """'G2 Esports', 'Team Falcons', 'BetBoom Team' -> 'g2', 'falcons', 'betboom' (manbalar nomni turlicha yozadi)."""
    t = re.sub(r"\b(team|esports?|gaming|clan|club)\b", "", team.lower())
    return re.sub(r"[^a-z0-9]", "", t)


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


def translate(text: str, src: str = "ru") -> str | None:
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

    q = urllib.parse.quote(LATIN_WORD.sub(hide, text[:480]) if src == "ru" else text[:480])
    for name, url in (("MyMemory", f"https://api.mymemory.translated.net/get?langpair={src}|uz&q={q}"),
                      ("Google", f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src}&tl=uz&dt=t&q={q}")):
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


def translate_en(text: str) -> str | None:
    """Liquipedia izohlari inglizcha."""
    return translate(text, "en")
