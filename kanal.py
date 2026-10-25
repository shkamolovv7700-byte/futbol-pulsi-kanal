"""
Futbol yangiliklari kanali.

agent.py ichidan ishga tushadi (o'sha Telegram akkaunt orqali):
- kanal hali yo'q bo'lsa, uni yaratadi, tavsif va havola qo'yadi, salomlashuv postini pin qiladi;
- sports.uz, daryo.uz, kun.uz dan futbol yangiliklarini olib, kunduzi ma'lum oraliqda post qiladi;
- rasmiy YouTube kanallaridan (APL, UEFA, FIFA, LaLiga, Seriya A) kuniga 4 ta video havolasini joylaydi;
- har kuni viktorina (quiz) va kechqurun so'rovnoma joylaydi (kanal_kontent.json dan).
"""

import asyncio
import html
import json
import logging
import os
import random
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.functions.channels import CreateChannelRequest, UpdateUsernameRequest
from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, TextWithEntities

import foto
import moderator
import rasm
import statistika
import video

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "kanal_state.json"
CONTENT_FILE = BASE_DIR / "kanal_kontent.json"

ENABLED = os.getenv("KANAL_YOQILGAN", "0").strip() == "1"
TITLE = os.getenv("KANAL_NOMI", "Gol Vaqti ⚽️").strip()
USERNAME = os.getenv("KANAL_USERNAME", "").strip().lstrip("@")
# Kanalni boshqa akkaunt ochgan bo'lsa (sizni admin qilib): KANAL_MAVJUD=1 va KANAL_USERNAME=kanal havolasi.
# Bunda kanal yaratilmaydi, faqat unga post qilinadi.
EXISTING = os.getenv("KANAL_MAVJUD", "0").strip() == "1"
ABOUT = os.getenv(
    "KANAL_TAVSIF",
    "⚽️ Futbol olamining eng tezkor yangiliklari — o'zbek tilida.\n"
    "🏆 APL, La Liga, Seriya A, Chempionlar ligasi, O'zbekiston\n"
    "🧠 Har kuni viktorina va so'rovnoma",
).strip()
# O'zbek tilidagi yangilik manbalari (umumiy saytlardan faqat futbolga oidlari olinadi)
NEWS_FEEDS = [
    ("Sports.uz", "https://sports.uz/oz/rss"),
    ("Daryo.uz", "https://daryo.uz/feed"),
    ("Kun.uz", "https://kun.uz/news/rss"),
]
NEWS_MAX_AGE_HOURS = 12
# Rasmiy YouTube kanallari: video qayta yuklanmaydi, faqat havolasi joylanadi
VIDEO_FEEDS = [
    ("Premier League", "UCG5qGWdu8nIRZqJ_GgDwQ-w"),
    ("UEFA", "UCyGa1YEx9ST66rYrJTGIKOw"),
    ("LaLiga", "UCTv-XvfzLX3i4IGWAm4sbmA"),
    ("Serie A", "UCBJeMCIeLQos7wacox4hmLQ"),
]
VIDEO_MAX_AGE_DAYS = 3
# O'yin sharhlari / gollar videosi ekanini sarlavhadan bilamiz
HIGHLIGHT_PATTERN = re.compile(r"highlight|goals|gol|sintesi|resumen|extended|all goals", re.IGNORECASE)
# Reklama, homiylik va tikish (stavka) haqidagi yozuvlar kanalga tushmaydi
AD_PATTERN = re.compile(
    r"reklama|homiy|hamkorlik asosida|promo|aksiya|bonus|bukmeker|stavka|1xbet|betting|kazino|tikish|"
    r"chegirma|sponsor|\bpr\b", re.IGNORECASE)
DAY_START, DAY_END = 8, 23  # shu soatlar oralig'ida har 30 daqiqada bitta post
QUIZ_HOUR = 13
POLL_HOUR = 20
CHECK_EVERY_SECONDS = 5 * 60
LIVE_POLL_SECONDS = 60
RUN_WINDOW_SECONDS = 25 * 60

log = logging.getLogger("kanal")

# Faqat futbolga oid yangiliklarni o'tkazish uchun so'z boshlanmalari.
# O'zbekchada qo'shimcha so'z oxiriga qo'shiladi ("Milanga"), shuning uchun faqat boshini tekshiramiz.
# "Inter", "Roma", "Real" oddiy so'zlarga o'xshagani uchun faqat qo'shtirnoq ichida hisoblanadi.
FOOTBALL_PATTERN = re.compile(
    r"\b(futbol|gol(?!f|land)|liga|apl\b|premer|bundesliga|la ?liga|chempionlar|uefa|fifa|superliga"
    r"|terma jamoa|transfer|murabbiy|darvozabon|penalti|assist|jahon chempionati|osiyo kubogi"
    r"|barselona|barsa|psj\b|manchester|liverpul|arsenal|chelsi|tottenhem|borussiya|yuventus"
    r"|napoli|atletiko|paxtakor|nasaf|navbahor|neftchi|bunyodkor|messi|ronaldu|mbappe|xolann"
    r"|shomurodov|xusanov|fayzullayev)"
    # "Bavariya" (viloyat), "Milan" (shahar), "Real", "Inter" oddiy so'z ham bo'ladi - faqat qo'shtirnoq ichida klub
    r"|[«\"“](inter|roma|real|psj|lokomotiv|andijon|bavariya|milan)",
    re.IGNORECASE,
)
# "Jahon chempionati", "terma jamoa", "murabbiy" boshqa sport turlarida ham bor - ular kanalga tushmasin
OTHER_SPORTS = re.compile(
    r"boks|bokschi|kurash|dzyudo|tennis|shaxmat|sambo|taekvondo|basketbol|voleybol|xokkey|og[ʻ‘'`]?ir atletika"
    r"|gimnastika|suzish|velosport|formula|mma\b|ufc|jang san|paralimpiya|eshkak|qilichboz|kamon|ko[ʻ‘'`]?pkurash",
    re.IGNORECASE,
)
# Ba'zi saytlar matnni turkcha harflar bilan yuboradi ("toğlaridagi", "öt qöyişda") - o'zbek lotiniga
TURKISH = str.maketrans({"ğ": "g‘", "Ğ": "G‘", "ö": "o‘", "Ö": "O‘", "ş": "sh", "Ş": "Sh", "ı": "i", "ç": "ch",
                         "Ç": "Ch", "ü": "u", "Ü": "U"})
HASHTAGS = {
    "#APL": ["apl", "premer", "manchester", "liverpul", "arsenal", "chelsi", "tottenhem", "lids"],
    "#LaLiga": ["la liga", "laliga", "real", "barselona", "atletiko", "sevilya"],
    "#SeriyaA": ["seriya a", "a seriya", "milan", "inter", "yuventus", "napoli", "roma", "atalanta"],
    "#Bundesliga": ["bundesliga", "bavariya", "borussiya", "leverkuzen"],
    "#ChempionlarLigasi": ["chempionlar ligasi", "chempionlar"],
    "#Ozbekiston": ["o‘zbekiston", "oʻzbekiston", "o'zbekiston", "superliga", "paxtakor", "nasaf",
                    "navbahor", "neftchi", "shomurodov", "xusanov", "fayzullayev", "legion"],
    "#Transfer": ["transfer", "shartnoma", "ijara"],
}
HOOKS = [
    "Sizningcha, bu qanchalik muhim? Reaksiya bilan bildiring 👇",
    "🔥 — ajoyib, 🤔 — hali ko'ramiz, 👎 — yoqmadi",
    "Bu yangilikni do'stingizga ham yuboring 📲",
    "Qanday fikrdasiz? Reaksiya qoldiring 👇",
    "Kutilganmi yoki kutilmaganmi? 👀",
]

state: dict = {}


def load_state() -> None:
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    state.setdefault("seen", [])
    state.setdefault("used_quiz", [])
    state.setdefault("used_poll", [])


def save_state() -> None:
    state["seen"] = state["seen"][-1500:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Yangiliklar ----------

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"


def fetch_xml(url: str) -> ET.Element:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return ET.fromstring(resp.read())


def parse_date(text: str | None) -> float:
    try:
        return (parsedate_to_datetime(text) if "," in text else datetime.fromisoformat(text)).timestamp()
    except (TypeError, ValueError):
        return datetime.now().timestamp()


def fetch_feed(source: str, url: str) -> list[dict]:
    """Bitta RSS manbadan yangiliklarni oladi."""
    items = []
    for it in fetch_xml(url).iter("item"):
        title = (it.findtext("title") or "").strip().translate(TURKISH)
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "").strip().translate(TURKISH)
        if len(desc) > 300:
            desc = desc[:300].rsplit(" ", 1)[0] + "…"
        items.append({"id": (it.findtext("guid") or link).strip(), "title": title, "desc": desc,
                      "link": link, "source": source, "ts": parse_date(it.findtext("pubDate"))})
    return items


def fetch_all_news() -> list[dict]:
    items = []
    for source, url in NEWS_FEEDS:
        try:
            items.extend(fetch_feed(source, url))
        except Exception as e:  # bitta manba ishlamasa, qolganlari bilan davom etamiz
            log.warning("%s yangiliklarini olib bo'lmadi: %s", source, e)
    return items


def title_words(title: str) -> set[str]:
    return {w for w in re.findall(r"\w+", title.lower()) if len(w) > 3}


def is_duplicate(title: str) -> bool:
    """Boshqa saytda xuddi shu yangilik allaqachon joylanganmi."""
    words = title_words(title)
    for old in state.get("recent_titles", []):
        old_words = title_words(old)
        if words and old_words and len(words & old_words) / len(words | old_words) >= 0.5:
            return True
    return False


def is_football(item: dict) -> bool:
    text = f"{item['title']} {item['desc']}"
    if OTHER_SPORTS.search(text) and not re.search(r"futbol", text, re.IGNORECASE):
        return False
    return bool(FOOTBALL_PATTERN.search(text))


def make_hashtags(item: dict) -> str:
    text = f"{item['title']} {item['desc']}".lower()
    tags = [tag for tag, words in HASHTAGS.items() if any(w in text for w in words)]
    return " ".join(["#futbol", *tags[:2]])


def channel_footer() -> str:
    return f"\n\n📲 @{USERNAME}" if USERNAME else ""


# Klub -> davlat bayrog'i (sarlavha boshida, FutbollLive_TV uslubida)
CLUB_FLAGS = [
    ("🇪🇸", r"real|barselona|barsa|atletiko|sevilya|valensiya|betis|vilyarreal"),
    ("🏴󠁧󠁢󠁥󠁮󠁧󠁿", r"manchester|siti|liverpul|arsenal|chelsi|tottenhem|nyukasl|aston villa|apl|premer"),
    ("🇩🇪", r"bavariya|borussiya|leverkuzen|leypsig|bundesliga"),
    ("🇮🇹", r"yuventus|milan|inter|napoli|roma|latsio|atalanta|seriya a|a seriya"),
    ("🇫🇷", r"psj|marsel|monako|liga 1"),
    ("🇺🇿", r"o[ʻ‘'`]?zbekiston|paxtakor|nasaf|navbahor|neftchi|shomurodov|xusanov|fayzullayev"),
    ("🇪🇺", r"chempionlar ligasi|uefa|yevropa ligasi|evropa ligasi"),
]
BREAKING = re.compile(r"rasman|tezkor|shoshilinch|jarohat|iste[ʼ'‘]?fo|ishdan bo[ʻ‘'`]?shat|o[ʻ‘'`]?tdi|"
                      r"imzoladi|shartnoma|ketadi|keladi|transfer", re.IGNORECASE)


def flag_for(text: str) -> str:
    low = text.lower()
    hits = [(m.start(), f) for f, pat in CLUB_FLAGS if (m := re.search(pat, low))]
    return min(hits)[1] if hits else "⚽️"


def news_tag(item: dict) -> str:
    text = f"{item['title']} {item['desc']}"
    if re.search(r"transfer|shartnoma|imzoladi|ijara", text, re.IGNORECASE):
        return "TRANSFER"
    return "TEZKOR" if BREAKING.search(text) else "YANGILIK"


def format_news(item: dict) -> str:
    """Qisqa va jonli: bayroq + qalin sarlavha, 1-2 gap mazmun, teg va kanal imzosi."""
    tag = news_tag(item)
    parts = [f"{'🚨 #BREAKING' if tag == 'TEZKOR' else '🔁 #TRANSFER' if tag == 'TRANSFER' else ''}".strip(),
             f"{flag_for(item['title'])} <b>{html.escape(item['title'])}</b>"]
    if item["desc"] and item["desc"].lower() not in item["title"].lower():
        parts.append(f"📰 {html.escape(item['desc'])}")
    source = f'<i>Manba: <a href="{html.escape(item["link"], quote=True)}">{item["source"]}</a></i>'
    return "\n\n".join(p for p in parts if p) + f"\n\n⚽️ @{USERNAME} — futbol olami bir joyda\n{source}"


def is_ad(item: dict) -> bool:
    return bool(AD_PATTERN.search(f"{item['title']} {item['desc']}"))


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


async def post_news(client: TelegramClient, channel) -> bool:
    items = await asyncio.to_thread(fetch_all_news)

    seen = set(state["seen"])
    min_ts = datetime.now().timestamp() - NEWS_MAX_AGE_HOURS * 3600
    # eng yangi 5 tasidan taniqli futbolchi/klub haqidagisini afzal ko'ramiz - unga haqiqiy foto topiladi
    fresh = [i for i in sorted(items, key=lambda i: i["ts"], reverse=True)
             if i["id"] not in seen and i["ts"] >= min_ts and is_football(i) and not is_ad(i)
             and not is_duplicate(i["title"])][:5]
    if not fresh:
        save_state()
        return False
    item = next((i for i in fresh if foto.subject(i["title"])), fresh[0])
    state["seen"].append(item["id"])

    cache = state.setdefault("foto_cache", {})
    photo = await asyncio.to_thread(foto.photo_for, item["title"], cache)
    if photo:
        image = await asyncio.to_thread(rasm.photo_card, photo[0], item["title"], news_tag(item), photo[1])
    else:
        image = await asyncio.to_thread(rasm.news_card, item["title"], item["desc"], item["source"], news_tag(item))
    await client.send_file(channel, image, caption=format_news(item)[:1024], parse_mode="html")
    state["recent_titles"] = (state.get("recent_titles", []) + [item["title"]])[-60:]
    save_state()
    log.info("Kanalga yangilik joylandi: %s", item["title"][:60])
    return True


# ---------- Videolar ----------


def fetch_videos() -> list[dict]:
    videos = []
    for name, channel_id in VIDEO_FEEDS:
        try:
            root = fetch_xml(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
        except Exception as e:
            log.warning("%s videolarini olib bo'lmadi: %s", name, e)
            continue
        for entry in root.iter(f"{ATOM}entry"):
            video_id = entry.findtext(f"{YT}videoId")
            if not video_id:
                continue
            videos.append({"id": f"yt:{video_id}", "title": (entry.findtext(f"{ATOM}title") or "").strip(),
                           "link": f"https://www.youtube.com/watch?v={video_id}", "source": name,
                           "ts": parse_date(entry.findtext(f"{ATOM}published"))})
    return videos


def format_video(video: dict) -> str:
    # birinchi havola - video: Telegram uni pastda pleer bilan ko'rsatadi
    return (
        f'🎬 <a href="{html.escape(video["link"], quote=True)}"><b>{html.escape(video["title"])}</b></a>\n\n'
        f'▶️ <a href="{html.escape(video["link"], quote=True)}">Videoni ko\'rish</a> · 📺 {html.escape(video["source"])}'
        f"\n\n#video #gollar"
        f"\n\n⚽️ @{USERNAME} — futbol olami bir joyda"
    )


async def post_video(client: TelegramClient, channel) -> bool:
    seen = set(state["seen"])
    min_ts = datetime.now().timestamp() - VIDEO_MAX_AGE_DAYS * 86400
    videos = await asyncio.to_thread(fetch_videos)
    # har safar boshqa kanaldan bo'lsin: oxirgi ishlatilgan manbadan boshqasini afzal ko'ramiz
    fresh = [v for v in videos if v["id"] not in seen and v["ts"] >= min_ts and not AD_PATTERN.search(v["title"])]
    # avval o'yin sharhlari (gollar), keyin boshqa kanaldan, keyin eng yangisi
    fresh.sort(key=lambda v: (not HIGHLIGHT_PATTERN.search(v["title"]),
                              v["source"] == state.get("last_video_source"), -v["ts"]))
    if not fresh:
        return False
    video = fresh[0]
    # Telegram hozir YouTube havolasidan pleer yasay olmayapti (WebPageEmpty), shuning uchun post rasm bilan
    # chiqadi, video esa havola orqali ochiladi. Videoni yuklab qayta joylash mualliflik huquqini buzadi.
    image = await asyncio.to_thread(rasm.video_card, video["title"], video["source"])
    await client.send_file(channel, image, caption=format_video(video), parse_mode="html")
    state["seen"].append(video["id"])
    state["last_video_source"] = video["source"]
    save_state()
    log.info("Kanalga video joylandi: %s", video["title"][:60])
    return True


async def post_highlights(client: TelegramClient, channel) -> bool:
    """Kechagi o'yinlarning gollari: rasmiy kanallardagi so'nggi 30 soatlik sharh videolari."""
    min_ts = datetime.now().timestamp() - 30 * 3600
    seen = set(state["seen"])
    videos = [v for v in await asyncio.to_thread(fetch_videos)
              if v["ts"] >= min_ts and v["id"] not in seen and HIGHLIGHT_PATTERN.search(v["title"])]
    if len(videos) < 2:
        return False
    videos.sort(key=lambda v: -v["ts"])
    rows = [f'▶️ <a href="{html.escape(v["link"], quote=True)}">{html.escape(v["title"][:90])}</a>'
            for v in videos[:8]]
    text = ("🎬 <b>Kechagi o'yinlar gollari</b> 👇\n\n"
            + "\n\n".join(rows) + "\n\n#gollar #video" + f"\n\n⚽️ @{USERNAME} — futbol olami bir joyda")
    image = await asyncio.to_thread(rasm.video_card, "Kechagi o'yinlar gollari", "Rasmiy kanallar", len(rows))
    await client.send_file(channel, image, caption=text[:1024], parse_mode="html")
    state["seen"].extend(v["id"] for v in videos[:8])
    save_state()
    log.info("Kanalga kechagi gollar joylandi (%d ta video)", len(rows))
    return True


# ---------- Viktorina va so'rovnomalar ----------

def load_content() -> dict:
    return json.loads(CONTENT_FILE.read_text(encoding="utf-8"))


def pick(pool: list[dict], used_key: str) -> tuple[int, dict]:
    used = set(state[used_key])
    free = [i for i in range(len(pool)) if i not in used]
    if not free:  # hammasi ishlatilgan bo'lsa, boshidan
        state[used_key], free = [], list(range(len(pool)))
    idx = random.choice(free)
    state[used_key].append(idx)
    return idx, pool[idx]


def build_poll(question: str, options: list[str], quiz: bool, correct: int | None = None,
               solution: str | None = None) -> InputMediaPoll:
    # Telegram javob kalitini "0", "1", ... ko'rinishida kutadi (bytes([n]) da viktorina rad etiladi)
    answers = [PollAnswer(text=TextWithEntities(o, []), option=str(n).encode()) for n, o in enumerate(options)]
    poll = Poll(id=random.getrandbits(62), question=TextWithEntities(question, []),
                answers=answers, hash=0, quiz=quiz or None)
    return InputMediaPoll(poll=poll, correct_answers=[correct] if quiz else None,
                          solution=solution if quiz else None, solution_entities=[] if quiz and solution else None)


async def post_quiz(client: TelegramClient, channel) -> bool:
    _, q = pick(load_content()["viktorina"], "used_quiz")
    # sarlavha savolning ichida: alohida xabar bo'lsa, savol chiqmay qolganda "yetim" sarlavha qolardi
    question = f"🧠 Kun viktorinasi: {q['savol']}"[:255]
    await client.send_file(channel, build_poll(question, q["javoblar"], True, q["togri"], q.get("izoh")))
    save_state()
    log.info("Kanalga viktorina joylandi")
    return True


async def post_poll(client: TelegramClient, channel) -> bool:
    _, p = pick(load_content()["sorovnoma"], "used_poll")
    await client.send_file(channel, build_poll(p["savol"], p["javoblar"], False))
    save_state()
    log.info("Kanalga so'rovnoma joylandi")
    return True


# ---------- Statistika (statistika.py) ----------

async def send_stat(client: TelegramClient, channel, make, *args) -> bool:
    result = await asyncio.to_thread(make, *args)
    if not result:
        return False
    caption, media = result
    await send_media(client, channel, media, caption + channel_footer())
    log.info("Kanalga statistika joylandi: %s", make.__name__)
    return True


async def send_media(client: TelegramClient, channel, media, caption: str) -> None:
    """Rasm (BytesIO) yoki mobilograf videosi ((fayl, atributlar) juftligi)."""
    if isinstance(media, tuple):
        file, attributes = media
        await client.send_file(channel, file, caption=caption, parse_mode="html", attributes=attributes,
                               supports_streaming=True)
    else:
        await client.send_file(channel, media, caption=caption, parse_mode="html")


def next_league(key: str, leagues: list) -> tuple:
    """Har safar navbatdagi chempionat (APL -> La Liga -> Seriya A -> ...)."""
    idx = state.get(key, 0) % len(leagues)
    state[key] = idx + 1
    return leagues[idx]


async def post_results(client: TelegramClient, channel) -> bool:
    return await send_stat(client, channel, statistika.results_post)


async def post_fixtures(client: TelegramClient, channel) -> bool:
    return await send_stat(client, channel, statistika.fixtures_post)


async def post_table(client: TelegramClient, channel) -> bool:
    league = next_league("table_idx", statistika.LEAGUES + statistika.CUPS[:1])
    return await send_stat(client, channel, statistika.table_post, league)


async def post_scorers(client: TelegramClient, channel) -> bool:
    league = next_league("scorers_idx", statistika.LEAGUES)
    return await send_stat(client, channel, statistika.scorers_post, league)


# Soat -> nima joylanadi (birinchisi topilmasa, keyingisi). Qolgan soatlarda yangilik.
SCHEDULE = {
    9: [post_results, post_news],
    10: [post_fixtures, post_news],
    11: [post_video, post_news],
    QUIZ_HOUR: [post_quiz],
    15: [post_scorers, post_news],
    16: [post_video, post_news],
    17: [post_table, post_news],
    19: [post_video, post_news],
    POLL_HOUR: [post_poll],
    22: [post_video, post_news],
}
# Kuchli kanallar kabi: postlar kam, lekin sifatli - soatiga bitta (+ #MATCHDAY va jonli gollar).
# Yarim soatda faqat 9:30 - kechagi o'yinlar gollari.
HALF_SCHEDULE = {
    9: [post_highlights],
}


def hour_plan(hour: int, half: bool = False) -> list:
    if half:
        return HALF_SCHEDULE.get(hour, [])
    return SCHEDULE.get(hour, [post_news, post_video])


# ---------- Jonli gollar ----------

def score_card(ev: dict, score, label: str, goals: list, photo_of: str = "", highlight: str = ""):
    """Namunadagidek gol / yakun rasmi: orqada gol urgan futbolchi surati (bo'lmasa stadion), logotiplar bilan hisob."""
    cache = state.setdefault("foto_cache", {})
    found = foto.player_photo(photo_of, cache) if photo_of else None
    if found:
        photo, credit = found[0], f"Foto: {found[1]['author']} · {found[1]['license']} · Wikimedia"
    else:
        st = foto.stadium_photo(ev.get("home_full") or ev["home"], cache)
        photo, credit = (st[0], st[1]) if st else (None, "")
    scorers = [(g["scorer"] + (" (a/g)" if g["og"] else " (p)" if g["pen"] else ""), g["minute"])
               for g in goals if g.get("scorer")]
    return rasm.score_photo_card(photo, foto.logo(ev.get("home_logo", "")), foto.logo(ev.get("away_logo", "")),
                                 uz_team(ev["home"]), uz_team(ev["away"]), score[0], score[1], label, scorers,
                                 credit, highlight)


async def send_goal(client: TelegramClient, channel, ev: dict, goal: dict | None, home_id: str, score: list) -> None:
    team = ev["home"] if home_id == ev["home_id"] else ev["away"]
    minute = goal["minute"] if goal else ev["clock"]
    upto = ev["goals"][: ev["goals"].index(goal) + 1] if goal in ev["goals"] else ev["goals"]
    name = goal["scorer"] if goal else ""
    image = await asyncio.to_thread(score_card, ev, score, f"GOOOL! {minute}", upto, name, name)
    caption = statistika.goal_post(ev, goal, score[0], score[1], team, minute)
    await client.send_file(channel, image, caption=caption + channel_footer(), parse_mode="html")
    log.info("GOL: %s %s:%s %s (%s)", ev["home"], score[0], score[1], ev["away"], minute)


async def post_score_change(client: TelegramClient, channel, ev: dict, info: dict, cur: tuple) -> None:
    """Hisob o'zgarishiga qarab gol yoki "gol bekor qilindi (VAR)" xabarini joylaydi.

    Faqat hisobning o'zi oshsa gol e'lon qilinadi: ESPN gol daqiqasini tuzatsa ham soxta xabar chiqmaydi.
    """
    prev = list(info.get("score") or cur)  # eski holat yozuvlarida "score" yo'q - qayta yubormaslik uchun
    new = [g for g in ev["goals"] if g["key"] not in info["goals"]]
    diff = sum(cur) - sum(prev)
    if diff > 0:
        if len(new) < diff and info.get("lag", 0) < 2:
            info["lag"] = info.get("lag", 0) + 1  # hisob yangilandi, gol muallifi hali kelmadi - biroz kutamiz
            return
        score = prev[:]
        for goal in new[-diff:]:
            side = 0 if goal["team"] == ev["home_id"] else 1
            score[side] += 1
            await send_goal(client, channel, ev, goal, goal["team"], score)
        for side in (0, 1):  # muallifi noma'lum qolgan gollar
            while score[side] < cur[side]:
                score[side] += 1
                await send_goal(client, channel, ev, None, ev["home_id"] if side == 0 else ev["away_id"], score)
    elif diff < 0:
        side = 0 if cur[0] < prev[0] else 1
        team = ev["home"] if side == 0 else ev["away"]
        image = rasm.goal_card(ev["league"][1], ev["home"], ev["away"], cur[0], cur[1], ev["clock"],
                               f"{team} goli hisobga olinmadi", word="GOL BEKOR", color=rasm.RED)
        await client.send_file(channel, image, caption=statistika.cancel_post(ev, team, cur) + channel_footer(),
                               parse_mode="html")
        log.info("VAR: %s goli bekor qilindi, %s %s:%s %s", team, ev["home"], cur[0], cur[1], ev["away"])
    info["goals"] = sorted(set(info["goals"]) | {g["key"] for g in ev["goals"]})
    info["score"], info["lag"] = list(cur), 0
    save_state()


MATCHDAY_PER_DAY = 3
# ESPN inglizcha nomlari -> o'zbekcha yozilishi
UZ_TEAMS = {"Bayern Munich": "Bavariya", "Barcelona": "Barselona", "Manchester City": "Manchester Siti",
            "Manchester United": "Manchester Yunayted", "Man United": "Manchester Yunayted", "Man City": "Manchester Siti",
            "Liverpool": "Liverpul", "Chelsea": "Chelsi", "Juventus": "Yuventus", "Paris Saint-Germain": "PSJ",
            "PSG": "PSJ", "Atlético Madrid": "Atletiko", "Atletico Madrid": "Atletiko", "Borussia Dortmund": "Borussiya D",
            "Dortmund": "Borussiya D", "Internazionale": "Inter", "Inter Milan": "Inter", "Tottenham Hotspur": "Tottenhem",
            "Spurs": "Tottenhem", "Bayer Leverkusen": "Bayer", "Leverkusen": "Bayer", "Newcastle United": "Nyukasl",
            "Newcastle": "Nyukasl", "Marseille": "Marsel", "AS Roma": "Roma", "Sevilla": "Sevilya",
            "Man Utd": "Manchester Yunayted", "Bournemouth": "Bornmut", "Nottm Forest": "Nottingem", "Wolves": "Vulvz",
            "Brighton": "Brayton", "Atlético": "Atletiko", "Bayern": "Bavariya", "AC Milan": "Milan", "Lazio": "Latsio",
            "Real Sociedad": "Real Sosyedad", "Villarreal": "Vilyarreal", "Valencia": "Valensiya", "Leipzig": "Leypsig",
            "RB Leipzig": "Leypsig", "Frankfurt": "Ayntraxt", "Monaco": "Monako", "Lyon": "Lion", "Lille": "Lill"}


def uz_team(name: str) -> str:
    return UZ_TEAMS.get(name, name)


def poster(ev: dict, bottom1: str, bottom2: str):
    """El Klasiko afishasidagidek: stadion foni, logotiplar, ikki jamoaning eng yaxshi to'purarlari, oltin sarlavha."""
    cache = state.setdefault("foto_cache", {})
    code = ev["league"][0]
    credits, photos = [], []
    for team_id in (ev["home_id"], ev["away_id"]):
        found = foto.player_photo(statistika.team_star(code, team_id) or "", cache)
        photos.append(found[0] if found else None)
        if found:
            credits.append(f"{found[1]['author']} ({found[1]['license']})")
    st = foto.stadium_photo(ev.get("home_full") or ev["home"], cache)
    if st:
        credits.insert(0, st[1].replace("Foto: ", "").replace(" · Wikimedia", ""))
    home, away = uz_team(ev["home"]), uz_team(ev["away"])
    title = statistika.match_title(ev, home, away)
    league = ev["league"][1]
    return rasm.matchday_poster(st[0] if st else None, foto.logo(ev.get("home_logo", "")), foto.logo(ev.get("away_logo", "")),
                                photos[0], photos[1], title, league, bottom1, bottom2,
                                ("Foto: Wikimedia Commons — " + ", ".join(credits)) if credits else "")


async def post_kickoff(client: TelegramClient, channel, ev: dict) -> None:
    """#MATCHDAY qilingan o'yin boshlanganda "UCHRASHUV BOSHLANDI" afishasi (bir marta)."""
    started = state.setdefault("kickoff", {})
    if ev["id"] in started or ev["id"] not in state.get("matchday", {}):
        return
    minute = int(re.sub(r"\D", "", ev["clock"] or "0") or 0)
    started[ev["id"]] = today()
    save_state()
    if minute > 20:  # kech ko'rdik - afisha endi kerak emas
        return
    image = await asyncio.to_thread(poster, ev, "Uchrashuv boshlandi", "Gollar kanalda jonli ⚡")
    _, league, flag, _ = ev["league"]
    caption = (f"🟢 <b>UCHRASHUV BOSHLANDI!</b>\n\n{flag} {html.escape(league)}\n"
               f"⚔️ <b>«{html.escape(uz_team(ev['home']))}» vs «{html.escape(uz_team(ev['away']))}»</b>\n\n"
               f"Gollar shu yerda jonli chiqadi ⚡️\n\n⚽️ @{USERNAME} — futbol olami bir joyda")
    await client.send_file(channel, image, caption=caption, parse_mode="html")
    for key in list(started)[:-50]:
        del started[key]
    log.info("Uchrashuv boshlandi: %s - %s", ev["home"], ev["away"])


async def post_matchday(client: TelegramClient, channel, events: list) -> None:
    """Katta o'yindan 2,5-3,5 soat oldin #MATCHDAY posti: stadion surati, juftlik va boshlanish vaqti."""
    done = state.setdefault("matchday", {})
    day = today()
    now = datetime.now().astimezone()
    for ev in sorted(events, key=lambda e: e["kickoff"]):
        if sum(1 for d in done.values() if d == day) >= MATCHDAY_PER_DAY:
            break
        left = ev["kickoff"] - now
        if ev["state"] != "pre" or ev["id"] in done or not timedelta(hours=2, minutes=30) <= left <= timedelta(hours=3, minutes=30):
            continue
        if not (statistika.is_big(ev.get("home_full", ev["home"])) or statistika.is_big(ev.get("away_full", ev["away"]))):
            continue
        _, league, flag, tag = ev["league"]
        when = ev["kickoff"].astimezone(statistika.TASHKENT).strftime("%H:%M")
        home, away = uz_team(ev["home"]), uz_team(ev["away"])
        title = f"{home} vs {away}"
        image = await asyncio.to_thread(poster, ev, f"Bugun {when}", "Toshkent vaqti bilan")
        caption = (f"🔥 #MATCHDAY\n\n{flag} {html.escape(league)}\n\n"
                   f"⚔️ <b>«{html.escape(home)}» vs «{html.escape(away)}»</b>\n\n"
                   f"🗓 Bugun | {when}\n\nKim yutadi? Izohlarda yozing 👇\n\n⚽️ @{USERNAME} — futbol olami bir joyda")
        await client.send_file(channel, image, caption=caption, parse_mode="html")
        done[ev["id"]] = day
        save_state()
        log.info("MATCHDAY: %s", title)
    for key in list(done)[:-50]:
        del done[key]


async def post_live(client: TelegramClient, channel) -> bool:
    """Muhim o'yinlardagi yangi gollar va yakuniy hisobni joylaydi. Jonli o'yin bormi - qaytaradi."""
    events = await asyncio.to_thread(statistika.live_events)
    if DAY_START <= datetime.now().hour < DAY_END:
        try:
            await post_matchday(client, channel, events)
        except Exception as e:
            log.error("MATCHDAY xatosi: %r", e)
    live = state.setdefault("live", {})
    now = datetime.now().astimezone()
    active = False
    for ev in events:
        if ev["state"] == "in" or (ev["state"] == "pre" and ev["kickoff"] - now < timedelta(minutes=15)):
            active = True
        if ev["state"] == "pre":
            continue
        if ev["state"] == "in":
            try:
                await post_kickoff(client, channel, ev)
            except Exception as e:
                log.error("Boshlanish afishasi xatosi: %r", e)
        cur = (int(ev["hs"] or 0), int(ev["as"] or 0))
        info = live.get(ev["id"])
        if info is None:
            # birinchi marta ko'ryapmiz: eski gollarni qayta yubormaymiz, oxirgi 10 daqiqadagisi bundan mustasno
            last_sec = max((g["sec"] for g in ev["goals"]), default=0)
            old = [g for g in ev["goals"] if g["sec"] < last_sec - 600 or ev["state"] == "post"]
            info = live[ev["id"]] = {
                "goals": [g["key"] for g in old], "ft": ev["state"] == "post",
                "score": [sum(g["team"] == ev["home_id"] for g in old), sum(g["team"] == ev["away_id"] for g in old)]}
        await post_score_change(client, channel, ev, info, cur)
        if ev["state"] == "post" and not info["ft"]:
            # orqa fonga g'olib jamoaning eng ko'p gol urgan futbolchisi (durangda - oxirgi gol muallifi)
            winner_id = ev["home_id"] if cur[0] > cur[1] else ev["away_id"] if cur[1] > cur[0] else None
            pool = [g["scorer"] for g in ev["goals"] if g["scorer"] and not g["og"] and (winner_id is None or g["team"] == winner_id)]
            hero = max(set(pool), key=pool.count) if pool else ""
            image = await asyncio.to_thread(score_card, ev, cur, "O'YIN YAKUNI", ev["goals"], hero)
            media = image
            # slaydli videolar o'chirilgan (egasi xohlamadi) - faqat rasm
            await send_media(client, channel, media, statistika.final_post(ev)[:1000] + channel_footer())
            info["ft"] = True
            save_state()
            log.info("Yakuniy hisob: %s %s:%s %s", ev["home"], ev["hs"], ev["as"], ev["away"])
    # eski o'yinlarni unutamiz
    for key in list(live)[:-80]:
        del live[key]
    return active


# ---------- Kanalni yaratish ----------

WELCOME = (
    "👋 <b>{title} kanaliga xush kelibsiz!</b>\n\n"
    "Bu yerda sizni kutmoqda:\n"
    "⚡️ Futbol olamining eng tezkor yangiliklari — har 30 daqiqada\n"
    "🔴 <b>Jonli gollar</b>: Chempionlar ligasi va katta klublar o'yinlari\n"
    "🏁 O'yin yakuni, kechagi natijalar va bugungi o'yinlar\n"
    "📊 Jadvallar va to'purarlar TOP 10\n"
    "🎬 9:30 da kechagi o'yinlar gollari videolari\n"
    "🧠 13:00 da viktorina, 📊 20:00 da so'rovnoma\n\n"
    "💬 Har bir post ostida <b>izoh qoldiring</b> — fikringiz muhim!\n"
    "🔔 Bildirishnomalarni yoqib qo'ying — eng muhim golni o'tkazib yubormang!\n"
    "🤝 Futbolni sevadigan do'stlaringizni ham taklif qiling."
)


async def ensure_channel(client: TelegramClient):
    if state.get("channel_id"):
        return await client.get_entity(state["channel_id"])

    if EXISTING:
        channel = await client.get_entity(USERNAME)
        state["channel_id"] = channel.id
        save_state()
        log.info("Mavjud kanalga ulandi: %s", getattr(channel, "title", USERNAME))
    else:
        result = await client(CreateChannelRequest(title=TITLE, about=ABOUT, broadcast=True))
        channel = result.chats[0]
        state["channel_id"] = channel.id
        save_state()
        log.info("Kanal yaratildi: %s", TITLE)

    if USERNAME and not EXISTING:
        try:
            await client(UpdateUsernameRequest(channel, USERNAME))
            log.info("Kanal havolasi: https://t.me/%s", USERNAME)
        except RPCError as e:
            log.warning("@%s havolasini qo'yib bo'lmadi (%s). Kanal yopiq bo'lib qoldi, "
                        "havolani Telegramda o'zingiz qo'ying.", USERNAME, e.__class__.__name__)

    msg = await client.send_message(channel, WELCOME.format(title=html.escape(TITLE)) + channel_footer(),
                                    parse_mode="html")
    await client.pin_message(channel, msg, notify=False)
    return channel


async def tick(client: TelegramClient, channel) -> None:
    """Shu yarim soatning posti hali joylanmagan bo'lsa, joylaydi."""
    now = datetime.now()
    half = now.minute >= 30
    slot = f"{today()} {now.hour}:{'30' if half else '00'}"
    if not (DAY_START <= now.hour < DAY_END) or state.get("last_slot") == slot:
        return
    try:
        for step in hour_plan(now.hour, half):
            if await step(client, channel):
                break
        state["last_slot"] = slot  # har 30 daqiqada bitta post
        save_state()
    except Exception as e:  # xato bo'lsa, shu soat ichida yana urinib ko'riladi
        log.error("Kanal xatosi: %r", e)


async def run(client: TelegramClient) -> None:
    """agent.py dan fon vazifasi sifatida chaqiriladi."""
    load_state()
    channel = await ensure_channel(client)
    log.info("Kanal ishlayapti: %s", TITLE)
    while True:
        await tick(client, channel)
        await asyncio.sleep(CHECK_EVERY_SECONDS)


async def run_once(client: TelegramClient) -> None:
    """Bulutda (GitHub Actions) bir marta ishga tushirish uchun: kanal havola orqali topiladi."""
    load_state()
    channel = await client.get_entity(USERNAME)
    log.info("Kanal topildi: %s (soat %s)", channel.title, datetime.now().strftime("%H:%M"))
    # GitHub jadvali ishga tushishlarni tez-tez o'tkazib yuboradi, shuning uchun har bir ishga tushish
    # ~25 daqiqa ishlaydi: har daqiqada post jadvali va jonli gollar tekshiriladi. Keyingisi navbatda kutib turadi.
    # Kunduzgi soatlardan tashqarida va jonli o'yin bo'lmasa - darhol tugaydi.
    deadline = datetime.now() + timedelta(seconds=RUN_WINDOW_SECONDS)
    while True:
        if not client.is_connected():  # Telegram aloqasi uzildi (masalan, kirish bekor qilingan) - kutmasdan chiqamiz
            raise SystemExit("Telegram bilan aloqa uzildi")
        await tick(client, channel)
        try:
            active = await post_live(client, channel)
        except Exception as e:
            log.error("Jonli gollar xatosi: %r", e)
            active = False
        try:
            await moderator.moderate(client, channel, state, USERNAME)
            save_state()
        except Exception as e:
            log.error("Moderator xatosi: %r", e)
        daytime = DAY_START <= datetime.now().hour < DAY_END
        if not (active or daytime) or datetime.now() + timedelta(seconds=LIVE_POLL_SECONDS) > deadline:
            break
        await asyncio.sleep(LIVE_POLL_SECONDS)
