"""
"USMLE Pulsi 🩺" kanali (t.me/usmlepulsi_uz): kuniga 10 ta post (08:00-22:00), Toshkent vaqti.
DARSLAR REJIMI (asosiy): usmle_darslar.json dagi har dars 2 kun - 1-kun mavzu postlari (kartochka +
o'zbekcha tushuntirish), 2-kun vaziyatli masalalar. LESSONS_ONLY=True bo'lsa faqat darslar chiqadi.
Quyidagi umumiy kontent (usmle_kontent.json) LESSONS_ONLY=False bo'lganda, darslar tugagach ishlatiladi:
Bulutda (GitHub Actions) ishlaydi: `python bulut.py usmle`. Kontent usmle_kontent.json da.

  - klinik savol: inglizcha vinyetka + Telegram viktorina (to'g'ri javob va o'zbekcha izoh bilan);
  - high-yield fakt: inglizcha rasm-kartochka + o'zbekcha tushuntirish;
  - maslahat: Step 1 / Step 2 CK ga tayyorlanish bo'yicha o'zbekcha post;
  - yangilik: ECFMG rasmiy RSS'ida yangi xabar chiqsa (o'zbekchaga tarjima), bo'lmasa - maslahat.
"""

import html
import json
import logging
import random
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from telethon import TelegramClient

import cs2data
import kanal
import usmle_rasm

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "usmle_state.json"
CONTENT_FILE = BASE_DIR / "usmle_kontent.json"
LESSONS_FILE = BASE_DIR / "usmle_darslar.json"
LESSONS_ONLY = True  # faqat darslar chiqadi; False - darslar tugaganda umumiy kontent (usmle_kontent.json)
USERNAME = "usmlepulsi_uz"
NEWS_FEED = "https://www.ecfmg.org/news/feed/"
NEWS_MAX_AGE_DAYS = 10
SLOTS = {8: "fakt", 10: "savol", 12: "fakt", 13: "yangilik", 15: "savol",
         17: "fakt", 18: "maslahat", 19: "savol", 21: "savol", 22: "fakt"}
LATE_HOURS = 1  # GitHub kechiksa ham shu soat ichida joylanadi

log = logging.getLogger("usmle")
state: dict = {}


def load_state() -> None:
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    for key in ("used_savol", "used_fakt", "used_maslahat", "seen_news"):
        state.setdefault(key, [])


def save_state() -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pick(items: list[dict], key: str) -> dict:
    """Navbatdagi ishlatilmagan element; fanlar aralashib chiqadi. Hammasi tugasa - boshidan."""
    free = [i for i in range(len(items)) if i not in state[key]]
    if not free:
        log.warning("%s tugadi - qaytadan boshlanmoqda. usmle_kontent.json ni to'ldiring!", key)
        state[key], free = [], list(range(len(items)))
    last = items[state[key][-1]].get("fan") if state[key] else None
    idx = next((i for i in free if items[i].get("fan") != last), free[0])
    state[key].append(idx)
    return items[idx]


def tags(*words: str) -> str:
    return " ".join("#" + re.sub(r"\W", "", w.replace(" ", "_")) for w in words if w)


FOOTER = f"\n\n📲 @{USERNAME}"


def shuffled(q: dict) -> tuple[list[str], int]:
    """Javob variantlarini aralashtiradi (JSON da to'g'ri javob ko'pincha birinchi turadi)."""
    order = list(range(len(q["javoblar"])))
    random.Random(q["vinyetka"]).shuffle(order)  # bir savol doim bir xil tartibda chiqadi
    return [q["javoblar"][i] for i in order], order.index(q["togri"])


async def post_savol(client: TelegramClient, channel, content: dict) -> None:
    q = pick(content["savollar"], "used_savol")
    text = (f"🩺 <b>USMLE {html.escape(q['step'])} | {html.escape(q['fan'])}</b>\n\n"
            f"{html.escape(q['vinyetka'])}\n\n"
            f"❓ <b>{html.escape(q['savol'])}</b>\n\n"
            f"👇 Javobni pastdagi viktorinada belgilang, izoh javobdan keyin chiqadi\n\n"
            f"{tags('USMLE', q['step'], q['fan'])}{FOOTER}")
    options, correct = shuffled(q)
    msg = await client.send_message(channel, text, parse_mode="html")
    await client.send_file(channel, kanal.build_poll(f"🧠 {q['savol']}"[:255], options, True,
                                                     correct, q["izoh"][:200]), reply_to=msg.id)
    log.info("Savol joylandi: %s — %s", q["fan"], q["vinyetka"][:50])


async def post_fakt(client: TelegramClient, channel, content: dict) -> None:
    f = pick(content["faktlar"], "used_fakt")
    card = usmle_rasm.fact_card(f["sarlavha"], f["bandlar"], f["fan"], style=len(state["used_fakt"]))
    text = (f"⚡️ <b>High-yield: {html.escape(f['sarlavha'])}</b>\n\n{html.escape(f['izoh'])}\n\n"
            f"💾 Saqlab qo'ying - imtihonda kerak bo'ladi\n\n{tags('highyield', f['fan'])}{FOOTER}")
    await client.send_file(channel, card, caption=text[:1024], parse_mode="html")
    log.info("Fakt joylandi: %s", f["sarlavha"])


async def post_maslahat(client: TelegramClient, channel, content: dict) -> None:
    m = pick(content["maslahatlar"], "used_maslahat")
    text = (f"💡 <b>{html.escape(m['sarlavha'])}</b>\n\n{html.escape(m['matn'])}\n\n"
            f"💬 Sizning tajribangiz qanday? Izohlarda yozing\n\n#maslahat #USMLE{FOOTER}")
    await client.send_message(channel, text, parse_mode="html", link_preview=False)
    log.info("Maslahat joylandi: %s", m["sarlavha"])


def fresh_news() -> dict | None:
    """ECFMG RSS'idagi eng eski ko'rilmagan, yaqinda chiqqan xabar."""
    try:
        root = kanal.fetch_xml(NEWS_FEED)
    except Exception as e:
        log.warning("ECFMG RSS ishlamadi: %r", e)
        return None
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            continue
        if link and link not in state["seen_news"] and time.time() - ts < NEWS_MAX_AGE_DAYS * 86400:
            desc = html.unescape(re.sub(r"<[^>]+>", " ", it.findtext("description") or ""))
            items.append({"link": link, "title": html.unescape(it.findtext("title") or "").strip(),
                          "desc": re.sub(r"\s+", " ", desc).strip()[:450], "ts": ts})
    return min(items, key=lambda i: i["ts"]) if items else None


def first_sentences(text: str, limit: int = 350) -> str:
    text = re.sub(r"\s*(The post .*|\[…\]|\[\.\.\.\])\s*$", "", text)
    out = ""
    for s in re.split(r"(?<=[.!?])\s+", text):
        if len(out) + len(s) > limit:
            break
        out += s + " "
    return out.strip() or text[:limit]


async def post_yangilik(client: TelegramClient, channel, content: dict) -> None:
    n = fresh_news()
    if not n:
        await post_maslahat(client, channel, content)
        return
    title = cs2data.translate_en(n["title"]) or n["title"]
    desc = first_sentences(n["desc"])
    desc_uz = cs2data.translate_en(desc) or desc
    text = (f"📰 <b>ECFMG yangiligi: {html.escape(title)}</b>\n\n{html.escape(desc_uz)}\n\n"
            f"🇬🇧 <i>{html.escape(n['title'])}</i>\n🔗 <a href=\"{html.escape(n['link'])}\">Rasmiy manba</a>"
            f"\n\n#yangilik #ECFMG{FOOTER}")
    await client.send_message(channel, text, parse_mode="html", link_preview=False)
    state["seen_news"].append(n["link"])
    log.info("Yangilik joylandi: %s", n["title"])


POSTERS = {"savol": post_savol, "fakt": post_fakt, "maslahat": post_maslahat, "yangilik": post_yangilik}


def due_slot() -> str | None:
    now = datetime.now()
    for hour in SLOTS:
        if hour <= now.hour < hour + LATE_HOURS:
            slot = f"{now:%Y-%m-%d} {hour}"
            return slot if state.get("last_slot") != slot else None
    return None


def lesson_item(lessons: list[dict], today: str) -> tuple[int, dict, str, int, dict] | None:
    """Darslar rejimi: har dars 2 kun - 1-kun 'postlar' (mavzu), 2-kun 'savollar' (vaziyatli masalalar).
    Kun tugaguncha chiqmay qolgan postlar ertasi kuni davom etadi (ma'lumot to'liq chiqishi uchun).
    Qaytaradi: (dars raqami, dars, bosqich, tartib raqami, element) yoki None - darslar tugagan."""
    d = state.setdefault("dars", {"idx": 0, "bosqich": "postlar", "pos": 0, "kun": None})
    if d["kun"] != today:
        if d["kun"] and d["idx"] < len(lessons) and d["pos"] >= len(lessons[d["idx"]][d["bosqich"]]):
            if d["bosqich"] == "postlar":
                d["bosqich"] = "savollar"
            else:
                d["idx"], d["bosqich"] = d["idx"] + 1, "postlar"
            d["pos"] = 0
        d["kun"] = today
    if d["idx"] >= len(lessons):
        return None
    items = lessons[d["idx"]][d["bosqich"]]
    if d["pos"] >= len(items):
        return None  # bugungi bosqich tugadi - keyingisi ertaga
    return d["idx"], lessons[d["idx"]], d["bosqich"], d["pos"], items[d["pos"]]


async def post_lesson(client: TelegramClient, channel, n: int, lesson: dict, stage: str, pos: int, item: dict) -> None:
    total = len(lesson[stage])
    head = f"{n + 1}-DARS: {lesson['nomi']}"
    tag = f"#dars{n + 1} #{re.sub(r'\W', '', lesson['fan'])}"
    if stage == "postlar":
        card = usmle_rasm.fact_card(item["sarlavha"], item["bandlar"], lesson["fan"], style=n,
                                    label=f"{n + 1}-DARS  •  {pos + 1}/{total}")
        text = (f"📚 <b>{html.escape(head)}</b> | {pos + 1}/{total}\n\n<b>{html.escape(item['sarlavha'])}</b>\n\n"
                f"{html.escape(item['izoh'], quote=False)}\n\n{tag}{FOOTER}")
        if len(re.sub(r"<[^>]+>", "", text)) <= 1024:
            await client.send_file(channel, card, caption=text, parse_mode="html")
        else:  # rasm izohi 1024 belgigacha - uzun tushuntirish rasmga javob sifatida alohida chiqadi
            photo = await client.send_file(channel, card, caption=f"📚 <b>{html.escape(head)}</b> | {pos + 1}/{total}",
                                           parse_mode="html")
            await client.send_message(channel, text, parse_mode="html", reply_to=photo.id)
    else:
        options, correct = shuffled(item)
        text = (f"🧩 <b>Vaziyatli masala {pos + 1}/{total}</b> | {html.escape(head)}\n\n"
                f"{html.escape(item['vinyetka'], quote=False)}\n\n❓ <b>{html.escape(item['savol'], quote=False)}</b>\n\n"
                f"👇 Javobni viktorinada belgilang, izoh javobdan keyin chiqadi\n\n{tag} #vaziyatli_masala{FOOTER}")
        msg = await client.send_message(channel, text, parse_mode="html")
        await client.send_file(channel, kanal.build_poll(f"🧠 {item['savol']}"[:255], options, True, correct,
                                                         item["izoh"][:200]), reply_to=msg.id)
    state["dars"]["pos"] += 1
    log.info("%s: %s %d/%d", head, stage, pos + 1, total)


async def run_once(client: TelegramClient) -> None:
    load_state()
    slot = due_slot()
    if not slot:
        return
    state["last_slot"] = slot  # avval belgilaymiz: xato bo'lsa ham shu soatda qayta-qayta urinmasin
    lessons = json.loads(LESSONS_FILE.read_text(encoding="utf-8"))["darslar"]
    item = lesson_item(lessons, slot.split()[0])
    save_state()
    if item:
        await post_lesson(client, await client.get_entity(USERNAME), *item)
    elif LESSONS_ONLY:
        log.info("Bugungi dars bosqichi tugagan yoki yangi dars yo'q - post joylanmadi")
    else:
        content = json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
        await POSTERS[SLOTS[int(slot.split()[1])]](client, await client.get_entity(USERNAME), content)
    save_state()
