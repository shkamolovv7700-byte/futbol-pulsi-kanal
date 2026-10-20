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

import statistika

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
LIVE_WINDOW_SECONDS = 9 * 60

log = logging.getLogger("kanal")

# Faqat futbolga oid yangiliklarni o'tkazish uchun so'z boshlanmalari.
# O'zbekchada qo'shimcha so'z oxiriga qo'shiladi ("Milanga"), shuning uchun faqat boshini tekshiramiz.
# "Inter", "Roma", "Real" oddiy so'zlarga o'xshagani uchun faqat qo'shtirnoq ichida hisoblanadi.
FOOTBALL_PATTERN = re.compile(
    r"\b(futbol|gol(?!f|land)|liga|apl\b|premer|bundesliga|la ?liga|chempionlar|uefa|fifa|superliga"
    r"|terma jamoa|transfer|murabbiy|darvozabon|penalti|assist|jahon chempionati|osiyo kubogi"
    r"|barselona|barsa|psj\b|manchester|liverpul|arsenal|chelsi|tottenhem|bavariya|borussiya|yuventus|milan"
    r"|napoli|atletiko|paxtakor|nasaf|navbahor|neftchi|bunyodkor|messi|ronaldu|mbappe|xolann"
    r"|shomurodov|xusanov|fayzullayev)"
    r"|[«\"“](inter|roma|real|psj|lokomotiv|andijon)",
    re.IGNORECASE,
)
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
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "").strip()
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
    return bool(FOOTBALL_PATTERN.search(f"{item['title']} {item['desc']}"))


def make_hashtags(item: dict) -> str:
    text = f"{item['title']} {item['desc']}".lower()
    tags = [tag for tag, words in HASHTAGS.items() if any(w in text for w in words)]
    return " ".join(["#futbol", *tags[:2]])


def channel_footer() -> str:
    return f"\n\n📲 @{USERNAME}" if USERNAME else ""


def format_news(item: dict) -> str:
    parts = [f"⚽️ <b>{html.escape(item['title'])}</b>"]
    if item["desc"] and item["desc"].lower() not in item["title"].lower():
        parts.append(html.escape(item["desc"]))
    parts.append(random.choice(HOOKS))
    parts.append(make_hashtags(item))
    # manba kichik havola bilan ko'rsatiladi (katta saytlar kartochkasi reklamaga o'xshab qolmasin)
    source = f'<i>Manba: <a href="{html.escape(item["link"], quote=True)}">{item["source"]}</a></i>'
    return "\n\n".join(parts) + channel_footer() + "\n" + source


def is_ad(item: dict) -> bool:
    return bool(AD_PATTERN.search(f"{item['title']} {item['desc']}"))


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


async def post_news(client: TelegramClient, channel) -> bool:
    items = await asyncio.to_thread(fetch_all_news)

    seen = set(state["seen"])
    min_ts = datetime.now().timestamp() - NEWS_MAX_AGE_HOURS * 3600
    item = None
    # eng yangisi birinchi: soatiga bitta post, shuning uchun eskirganidan ko'ra tezkorini afzal ko'ramiz
    for i in sorted(items, key=lambda i: i["ts"], reverse=True):
        if i["id"] in seen:
            continue
        seen.add(i["id"])
        state["seen"].append(i["id"])
        if i["ts"] >= min_ts and is_football(i) and not is_ad(i) and not is_duplicate(i["title"]):
            item = i
            break
    if not item:
        save_state()
        return False

    await client.send_message(channel, format_news(item), parse_mode="html", link_preview=False)
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
    return (
        f"🎬 <b>Futbol videosi</b>\n\n"
        f"{html.escape(video['title'])}\n\n"
        f'▶️ <a href="{html.escape(video["link"], quote=True)}">Ko\'rish — {html.escape(video["source"])}</a>\n\n'
        f"Yoqdimi? 🔥 bosing!\n\n#video #futbol"
        + channel_footer()
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
    await client.send_message(channel, format_video(video), parse_mode="html", link_preview=True)
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
    text = ("🎬 <b>Kechagi o'yinlar gollari</b>\n\nBarcha gollar va eng qiziq lahzalar 👇\n\n"
            + "\n\n".join(rows) + "\n\nQaysi gol eng zo'ri? 🔥\n\n#gollar #video" + channel_footer())
    await client.send_message(channel, text, parse_mode="html", link_preview=False)
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
    answers = [PollAnswer(text=TextWithEntities(o, []), option=bytes([n])) for n, o in enumerate(options)]
    poll = Poll(id=random.getrandbits(62), question=TextWithEntities(question, []),
                answers=answers, hash=0, quiz=quiz or None)
    return InputMediaPoll(poll=poll, correct_answers=[correct] if quiz else None,
                          solution=solution if quiz else None, solution_entities=[] if quiz and solution else None)


async def post_quiz(client: TelegramClient, channel) -> bool:
    _, q = pick(load_content()["viktorina"], "used_quiz")
    await client.send_message(channel, "🧠 <b>Kun viktorinasi!</b> Bilimingizni sinab ko'ring 👇",
                              parse_mode="html")
    await client.send_file(channel, build_poll(q["savol"], q["javoblar"], True, q["togri"], q.get("izoh")))
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
    text = await asyncio.to_thread(make, *args)
    if not text:
        return False
    await client.send_message(channel, text + channel_footer(), parse_mode="html", link_preview=False)
    log.info("Kanalga statistika joylandi: %s", make.__name__)
    return True


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
# Har soatning yarmida (:30) - yangilik; 9:30 da kechagi o'yinlar gollari
HALF_SCHEDULE = {
    9: [post_highlights, post_news, post_video],
}


def hour_plan(hour: int, half: bool = False) -> list:
    if half:
        return HALF_SCHEDULE.get(hour, [post_news, post_video])
    return SCHEDULE.get(hour, [post_news, post_video])


# ---------- Jonli gollar ----------

async def post_live(client: TelegramClient, channel) -> bool:
    """Muhim o'yinlardagi yangi gollar va yakuniy hisobni joylaydi. Jonli o'yin bormi - qaytaradi."""
    events = await asyncio.to_thread(statistika.live_events)
    live = state.setdefault("live", {})
    now = datetime.now().astimezone()
    active = False
    for ev in events:
        if ev["state"] == "in" or (ev["state"] == "pre" and ev["kickoff"] - now < timedelta(minutes=15)):
            active = True
        if ev["state"] == "pre":
            continue
        info = live.get(ev["id"])
        if info is None:
            # birinchi marta ko'ryapmiz: eski gollarni qayta yubormaymiz, oxirgi 10 daqiqadagisi bundan mustasno
            last_sec = max((g["sec"] for g in ev["goals"]), default=0)
            info = live[ev["id"]] = {"goals": [g["key"] for g in ev["goals"] if g["sec"] < last_sec - 600],
                                     "ft": ev["state"] == "post"}
        for goal in ev["goals"]:
            if goal["key"] in info["goals"]:
                continue
            await client.send_message(channel, statistika.goal_post(ev, goal) + channel_footer(), parse_mode="html")
            info["goals"].append(goal["key"])
            save_state()
            log.info("GOL: %s %s:%s %s (%s)", ev["home"], ev["hs"], ev["as"], ev["away"], goal["minute"])
        if ev["state"] == "post" and not info["ft"]:
            await client.send_message(channel, statistika.final_post(ev) + channel_footer(), parse_mode="html")
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
    "⚡️ Futbol olamining eng tezkor yangiliklari\n"
    "🏆 APL, La Liga, Seriya A, Bundesliga, Chempionlar ligasi\n"
    "🇺🇿 O'zbek futboli va legionerlarimiz\n"
    "🧠 Har kuni soat 13:00 da viktorina\n"
    "📊 Har kuni soat 20:00 da so'rovnoma\n\n"
    "🔔 Bildirishnomalarni yoqib qo'ying — eng muhim xabarni o'tkazib yubormang!\n"
    "📲 Futbolni sevadigan do'stlaringizni ham taklif qiling."
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
    # Jonli o'yin bo'lsa, ~9 daqiqa davomida har daqiqada gollarni tekshiramiz (keyingi ishga tushish davom ettiradi)
    deadline = datetime.now() + timedelta(seconds=LIVE_WINDOW_SECONDS)
    while True:
        await tick(client, channel)
        try:
            active = await post_live(client, channel)
        except Exception as e:
            log.error("Jonli gollar xatosi: %r", e)
            active = False
        if not active or datetime.now() + timedelta(seconds=LIVE_POLL_SECONDS) > deadline:
            break
        await asyncio.sleep(LIVE_POLL_SECONDS)
