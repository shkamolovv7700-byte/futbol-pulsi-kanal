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
HIGHLIGHT = re.compile(r"highlight|best|ace|clutch|moments|plays|top \d+|insane|1v\d|\bvs\.?\b", re.IGNORECASE)
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


def uz_dates(t: dict) -> str:
    s, e = t["start"], t["end"]
    m = lambda d: cs2data.UZ_MONTHS[list(cs2data.MONTHS)[d.month - 1]]
    return f"{s.day}–{e.day} {m(e)}" if s.month == e.month else f"{s.day} {m(s)} – {e.day} {m(e)}"


async def post_calendar(client, channel) -> bool:
    """Turnirlar taqvimi: chorshanba va shanba kunlari."""
    if datetime.now().weekday() not in (2, 5) or state.get("calendar_day") == today():
        return False
    now = datetime.now(cs2data.TASHKENT)
    ts = sorted((t for t in await asyncio.to_thread(cs2data.tournaments) if t["end"] >= now and not t["cancelled"]),
                key=lambda t: t["start"])[:6]
    if not ts:
        return False
    rows = [(i, t["name"], f"{uz_dates(t)} · {t['location']} · {t['prize']}", t["teams"]) for i, t in enumerate(ts, 1)]
    image = await asyncio.to_thread(rasm.scorers_card, "Eng katta turnirlar taqvimi", rows, "jamoalar soni", "TURNIRLAR")
    lines = [f"{'🔴' if t['start'] <= now else '🗓'} <b>{html.escape(t['name'])}</b> — {uz_dates(t)}, "
             f"{html.escape(t['location'])}, 💰 {t['prize']}" for t in ts]
    await send_card(client, channel, image, "🏆 <b>CS2 turnirlari taqvimi</b> <i>(S-daraja)</i>\n\n" + "\n".join(lines)
                    + "\n\nQaysi turnirni kutyapsiz? 👇\n\n#CS2 #turnirlar")
    state["calendar_day"] = today()
    save_state()
    return True


async def post_preview(client, channel) -> bool:
    """Turnir boshlanishidan oldin (3 kun ichida): kimlar qatnashadi, qanday kirgan, TOP-15 dan kim yo'q va nega."""
    now = datetime.now(cs2data.TASHKENT)
    done = state.setdefault("previewed", [])
    soon = [t for t in await asyncio.to_thread(cs2data.tournaments)
            if not t["cancelled"] and t["page"] not in done and timedelta(days=-1) <= t["start"] - now <= timedelta(days=3)]
    if not soon:
        return False
    t = min(soon, key=lambda t: t["start"])
    teams, page_text = await asyncio.to_thread(cs2data.participants, t["page"])
    if len(teams) < 4:  # qatnashchilar hali e'lon qilinmagan
        return False
    names = {cs2data.norm(x["team"]) for x in teams}
    _, ranking = await asyncio.to_thread(cs2data.ranking)
    missing = [r for r in ranking[:15] if cs2data.norm(r["team"]) not in names]
    invited = sum(x["how"].startswith("Taklif") for x in teams)
    image = await asyncio.to_thread(
        rasm.two_column_card, t["name"], f"{uz_dates(t)} · {t['location']} · {t['prize']} · {len(teams)} jamoa",
        [(x["team"], x["how"]) for x in teams], "QATNASHCHILAR", "Manba: Liquipedia")
    absent = []
    for r in missing:
        reason = cs2data.absence_reason(r["team"], page_text)
        reason_uz = await asyncio.to_thread(cs2data.translate_en, reason) if reason else None
        absent.append(f"• <b>{html.escape(r['team'])}</b> (Valve reytingida {r['rank']}-o'rin)"
                      + (f" — {html.escape(reason_uz)}" if reason_uz else ""))
    caption = (f"🏆 <b>{html.escape(t['name'])}</b> {'boshlandi' if t['start'] <= now else 'yaqinda boshlanadi'}!\n"
               f"🗓 {uz_dates(t)} · 📍 {html.escape(t['location'])} · 💰 {t['prize']}\n\n"
               f"👥 {len(teams)} ta jamoa: {invited} tasi Valve reytingi bo'yicha taklif olgan, "
               f"{len(teams) - invited} tasi saralashdan o'tgan.\n")
    if absent:
        caption += ("\n❌ <b>Reytingdagi TOP-15 dan qatnashmayotganlar:</b>\n" + "\n".join(absent[:8])
                    + ("\n<i>Sababi ko'rsatilmaganlar uchun rasmiy sabab e'lon qilinmagan: odatda jamoa taklifni rad etgan "
                       "yoki boshqa turnir bilan vaqti to'g'ri kelmagan.</i>" if any("—" not in a for a in absent) else ""))
    caption += "\n\nKim chempion bo'ladi? Izohlarda yozing 👇\n\n#CS2 #turnir"
    await send_card(client, channel, image, caption)
    done.append(t["page"])
    save_state()
    return True


async def post_highlights(client, channel, since_hours: int = 30, title: str = "Kechagi o'yinlar lavhalari") -> bool:
    """Bir necha soatlik eng zo'r lavhalar to'plami (rasmiy kanallardagi sharh videolari)."""
    min_ts = datetime.now().timestamp() - since_hours * 3600
    seen = set(state["seen"])
    videos = sorted((v for v in await asyncio.to_thread(fetch_videos)
                     if v["ts"] >= min_ts and v["id"] not in seen and HIGHLIGHT.search(v["title"])), key=lambda v: -v["ts"])
    if not videos:
        return False
    rows = [f'▶️ <a href="{html.escape(v["link"], quote=True)}">{html.escape(v["title"][:90])}</a>' for v in videos[:8]]
    image = await asyncio.to_thread(rasm.video_card, title, "ESL · HLTV", len(rows))
    await send_card(client, channel, image, f"🎬 <b>{title}</b>\n\n" + "\n\n".join(rows)
                    + "\n\nQaysi lahza eng zo'ri? 🔥\n\n#CS2 #lavhalar")
    state["seen"].extend(v["id"] for v in videos[:8])
    save_state()
    return True


async def post_today_highlights(client, channel) -> bool:
    hours = max(1, datetime.now().hour - 6)
    return await post_highlights(client, channel, hours, "Bugungi o'yinlar lavhalari")


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
    12: [post_preview, post_news], 13: [post_quiz], 16: [post_video, post_news],
    17: [post_ranking, post_calendar, post_news], 19: [post_video, post_news], 20: [post_poll],
    22: [post_video, post_news],
}
# yarim soatlar: 9:30 kechagi o'yinlar lavhalari, 22:30 bugungi o'yinlar lavhalari, qolganida yangilik
HALF_SCHEDULE = {9: [post_highlights, post_news], 22: [post_today_highlights, post_news]}


def hour_plan(hour: int, half: bool) -> list:
    if half:
        return HALF_SCHEDULE.get(hour, [post_news, post_video])
    return SCHEDULE.get(hour, [post_news, post_video])


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
        if not client.is_connected():  # Telegram aloqasi uzildi (masalan, kirish bekor qilingan) - kutmasdan chiqamiz
            raise SystemExit("Telegram bilan aloqa uzildi")
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
