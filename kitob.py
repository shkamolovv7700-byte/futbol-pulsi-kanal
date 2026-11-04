"""
"Kitob Pulsi 📖" kanali (t.me/kitobpulsi_uz): kuniga 10 marta (08:00-22:00) kitobdan iqtibos (rasm-kartochka).
Bulutda (GitHub Actions) ishlaydi: `python bulut.py kitob`. Iqtiboslar kitob_kontent.json da.
Muhokama guruhi moderatsiyasi - moderator.py.
"""

import html
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient

import kitob_rasm
import moderator

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "kitob_state.json"
CONTENT_FILE = BASE_DIR / "kitob_kontent.json"
USERNAME = "kitobpulsi_uz"
SLOTS = [8, 10, 13, 15, 17, 18, 19, 20, 21, 22]  # Toshkent vaqti: kuniga 10 ta, kechqurun zichroq
LONG_SLOTS = {13, 21}  # bu soatlarda uzunroq "kitobdan parcha" (odamlar bo'sh, ko'proq o'qiydi)
LATE_HOURS = 1              # GitHub kechiksa ham shu soat ichida joylanadi
TAGS = {"jahon": "#jahon_adabiyoti", "ozbek": "#ozbek_adabiyoti", "motivatsiya": "#motivatsiya"}

log = logging.getLogger("kitob")
state: dict = {}


def load_state() -> None:
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    state.setdefault("used", [])


def save_state() -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def is_long(q: dict) -> bool:
    return q.get("turi") == "parcha"


def next_quote(quotes: list[dict], priority: list[str] = (), long: bool = False) -> tuple[int, dict]:
    """Avval ustuvor mualliflar (masalan, yangi yuborilgan kitob) ketma-ket chiqadi, keyin turkumlar
    navbatma-navbat aralashtiriladi (jahon -> o'zbek -> motivatsiya); hammasi tugasa - boshidan.
    long=True - parcha slotida faqat parchalar (tugasa - oddiy iqtibos), aks holda faqat qisqa iqtiboslar."""
    free = [i for i in range(len(quotes)) if i not in state["used"]]
    if not free:
        log.warning("Barcha iqtiboslar ishlatildi - qaytadan boshlanmoqda. kitob_kontent.json ni to'ldiring!")
        state["used"], free = [], list(range(len(quotes)))
    free = [i for i in free if is_long(quotes[i]) == long] or free
    for author in priority:  # ro'yxat tartibida: birinchi muallif tugasa - keyingisi
        first = next((i for i in free if quotes[i]["muallif"] == author), None)
        if first is not None:
            return first, quotes[first]
    order = ["jahon", "ozbek", "motivatsiya"]
    want = order[len(state["used"]) % len(order)]
    idx = next((i for i in free if quotes[i].get("turkum") == want), free[0])
    return idx, quotes[idx]


def caption(q: dict) -> str:
    source = f", «{html.escape(q['manba'])}»" if q.get("manba") else ""
    author_tag = "#" + re.sub(r"\W", "", q["muallif"].split()[-1])
    tail = (f"— <b>{html.escape(q['muallif'])}</b>{source}\n\n"
            f"💬 Sizga qaysi so'z yoqdi? Izohlarda yozing\n\n#iqtibos {TAGS.get(q.get('turkum'), '')} {author_tag}"
            f"\n\n📲 @{USERNAME}")
    head = "📜 <b>KITOBDAN PARCHA</b>\n\n" if is_long(q) else "📖 "
    text = f"{head}<i>«{html.escape(q['matn'])}»</i>\n\n" + tail
    # Telegram rasm izohi 1024 belgigacha: juda uzun parcha faqat kartochkada qoladi
    return text if len(re.sub(r"<[^>]+>", "", html.unescape(text))) <= 1024 else "📖 " + tail


def due_slot() -> str | None:
    now = datetime.now()
    for hour in SLOTS:
        if hour <= now.hour < hour + LATE_HOURS:
            slot = f"{now:%Y-%m-%d} {hour}"
            return slot if state.get("last_slot") != slot else None
    return None


async def run_once(client: TelegramClient) -> None:
    load_state()
    channel = await client.get_entity(USERNAME)
    slot = due_slot()
    if slot:
        content = json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
        long = int(slot.split()[1]) in LONG_SLOTS
        idx, q = next_quote(content["iqtiboslar"], content.get("ustuvor", []), long)
        card = kitob_rasm.quote_card(q["matn"], q["muallif"], q.get("manba", ""), style=len(state["used"]),
                                     label="KITOBDAN PARCHA" if is_long(q) else kitob_rasm.BRAND)
        await client.send_file(channel, card, caption=caption(q), parse_mode="html")
        state["used"].append(idx)
        state["last_slot"] = slot
        save_state()
        log.info("Iqtibos joylandi: %s — %s", q["muallif"], q["matn"][:50])
    try:
        await moderator.moderate(client, channel, state, USERNAME)
    except Exception as e:
        log.error("Moderator xatosi: %r", e)
    save_state()
