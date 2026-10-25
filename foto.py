"""
Postlar uchun haqiqiy foto - faqat erkin litsenziyali (Wikimedia Commons: CC BY / CC BY-SA / Public domain).

Sarlavhadan futbolchi, murabbiy yoki klubni topamiz (o'zbekcha yozilishi -> inglizcha Wikipedia maqolasi),
maqolaning asosiy rasmini olamiz va litsenziyasini tekshiramiz. Klub maqolalarida rasm odatda logotip
(erkin emas) bo'ladi, shuning uchun klub uchun uning stadioni surati olinadi.
Muallif va litsenziya rasm ustida ko'rsatiladi (CC BY-SA talabi).
"""

import io
import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("foto")
UA = {"User-Agent": "FutbolPulsiBot/1.0 (https://t.me/futbolpulsi_uz; kanal uchun erkin litsenziyali rasmlar)"}
FREE = re.compile(r"^(cc[ -]by|cc0|public domain|pd)", re.IGNORECASE)

# o'zbekcha yozilish o'zagi -> inglizcha Wikipedia maqolasi (avval aniqroq/uzunroqlari)
PEOPLE = {
    "mbappe": "Kylian Mbappé", "lamin yamal": "Lamine Yamal", "yamal": "Lamine Yamal", "xolann": "Erling Haaland",
    "messi": "Lionel Messi", "ronaldu": "Cristiano Ronaldo", "shomurodov": "Eldor Shomurodov",
    "xusanov": "Abdukodir Khusanov", "fayzullayev": "Abbosbek Fayzullaev", "flik": "Hansi Flick",
    "mourinyo": "José Mourinho", "gvardiola": "Pep Guardiola", "anchelotti": "Carlo Ancelotti",
    "arteta": "Mikel Arteta", "xabi alonso": "Xabi Alonso", "ten hag": "Erik ten Hag", "slot": "Arne Slot",
    "saloh": "Mohamed Salah", "salah": "Mohamed Salah", "kein": "Harry Kane", "bellingem": "Jude Bellingham",
    "vinisius": "Vinícius Júnior", "rafinya": "Raphinha", "kvaratsxeliya": "Khvicha Kvaratskhelia",
    "levandovski": "Robert Lewandowski", "modrich": "Luka Modrić", "valverde": "Federico Valverde",
    "rodri": "Rodri (footballer, born 1996)", "foden": "Phil Foden", "saka": "Bukayo Saka",
    "odegor": "Martin Ødegaard", "musiala": "Jamal Musiala", "virts": "Florian Wirtz", "osimxen": "Victor Osimhen",
    "lautaro": "Lautaro Martínez", "dembele": "Ousmane Dembélé", "pedri": "Pedri", "gavi": "Gavi (footballer)",
    "kurtua": "Thibaut Courtois", "alisson": "Alisson Becker", "neymar": "Neymar", "de bryuyne": "Kevin De Bruyne",
    "van deyk": "Virgil van Dijk", "rays": "Declan Rice", "palmer": "Cole Palmer", "isak": "Alexander Isak",
    "gyokeres": "Viktor Gyökeres", "kubarsi": "Pau Cubarsí", "olise": "Michael Olise", "kane": "Harry Kane",
    "ramos": "Sergio Ramos", "kasemiro": "Casemiro", "bruno fernandesh": "Bruno Fernandes", "frenki de yong": "Frenkie de Jong",
}
STADIUMS = {
    "real": "Santiago Bernabéu Stadium", "barselona": "Camp Nou", "barsa": "Camp Nou", "atletiko": "Metropolitano Stadium",
    "manchester yunayted": "Old Trafford", "manchester siti": "City of Manchester Stadium", "siti": "City of Manchester Stadium",
    "liverpul": "Anfield", "arsenal": "Emirates Stadium", "chelsi": "Stamford Bridge", "tottenhem": "Tottenham Hotspur Stadium",
    "bavariya": "Allianz Arena", "borussiya": "Westfalenstadion", "psj": "Parc des Princes", "yuventus": "Juventus Stadium",
    "milan": "San Siro", "inter": "San Siro", "napoli": "Stadio Diego Armando Maradona", "roma": "Stadio Olimpico",
    "nyukasl": "St James' Park", "aston villa": "Villa Park", "paxtakor": "Pakhtakor Central Stadium",
    "o'zbekiston": "Milliy Stadium", "oʻzbekiston": "Milliy Stadium", "o‘zbekiston": "Milliy Stadium",
}


# ESPN jamoa nomi (inglizcha) -> uy stadioni (#MATCHDAY rasmlari uchun)
EN_STADIUMS = {
    "real madrid": "Santiago Bernabéu Stadium", "barcelona": "Camp Nou", "atlético": "Metropolitano Stadium",
    "atletico": "Metropolitano Stadium", "manchester united": "Old Trafford", "man united": "Old Trafford",
    "manchester city": "City of Manchester Stadium", "man city": "City of Manchester Stadium", "liverpool": "Anfield",
    "arsenal": "Emirates Stadium", "chelsea": "Stamford Bridge", "tottenham": "Tottenham Hotspur Stadium",
    "spurs": "Tottenham Hotspur Stadium", "newcastle": "St James' Park", "aston villa": "Villa Park",
    "bayern": "Allianz Arena", "dortmund": "Westfalenstadion", "leverkusen": "BayArena",
    "paris": "Parc des Princes", "psg": "Parc des Princes", "juventus": "Juventus Stadium", "milan": "San Siro",
    "inter": "San Siro", "napoli": "Stadio Diego Armando Maradona", "roma": "Stadio Olimpico", "marseille": "Stade Vélodrome",
}


def player_photo(name: str, cache: dict) -> tuple[bytes, dict] | None:
    """Futbolchining erkin litsenziyali surati (Wikipedia: "Ism" yoki "Ism (footballer)")."""
    if not name:
        return None
    info = lookup(name, cache) or lookup(f"{name} (footballer)", cache)
    if not info:
        return None
    try:
        return _get(info["url"], timeout=30), info
    except Exception as e:
        log.warning("Futbolchi rasmi yuklanmadi (%s): %r", name, e)
        return None


_logos: dict = {}


def logo(url: str) -> bytes | None:
    """Jamoa logotipi (ESPN) - bir ishga tushish davomida keshlanadi."""
    if not url:
        return None
    if url not in _logos:
        try:
            _logos[url] = _get(url, timeout=20)
        except Exception as e:
            log.warning("Logotip yuklanmadi: %r", e)
            _logos[url] = None
    return _logos[url]


def stadium_photo(team: str, cache: dict) -> tuple[bytes, str] | None:
    low = team.lower()
    title = next((v for k, v in EN_STADIUMS.items() if k in low), None)
    info = lookup(title, cache) if title else None
    if not info:
        return None
    try:
        return _get(info["url"], timeout=30), f"Foto: {info['author']} · {info['license']} · Wikimedia"
    except Exception as e:
        log.warning("Stadion rasmi yuklanmadi: %r", e)
        return None


def _get(url: str, timeout: int = 20) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


# oddiy so'z ichida ham uchraydiganlar ("intervyu", "reallik") - faqat qo'shtirnoq ichida hisoblanadi
AMBIGUOUS = {"real", "inter", "roma", "siti", "slot", "saka", "gavi", "rays", "kane", "milan"}


def subject(text: str) -> str | None:
    """Matndagi birinchi taniqli shaxs, bo'lmasa klub stadioni (Wikipedia maqola nomi)."""
    low = text.lower()
    for table in (PEOPLE, STADIUMS):
        hits = []
        for k in sorted(table, key=len, reverse=True):
            pattern = rf"[«\"“']{re.escape(k)}" if k in AMBIGUOUS else rf"(?<![a-zʻ'‘]){re.escape(k)}"
            m = re.search(pattern, low)
            if m:
                hits.append((m.start(), table[k]))
        if hits:
            return min(hits)[1]
    return None


def lookup(title: str, cache: dict) -> dict | None:
    """{url, author, license} yoki None (erkin litsenziyali rasm topilmasa). Natija keshda saqlanadi."""
    if title in cache:
        return cache[title]
    found = None
    try:
        q = urllib.parse.quote(title)
        d = json.loads(_get(f"https://en.wikipedia.org/w/api.php?action=query&titles={q}&prop=pageimages"
                            "&piprop=name&format=json&redirects=1"))
        name = next(iter(d["query"]["pages"].values())).get("pageimage")
        if name:
            info = json.loads(_get("https://commons.wikimedia.org/w/api.php?action=query&titles=File:"
                                   f"{urllib.parse.quote(name)}&prop=imageinfo&iiprop=url|extmetadata"
                                   "&iiurlwidth=1280&format=json"))
            ii = next(iter(info["query"]["pages"].values())).get("imageinfo", [{}])[0]
            meta = ii.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            if ii.get("thumburl") and FREE.search(lic):
                author = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
                found = {"url": ii["thumburl"], "author": author[:40] or "Wikimedia Commons", "license": lic}
    except Exception as e:  # tarmoq xatosi - rasmsiz davom etamiz, keyin qayta urinmaslik uchun keshlamaymiz
        log.warning("Wikimedia rasmi olinmadi (%s): %r", title, e)
        return None
    cache[title] = found
    return found


def photo_for(text: str, cache: dict) -> tuple[bytes, str] | None:
    """Matnga mos foto (bayt) va "Foto: muallif / litsenziya" yozuvi."""
    title = subject(text)
    info = lookup(title, cache) if title else None
    if not info:
        return None
    try:
        return _get(info["url"], timeout=30), f"Foto: {info['author']} · {info['license']} · Wikimedia"
    except Exception as e:
        log.warning("Rasmni yuklab bo'lmadi: %r", e)
        return None
