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
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from telethon import TelegramClient

import cs2data
import kanal
import moderator
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


DAY_START, DAY_END = 8 * 60, 22 * 60  # dars postlari 08:00-22:00 oralig'ida
MAX_TRIES = 3  # bir post 3 marta xato bersa, o'tkazib yuboriladi (kanal to'xtab qolmasin)


def step_lessons(step: str) -> list[int]:
    """'3:postlar' -> [3]; 'aralash:1,2,3,4' -> [1, 2, 3, 4]."""
    return [int(x) for x in step.split(":")[1].split(",")] if step.startswith("aralash:") else [int(step.split(":")[0])]


def plan_steps(data: dict) -> list[str]:
    """Qadamlar tartibi: avval 'reja', keyin rejada yo'q darslar odatiy tartibda -
    1 kun mavzu ('5:postlar'), ertasi o'sha darsning masalalari ('5:savollar').
    'aralash:1,2,3,4' - shu darslarning barcha vaziyatli masalalari bir kunda, aralashtirib."""
    steps = list(data.get("reja", []))
    covered = {f"{n}:savollar" for s in steps if s.startswith("aralash:") for n in step_lessons(s)}
    for n in range(1, len(data["darslar"]) + 1):
        steps += [s for s in (f"{n}:postlar", f"{n}:savollar") if s not in steps and s not in covered]
    return steps


def step_items(lessons: list[dict], step: str | None) -> list[tuple[int, str, int, dict]] | None:
    """Qadam elementlari: (dars raqami 0 dan, bosqich, dars ichidagi tartib, element). Dars hali yo'q - None."""
    if not step or any(n > len(lessons) for n in step_lessons(step)):
        return None
    if step.startswith("aralash:"):
        groups = [[(n - 1, "savollar", k, q) for k, q in enumerate(lessons[n - 1]["savollar"])]
                  for n in step_lessons(step)]
        return [g[i] for i in range(max(map(len, groups))) for g in groups if i < len(g)]  # navbatma-navbat
    n, stage = step.split(":")
    return [(int(n) - 1, stage, k, x) for k, x in enumerate(lessons[int(n) - 1][stage])]


def due_count(total: int, now: datetime) -> int:
    """Hozirgacha nechta post chiqqan bo'lishi kerak: 10 tagacha - SLOTS soatlarida,
    ko'proq bo'lsa (aralash kun) - 08:00-22:00 oralig'ida teng taqsimlanadi."""
    if total <= len(SLOTS):
        return min(total, sum(1 for h in SLOTS if h <= now.hour))
    minutes = now.hour * 60 + now.minute
    if minutes < DAY_START:
        return 0
    return min(total, int((minutes - DAY_START) / ((DAY_END - DAY_START) / total)) + 1)


def lesson_item(data: dict, now: datetime):
    """Har kuni bitta qadam. Kun tugaguncha chiqmay qolgan postlar ertasi kuni davom etadi (ma'lumot
    to'liq chiqishi uchun). Darsi hali qo'shilmagan qadam o'tkazib yuboriladi va dars qo'shilganda
    navbatdagi bo'ladi. Qaytaradi: (qadam, tartib, jami, (dars, bosqich, k, element)) yoki None."""
    lessons, today = data["darslar"], f"{now:%Y-%m-%d}"
    d = state.setdefault("dars", {})
    if "idx" in d:  # eski holat formati -> yangi
        d.update(joriy=f"{d.pop('idx') + 1}:{d.pop('bosqich')}", bajarildi=[])
    d.setdefault("bajarildi", [])
    d.setdefault("pos", 0)
    if d.get("kun") != today:
        cur = step_items(lessons, d.get("joriy"))
        if cur is None or d["pos"] >= len(cur):  # joriy qadam tugagan - keyingisini tanlaymiz
            if cur is not None:
                d["bajarildi"].append(d["joriy"])
            d["joriy"] = next((s for s in plan_steps(data) if s not in d["bajarildi"] and step_items(lessons, s)), None)
            d["pos"] = 0
            d["qoshimcha"] = 0  # flesh-karta / rasm-test hisobi
            d.pop("xulosa", None)
        d["kun"], d["boshlanish"] = today, d["pos"]  # bugun shu tartibdan boshladik
    cur = step_items(lessons, d.get("joriy"))
    if not cur or d["pos"] >= len(cur):
        return None  # bugungi qadam tugadi yoki yangi dars yo'q
    start = d.get("boshlanish", 0)
    if d["pos"] - start >= due_count(len(cur) - start, now):
        return None  # keyingi post vaqti hali kelmadi
    return d["joriy"], d["pos"], len(cur), cur[d["pos"]]


async def post_lesson(client: TelegramClient, channel, step: str, pos: int, total: int, entry: tuple) -> None:
    n, stage, k, item = entry
    lesson = json.loads(LESSONS_FILE.read_text(encoding="utf-8"))["darslar"][n]
    head = f"{n + 1}-DARS: {lesson['nomi']}"
    tag = f"#dars{n + 1} #{re.sub(r'\W', '', lesson['fan'])}"
    if stage == "postlar":
        card = usmle_rasm.fact_card(item["sarlavha"], item["bandlar"], lesson["fan"], style=n,
                                    label=f"{n + 1}-DARS  •  {pos + 1}/{total}")
        text = (f"📚 <b>{html.escape(head)}</b> | {pos + 1}/{total}\n\n<b>{html.escape(item['sarlavha'])}</b>\n\n"
                f"{html.escape(item['izoh'], quote=False)}\n\n{tag}{FOOTER}")
        files = [card]
        pic = item.get("rasm")
        if pic and (BASE_DIR / pic["fayl"]).exists():  # anatomik rasm (lotincha yozuvlar) - kartochka bilan albom
            files.append(str(BASE_DIR / pic["fayl"]))
            text = text.replace(FOOTER, f"\n🖼 <i>{html.escape(pic['manba'])}</i>{FOOTER}")
        short = f"📚 <b>{html.escape(head)}</b> | {pos + 1}/{total}"
        fits = len(re.sub(r"<[^>]+>", "", text)) <= 1024
        sent = await client.send_file(channel, files if len(files) > 1 else card,
                                      caption=([text if fits else short] + [""] * (len(files) - 1)) if len(files) > 1
                                      else (text if fits else short), parse_mode="html")
        if not fits:  # rasm izohi 1024 belgigacha - uzun tushuntirish rasmga javob sifatida alohida chiqadi
            first = sent[0] if isinstance(sent, list) else sent
            await client.send_message(channel, text, parse_mode="html", reply_to=first.id)
    else:
        uz = item.get("javoblar_uz") or []
        # variant: "inglizcha (o'zbekcha)"; Telegram cheklovi 100 belgi - oshsa faqat inglizcha qoladi
        both = [f"{en} ({u})" if u and len(f"{en} ({u})") <= 100 else en
                for en, u in zip(item["javoblar"], uz + [""] * len(item["javoblar"]))]
        options, correct = shuffled({**item, "javoblar": both})
        if step.startswith("aralash:"):
            title = f"🧩 <b>Vaziyatli masala {pos + 1}/{total}</b> | {n + 1}-dars: {html.escape(lesson['nomi'])}"
        else:
            title = f"🧩 <b>Vaziyatli masala {pos + 1}/{total}</b> | {html.escape(head)}"
        uz_text = ""
        if item.get("vinyetka_uz"):
            uz_text = (f"🇺🇿 <i>{html.escape(item['vinyetka_uz'], quote=False)}</i>\n\n")
        question = f"❓ <b>{html.escape(item['savol'], quote=False)}</b>"
        if item.get("savol_uz"):
            question += f"\n❓ <b>{html.escape(item['savol_uz'], quote=False)}</b>"
        text = (f"{title}\n\n🇬🇧 {html.escape(item['vinyetka'], quote=False)}\n\n{uz_text}{question}\n\n"
                f"👇 Javobni viktorinada belgilang, izoh javobdan keyin chiqadi\n\n{tag} #vaziyatli_masala{FOOTER}")
        poll_q = f"🧠 {item['savol']} ({item['savol_uz']})" if item.get("savol_uz") else f"🧠 {item['savol']}"
        if len(poll_q) > 255:
            poll_q = f"🧠 {item['savol']}"
        msg = await client.send_message(channel, text, parse_mode="html")
        await client.send_file(channel, kanal.build_poll(poll_q[:255], options, True, correct,
                                                         item["izoh"][:200]), reply_to=msg.id)
    log.info("%s: %s %d/%d (%s)", head, stage, pos + 1, total, step)


# --- Qo'shimcha formatlar (dars kuni): flesh-karta, "rasmni top" testi, kechki xulosa -------------------------
EXTRA_EVERY = 3        # har 3 ta mavzu postidan keyin bitta qo'shimcha post
EXTRA_GAP_MIN = 10     # postlar orasida kamida 10 daqiqa (ketma-ket "portlash" bo'lmasin)
SUMMARY_FROM = 21 * 60 + 30  # kechki xulosa 21:30 dan keyin, barcha postlardan so'ng


def lesson_extras(lesson: dict) -> list[tuple[str, dict]]:
    """Kartalar va rasm-testlarni aralashtiradi: 2 ta karta, 1 ta rasm-test, ..."""
    cards = [("karta", c) for c in lesson.get("kartalar", [])]
    tests = [("rasm_test", t) for t in lesson.get("rasm_testlar", [])]
    out = []
    while cards or tests:
        out += cards[:2]
        cards = cards[2:]
        if tests:
            out.append(tests.pop(0))
    return out


def utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def rich(parts: list[tuple[str, str | None]]) -> tuple[str, list]:
    """[(matn, 'bold'|'italic'|'spoiler'|None)] -> (matn, Telegram entity'lari). Telethon HTML spoiler'ni bilmaydi."""
    from telethon.tl.types import MessageEntityBold, MessageEntityItalic, MessageEntitySpoiler
    kinds = {"bold": MessageEntityBold, "italic": MessageEntityItalic, "spoiler": MessageEntitySpoiler}
    text, ents = "", []
    for chunk, kind in parts:
        if kind:
            ents.append(kinds[kind](offset=utf16(text), length=utf16(chunk)))
        text += chunk
    return text, ents


def extra_due(lessons: list[dict], now: datetime):
    d = state.get("dars", {})
    step = d.get("joriy")
    if not step or not step.endswith(":postlar"):
        return None
    n = int(step.split(":")[0]) - 1
    lesson = lessons[n]
    last = d.get("oxirgi_vaqt", "")
    if last and last > (now - timedelta(minutes=EXTRA_GAP_MIN)).isoformat(timespec="minutes"):
        return None
    extras = lesson_extras(lesson)
    k = d.get("qoshimcha", 0)
    posts_done = d.get("pos", 0) >= len(lesson["postlar"])
    if k < len(extras) and (d.get("pos", 0) >= EXTRA_EVERY * (k + 1) or posts_done):
        return n, lesson, k, len(extras), extras[k]
    if posts_done and k >= len(extras) and lesson.get("xulosa") and not d.get("xulosa") \
            and now.hour * 60 + now.minute >= SUMMARY_FROM:
        return n, lesson, None, None, ("xulosa", lesson["xulosa"])
    return None


async def post_extra(client: TelegramClient, channel, n: int, lesson: dict, k, total, entry) -> None:
    kind, item = entry
    tag = f"#dars{n + 1} #{re.sub(r'\W', '', lesson['fan'])}"
    if kind == "karta":
        text, ents = rich([
            (f"🃏 FLESH-KARTA | {n + 1}-dars\n\n", "bold"),
            (f"❓ {item['savol']}\n\n", "bold"),
            ("🤔 Avval o'zingiz javob bering, keyin pastdagi yashirin javobni bosing:\n\n👉 ", None),
            (item["javob"], "spoiler"),
            (f"\n\n{tag} #fleshkarta\n\n📲 @{USERNAME}", None)])
        await client.send_message(channel, text, formatting_entities=ents, link_preview=False)
    elif kind == "rasm_test":
        caption = (f"🔍 <b>RASMNI TOP</b> | {n + 1}-dars\n\n{html.escape(item['savol'])}\n\n"
                   f"👇 Javobni viktorinada belgilang\n\n🖼 <i>{html.escape(item['manba'])}</i>\n\n{tag} #rasmni_top{FOOTER}")
        photo = await client.send_file(channel, str(BASE_DIR / item["fayl"]), caption=caption, parse_mode="html")
        options, correct = shuffled({"vinyetka": item["fayl"], "javoblar": item["javoblar"], "togri": item["togri"]})
        await client.send_file(channel, kanal.build_poll("🔍 '?' bilan belgilangan tuzilma qaysi?", options, True,
                                                         correct, item["izoh"][:200]), reply_to=photo.id)
    else:  # kechki xulosa
        lines = "\n".join(f"{i}. {html.escape(f)}" for i, f in enumerate(item, 1))
        text = (f"🌙 <b>KUN XULOSASI | {n + 1}-dars: {html.escape(lesson['nomi'])}</b>\n\n"
                f"Bugungi darsdan eng muhim {len(item)} ta fakt - uxlashdan oldin bir marta o'qib chiqing:\n\n{lines}\n\n"
                f"📌 Saqlab qo'ying. Ertaga - shu darsning vaziyatli masalalari!\n\n{tag} #xulosa{FOOTER}")
        await client.send_message(channel, text, parse_mode="html", link_preview=False)
    log.info("%d-dars qo'shimcha post: %s", n + 1, kind)


async def run_once(client: TelegramClient) -> None:
    load_state()
    now = datetime.now()
    lessons = json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
    item = lesson_item(lessons, now)
    save_state()
    d = state["dars"]
    channel = None
    if d.get("keyingi_urinish", "") > now.isoformat(timespec="minutes"):
        pass  # oldingi urinish xato berdi - biroz kutamiz (har daqiqada urinib, Telegramni zo'riqtirmaslik uchun)
    elif item:
        channel = await client.get_entity(USERNAME)
        await attempt(lambda: post_lesson(client, channel, *item), now, "pos")
    else:
        extra = extra_due(lessons["darslar"], now)
        if extra:
            channel = await client.get_entity(USERNAME)
            await attempt(lambda: post_extra(client, channel, *extra), now,
                          "xulosa" if extra[2] is None else "qoshimcha")
        elif not LESSONS_ONLY and step_items(lessons["darslar"], d.get("joriy")) is None:
            slot = due_slot()  # darslar tugagan - umumiy kontent eski jadval bo'yicha
            if slot:
                state["last_slot"] = slot
                save_state()
                content = json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
                await POSTERS[SLOTS[int(slot.split()[1])]](client, await client.get_entity(USERNAME), content)
                save_state()
    try:  # muhokama guruhi moderatsiyasi (so'kinish, reklama)
        await moderator.moderate(client, channel or await client.get_entity(USERNAME), state, USERNAME)
    except Exception as e:
        log.error("Moderator xatosi: %r", e)
    save_state()


async def attempt(make_post, now: datetime, counter: str) -> None:
    """Postni yuboradi. Xato bo'lsa post O'TKAZIB YUBORILMAYDI: xato yoziladi, 5 daqiqadan keyin qayta urinadi."""
    d = state["dars"]
    d["urinish"] = d.get("urinish", 0) + 1
    save_state()
    try:
        await make_post()
    except Exception as e:
        log.exception("Post xato berdi (%s, %d-urinish)", d.get("joriy"), d["urinish"])
        d["oxirgi_xato"] = f"{now:%Y-%m-%d %H:%M} {d.get('joriy')} {counter}: {e!r}"[:500]
        d["keyingi_urinish"] = (now + timedelta(minutes=5)).isoformat(timespec="minutes")
        save_state()
        return
    if counter == "xulosa":
        d["xulosa"] = True
    else:
        d[counter] = d.get(counter, 0) + 1
    d["urinish"] = 0
    d["oxirgi_vaqt"] = now.isoformat(timespec="minutes")
    d.pop("keyingi_urinish", None)
    save_state()
