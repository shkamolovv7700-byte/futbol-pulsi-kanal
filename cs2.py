"""
"CS2 Pulsi 🎯" kanali (t.me/cs2pulsi_uz): bulutda (GitHub Actions) ishlaydi, kanal egasi - ikkinchi akkaunt.

Har 30 daqiqada bitta post (8:00-23:00):
  9:00 kechagi natijalar, 10:00 bugungi o'yinlar, 13:00 viktorina, 17:00 Valve reytingi (dushanba) /
  yangilik, 20:00 so'rovnoma, 11/16/19/22 video, qolgan vaqtda o'zbekchaga tarjima qilingan yangiliklar.
Jonli: muhim o'yinlarda har bir xarita yutilganda va o'yin tugaganda xabar.
Muhokama guruhi moderatsiyasi - moderator.py.
"""

import asyncio
import html
import json
import logging
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from telethon import TelegramClient

import cs2data
import kanal  # umumiy yordamchilar: build_poll, fetch_xml, parse_date, ATOM/YT
import moderator
import rasm

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "cs2_state.json"
CONTENT_FILE = BASE_DIR / "cs2_kontent.json"
USERNAME = "cs2pulsi_uz"
DAY_START, DAY_END = 8, 23
POLL_SECONDS = 60
RUN_WINDOW_SECONDS = 25 * 60
NEWS_MAX_AGE_HOURS = 12

VIDEO_FEEDS = [
    ("ESL Counter-Strike", "UCPq2ETz4aAGo2Z-8JisDPIA"),
    ("ESL CS Highlights", "UCDQZcZZwv-RhxHxJpHCdmbQ"),
    ("HLTV", "UCXbsUubmNPK8XJFbkmlMcMg"),
]
HIGHLIGHT = re.compile(r"highlight|best|ace|clutch|moments|plays|top \d+|insane|1v\d", re.IGNORECASE)
HOOKS = ["Sizningcha, kim haq? Izohlarda yozing 👇", "Fikringizni izohlarda qoldiring 💬",
         "🔥 — zo'r, 🤔 — ko'ramiz, 👎 — yoqmadi", "Do'stlaringizga ham ulashing 📲"]

log = logging.getLogger("cs2")
state: dict = {}


def load_state() -> None:
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    for key in ("seen", "used_quiz", "used_poll"):
        state.setdefault(key, [])


def save_state() -> None:
    state["seen"] = state["seen"][-1500:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def footer() -> str:
    return f"\n\n📲 @{USERNAME}"


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


async def send_card(client, channel, image, caption: str) -> None:
    await client.send_file(channel, image, caption=caption[:1000] + footer(), parse_mode="html")


# ---------- Yangiliklar ----------

async def post_news(client, channel) -> bool:
    items = await asyncio.to_thread(cs2data.fetch_news)
    seen = set(state["seen"])
    min_ts = datetime.now().timestamp() - NEWS_MAX_AGE_HOURS * 3600
    for item in sorted(items, key=lambda i: -i["ts"]):
        if item["id"] in seen:
            continue
        state["seen"].append(item["id"])
        if item["ts"] < min_ts:
            continue
        title = await asyncio.to_thread(cs2data.translate, item["title"])
        if not title:  # tarjima ishlamadi - keyinroq urinib ko'ramiz
            state["seen"].pop()
            save_state()
            return False
        desc = await asyncio.to_thread(cs2data.translate, item["desc"]) if item["desc"] else ""
        image = await asyncio.to_thread(rasm.news_card, title, desc or "", "Cybersport.ru")
        caption = (f"🎯 <b>{html.escape(title)}</b>\n\n" + (f"{html.escape(desc)}\n\n" if desc else "")
                   + f"{random.choice(HOOKS)}\n\n#CS2 #yangilik"
                   + f"\n<i>Manba: <a href=\"{html.escape(item['link'], quote=True)}\">Cybersport.ru</a></i>")
        await send_card(client, channel, image, caption)
        save_state()
        log.info("CS2 yangilik: %s", title[:60])
        return True
    save_state()
    return False


# ---------- Videolar ----------

def fetch_videos() -> list[dict]:
    old = kanal.VIDEO_FEEDS
    kanal.VIDEO_FEEDS = VIDEO_FEEDS
    try:
        return kanal.fetch_videos()
    finally:
        kanal.VIDEO_FEEDS = old


async def post_video(client, channel) -> bool:
    seen = set(state["seen"])
    min_ts = datetime.now().timestamp() - 3 * 86400
    videos = [v for v in await asyncio.to_thread(fetch_videos) if v["id"] not in seen and v["ts"] >= min_ts]
    videos.sort(key=lambda v: (not HIGHLIGHT.search(v["title"]), v["source"] == state.get("last_video_source"), -v["ts"]))
    if not videos:
        return False
    v = videos[0]
    image = await asyncio.to_thread(rasm.video_card, v["title"], v["source"])
    caption = (f"🎬 <b>CS2 video</b>\n\n{html.escape(v['title'])}\n\n"
               f'▶️ <a href="{html.escape(v["link"], quote=True)}">Ko\'rish — {html.escape(v["source"])}</a>\n\n'
               "Yoqdimi? 🔥 bosing!\n\n#CS2 #video")
    await send_card(client, channel, image, caption)
    state["seen"].append(v["id"])
    state["last_video_source"] = v["source"]
    save_state()
    return True


# ---------- O'yinlar, natijalar, reyting ----------

def day_window(days_back: int) -> tuple[datetime, datetime]:
    now = datetime.now(cs2data.TASHKENT)
    start = now.replace(hour=6, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    if now.hour < 6:
        start -= timedelta(days=1)
    return start, start + timedelta(days=1)


def important_matches() -> list[dict]:
    tops = cs2data.top_teams()
    return [m for m in cs2data.matches() if cs2data.is_important(m, tops)]


def group_by_tournament(ms: list[dict], mid) -> list[tuple]:
    blocks: dict[str, list] = {}
    for m in sorted(ms, key=lambda m: m["when"]):
        blocks.setdefault(m["tournament"] or "Turnir", []).append((m["team1"], mid(m), m["team2"]))
    return [(name, rows[:8]) for name, rows in list(blocks.items())[:5]]


async def post_fixtures(client, channel) -> bool:
    start, end = day_window(0)
    ms = [m for m in await asyncio.to_thread(important_matches)
          if not m["finished"] and start <= m["when"] < end]
    if not ms:
        return False
    blocks = group_by_tournament(ms, lambda m: m["when"].strftime("%H:%M"))
    image = await asyncio.to_thread(rasm.matches_card, "Bugungi o'yinlar", blocks, "Bugungi o'yinlar")
    await send_card(client, channel, image, f"📅 <b>Bugungi CS2 o'yinlari</b> — {len(ms)} ta muhim o'yin "
                    "<i>(Toshkent vaqti)</i>\n\nKimga ishonasiz? Izohlarda yozing 👇\n\n#CS2 #oyinlar")
    return True


async def post_results(client, channel) -> bool:
    start, end = day_window(1)
    ms = [m for m in await asyncio.to_thread(important_matches) if m["finished"] and start <= m["when"] < end]
    if not ms:
        return False
    blocks = group_by_tournament(ms, lambda m: f"{m['s1']} : {m['s2']}")
    image = await asyncio.to_thread(rasm.matches_card, "Kechagi natijalar", blocks, "Natijalar")
    await send_card(client, channel, image, "✅ <b>Kechagi CS2 natijalari</b>\n\n"
                    "Qaysi natija kutilmagan bo'ldi? 👇\n\n#CS2 #natijalar")
    return True


async def post_ranking(client, channel) -> bool:
    date, rows = await asyncio.to_thread(cs2data.ranking)
    # yangi reyting chiqqanda yoki dushanba kunlari
    if state.get("ranking_posted") == date and datetime.now().weekday() != 0:
        return False
    if state.get("ranking_day") == today():
        return False
    image = await asyncio.to_thread(
        rasm.scorers_card, f"Valve reytingi — TOP 10 ({date})",
        [(r["rank"], r["team"], r["roster"], r["points"]) for r in rows[:10]], "ochko", "REYTING")
    lead = rows[0]
    caption = (f"🏆 <b>CS2 jamoalar reytingi — TOP 10</b> <i>({date}, Valve)</i>\n\n"
               f"🥇 Peshqadam: <b>{html.escape(lead['team'])}</b> — {lead['points']} ochko\n"
               f"👥 Tarkib: {html.escape(lead['roster'])}\n\nSizning sevimli jamoangiz nechanchi? 👇\n\n#CS2 #reyting")
    await send_card(client, channel, image, caption)
    state["ranking_posted"], state["ranking_day"] = date, today()
    save_state()
    return True


# ---------- Viktorina va so'rovnoma ----------

def pick(pool: list, key: str) -> dict:
    free = [i for i in range(len(pool)) if i not in state[key]] or list(range(len(pool)))
    if len(free) == len(pool):
        state[key] = []
    idx = random.choice(free)
    state[key].append(idx)
    return pool[idx]


async def post_quiz(client, channel) -> bool:
    q = pick(json.loads(CONTENT_FILE.read_text(encoding="utf-8"))["viktorina"], "used_quiz")
    await client.send_file(channel, kanal.build_poll(f"🧠 CS2 viktorinasi: {q['savol']}"[:255], q["javoblar"],
                                                     True, q["togri"], q.get("izoh")))
    save_state()
    return True


async def post_poll(client, channel) -> bool:
    p = pick(json.loads(CONTENT_FILE.read_text(encoding="utf-8"))["sorovnoma"], "used_poll")
    await client.send_file(channel, kanal.build_poll(p["savol"], p["javoblar"], False))
    save_state()
    return True


# ---------- Jonli: xaritalar va o'yin yakuni ----------

async def post_live(client, channel) -> bool:
    ms = await asyncio.to_thread(important_matches)
    live = state.setdefault("live", {})
    now = datetime.now(cs2data.TASHKENT)
    active = False
    for m in ms:
        if cs2data.is_live(m) or (not m["finished"] and timedelta(0) <= m["when"] - now < timedelta(minutes=15)):
            active = True
        if not (cs2data.is_live(m) or m["finished"]):
            continue
        info = live.get(m["id"])
        score = [int(m["s1"] or 0), int(m["s2"] or 0)]
        if info is None:  # birinchi marta ko'ryapmiz: o'tib ketgan xaritalar haqida yozmaymiz
            info = live[m["id"]] = {"score": score, "done": m["finished"]}
            continue
        if score != info["score"] and not m["finished"] and sum(score) > sum(info["score"]):
            winner = m["team1"] if score[0] > info["score"][0] else m["team2"]
            image = await asyncio.to_thread(rasm.goal_card, m["tournament"], m["team1"], m["team2"], score[0],
                                            score[1], f"{m['bo']} · {sum(score)}-xarita", f"{winner} xaritani yutdi",
                                            word="XARITA!")
            await send_card(client, channel, image,
                            f"🗺 <b>{html.escape(winner)} xaritani yutdi!</b>\n\n🏆 {html.escape(m['tournament'])}\n"
                            f"<b>{html.escape(m['team1'])} {score[0]}:{score[1]} {html.escape(m['team2'])}</b> ({m['bo']})"
                            "\n\n#CS2 #jonli")
            info["score"] = score
        if m["finished"] and not info["done"]:
            winner = m["team1"] if score[0] > score[1] else m["team2"]
            image = await asyncio.to_thread(rasm.final_card, m["tournament"], m["team1"], m["team2"],
                                            score[0], score[1], [f"G'olib: {winner}", m["bo"]])
            await send_card(client, channel, image,
                            f"🏁 <b>O'yin yakunlandi: {html.escape(winner)} g'alaba qozondi!</b>\n\n"
                            f"🏆 {html.escape(m['tournament'])}\n<b>{html.escape(m['team1'])} {score[0]}:{score[1]} "
                            f"{html.escape(m['team2'])}</b>\n\nNatija sizga yoqdimi? 👇\n\n#CS2 #natija")
            info.update(score=score, done=True)
        save_state()
    for key in list(live)[:-150]:
        del live[key]
    return active


# ---------- Jadval ----------

SCHEDULE = {
    9: [post_results, post_news], 10: [post_fixtures, post_news], 11: [post_video, post_news],
    13: [post_quiz], 16: [post_video, post_news], 17: [post_ranking, post_news],
    19: [post_video, post_news], 20: [post_poll], 22: [post_video, post_news],
}


def hour_plan(hour: int, half: bool) -> list:
    return [post_news, post_video] if half else SCHEDULE.get(hour, [post_news, post_video])


async def tick(client, channel) -> None:
    now = datetime.now()
    half = now.minute >= 30
    slot = f"{today()} {now.hour}:{'30' if half else '00'}"
    if not (DAY_START <= now.hour < DAY_END) or state.get("last_slot") == slot:
        return
    try:
        for step in hour_plan(now.hour, half):
            if await step(client, channel):
                log.info("CS2 post: %s", step.__name__)
                break
        state["last_slot"] = slot
        save_state()
    except Exception as e:
        log.error("CS2 kanal xatosi: %r", e)


async def run_once(client: TelegramClient) -> None:
    rasm.configure("CS2 PULSI", f"@{USERNAME}", top=(40, 42, 50), bottom=(10, 11, 14))
    load_state()
    channel = await client.get_entity(USERNAME)
    log.info("Kanal topildi: %s (soat %s)", channel.title, datetime.now().strftime("%H:%M"))
    deadline = datetime.now() + timedelta(seconds=RUN_WINDOW_SECONDS)
    while True:
        await tick(client, channel)
        try:
            active = await post_live(client, channel)
        except Exception as e:
            log.error("Jonli xato: %r", e)
            active = False
        try:
            await moderator.moderate(client, channel, state, USERNAME)
            save_state()
        except Exception as e:
            log.error("Moderator xatosi: %r", e)
        daytime = DAY_START <= datetime.now().hour < DAY_END
        if not (active or daytime) or datetime.now() + timedelta(seconds=POLL_SECONDS) > deadline:
            break
        await asyncio.sleep(POLL_SECONDS)
