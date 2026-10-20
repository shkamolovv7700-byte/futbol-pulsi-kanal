"""
Reklama bo'yicha AI yordamchi bot (Telegram Bot API + Claude).

Futbol Pulsi va CS2 Pulsi kanallariga reklama bermoqchi bo'lganlarga javob beradi, so'rovni to'plab
admin maxfiy kanaliga (LEADS_CHAT_ID) buyurtma qilib yuboradi.

Muhit o'zgaruvchilari:
  BOT_TOKEN          - @BotFather bergan token
  ANTHROPIC_API_KEY  - console.anthropic.com dan kalit (SDK o'zi o'qiydi)
  LEADS_CHAT_ID      - buyurtmalar yuboriladigan maxfiy kanal (bot u yerda admin)
  BOT_RUN_SECONDS    - bitta ishga tushishda necha soniya ishlasin (bulut uchun, standart 25 daqiqa)

Mijozlar yozishmalari faqat xotirada saqlanadi (repozitoriy ochiq - shaxsiy ma'lumot u yerga yozilmaydi).
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import anthropic

BASE_DIR = Path(__file__).resolve().parent
TOKEN = os.getenv("BOT_TOKEN", "").strip()
LEADS_CHAT_ID = os.getenv("LEADS_CHAT_ID", "")
RUN_SECONDS = int(os.getenv("BOT_RUN_SECONDS", str(25 * 60)))
MODEL = "claude-opus-5-5"
CHANNELS = ["futbolpulsi_uz", "cs2pulsi_uz"]
HISTORY_LIMIT = 40  # shundan uzun suhbat yangidan boshlanadi (tarix boshini kesish API da rad etiladi)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bot")
claude = anthropic.Anthropic()


# ---------- Telegram Bot API ----------

def tg(method: str, http_timeout: int = 60, **params) -> dict:
    data = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                   for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=http_timeout) as r:
        return json.loads(r.read())["result"]


def send(chat_id, text: str) -> None:
    for i in range(0, len(text), 4000):  # Telegram xabari 4096 belgigacha
        tg("sendMessage", chat_id=chat_id, text=text[i:i + 4000], disable_web_page_preview="true")


# ---------- Kanallar haqida ma'lumot ----------

_stats = {"t": 0.0, "text": ""}


def channel_stats() -> str:
    """Obunachilar soni - har soatda yangilanadi."""
    if time.time() - _stats["t"] < 3600 and _stats["text"]:
        return _stats["text"]
    lines = []
    for ch in CHANNELS:
        try:
            info = tg("getChat", chat_id=f"@{ch}")
            count = tg("getChatMemberCount", chat_id=f"@{ch}")
            lines.append(f"- {info.get('title', ch)} (t.me/{ch}): {count} obunachi")
        except urllib.error.HTTPError as e:
            log.warning("@%s statistikasi olinmadi: %s", ch, e)
    _stats.update(t=time.time(), text="\n".join(lines))
    return _stats["text"]


def prices() -> str:
    path = BASE_DIR / "reklama_narxlar.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return json.dumps(data, ensure_ascii=False, indent=1) if data.get("narxlar") else ""


def system_prompt() -> str:
    price_list = prices()
    return f"""Siz "Pulsi" Telegram kanallarining reklama bo'yicha yordamchisisiz. Kanallar egasi nomidan reklama \
beruvchilar bilan muloqot qilasiz.

Kanallar:
{channel_stats() or "- Futbol Pulsi 🔥 (t.me/futbolpulsi_uz) va CS2 Pulsi 🎯 (t.me/cs2pulsi_uz)"}
Futbol Pulsi - o'zbek tilidagi futbol yangiliklari, jonli gollar, jadvallar. CS2 Pulsi - o'zbek tilidagi CS2 \
kibersporti: turnirlar, jonli natijalar, reyting. Ikkala kanal ham yangi va tez o'smoqda.

Narxlar:
{price_list or "Narxlar ro'yxati hali kiritilmagan. Hech qachon narx o'ylab topmang: admin narxni aniqlab yozishini ayting."}

Vazifangiz:
- Mijoz qaysi tilda yozsa, o'sha tilda (o'zbek, rus yoki ingliz) qisqa, xushmuomala va aniq javob bering. Markdown ishlatmang.
- Kanallar, auditoriya, reklama formatlari (post, pin, repost) haqidagi savollarga javob bering.
- Faqat yuqoridagi ma'lumotlarga tayaning. Bilmagan narsangizni (o'rtacha ko'rishlar, kafolatlar, chegirmalar) va'da \
qilmang - admin aniqlab berishini ayting.
- Mijoz reklama bermoqchi bo'lsa, quyidagilarni bilib oling: qaysi kanal(lar), nima reklama qilinadi (havola bilan), \
qachon joylash kerak, byudjet (agar aytsa) va aloqa uchun Telegram username yoki telefon. Hammasini bir xabarda \
so'ramang - suhbat davomida tabiiy so'rang.
- Ma'lumot yetarli bo'lgach, send_lead vositasi orqali so'rovni adminga yuboring va mijozga admin tez orada \
bog'lanishini ayting.
- Rad etiladigan reklamalar: stavka/bukmeker, kazino, 18+, firibgarlik, "tez boyish", moliyaviy piramida, \
qonunga zid narsalar. Bunday so'rovlarni muloyimlik bilan rad eting va send_lead ni chaqirmang.
- Parol, karta raqami yoki SMS kod so'ramang va qabul qilmang. To'lov faqat admin bilan kelishiladi."""


SEND_LEAD = {
    "name": "send_lead",
    "description": "Reklama so'rovini adminga yuboradi. Mijoz kanal, reklama mazmuni, vaqt va aloqani aytgandan keyin chaqiring.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "kanallar": {"type": "string", "description": "Qaysi kanal(lar): Futbol Pulsi, CS2 Pulsi yoki ikkalasi"},
            "mazmun": {"type": "string", "description": "Nima reklama qilinadi, havolasi bilan"},
            "vaqt": {"type": "string", "description": "Qachon joylash kerak"},
            "byudjet": {"type": "string", "description": "Mijoz aytgan byudjet yoki 'aytilmadi'"},
            "aloqa": {"type": "string", "description": "Telegram username yoki telefon"},
            "izoh": {"type": "string", "description": "Qo'shimcha muhim tafsilotlar yoki bo'sh"},
        },
        "required": ["kanallar", "mazmun", "vaqt", "byudjet", "aloqa", "izoh"],
        "additionalProperties": False,
    },
}


# ---------- Suhbat ----------

histories: dict[int, list] = {}


def forward_lead(lead: dict, user: dict) -> str:
    if not LEADS_CHAT_ID:
        return "Admin kanali sozlanmagan - so'rov saqlanmadi."
    who = f"@{user['username']}" if user.get("username") else user.get("first_name", "")
    text = ("🆕 Yangi reklama so'rovi\n\n"
            f"👤 Mijoz: {who} (id {user['id']})\n"
            f"📢 Kanal: {lead['kanallar']}\n📝 Mazmun: {lead['mazmun']}\n🗓 Vaqt: {lead['vaqt']}\n"
            f"💰 Byudjet: {lead['byudjet']}\n📞 Aloqa: {lead['aloqa']}\n💬 Izoh: {lead['izoh'] or '-'}")
    send(LEADS_CHAT_ID, text)
    log.info("Yangi so'rov adminga yuborildi")
    return "So'rov adminga yuborildi."


def answer(chat_id: int, user: dict, text: str) -> str:
    history = histories.setdefault(chat_id, [])
    if len(history) > HISTORY_LIMIT:
        history.clear()
    history.append({"role": "user", "content": text})
    for _ in range(3):  # vosita chaqiruvi bo'lsa - natijasini qaytarib, javobni olamiz
        response = claude.beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=system_prompt(),
            messages=history,
            tools=[SEND_LEAD],
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        history.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            history.pop()
            history.pop()
            return "Kechirasiz, bu savolga javob bera olmayman. Reklama bo'yicha savolingiz bo'lsa, yozing."
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "send_lead":
                try:
                    result, is_error = forward_lead(block.input, user), False
                except (urllib.error.URLError, KeyError) as e:
                    result, is_error = f"Yuborib bo'lmadi: {e}", True
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": result, "is_error": is_error})
        history.append({"role": "user", "content": results})
    reply = "".join(b.text for b in response.content if b.type == "text").strip()
    return reply or "Rahmat! Admin tez orada siz bilan bog'lanadi."


def handle(update: dict) -> None:
    msg = update.get("message") or {}
    chat, user, text = msg.get("chat", {}), msg.get("from", {}), msg.get("text")
    if chat.get("type") != "private" or not text:
        return
    if text.startswith("/start"):
        histories.pop(chat["id"], None)
        send(chat["id"], "Assalomu alaykum! 👋 Men Futbol Pulsi 🔥 va CS2 Pulsi 🎯 kanallarining reklama bo'yicha "
                         "yordamchisiman. Qaysi kanalda va nimani reklama qilmoqchisiz?")
        return
    tg("sendChatAction", chat_id=chat["id"], action="typing")
    try:
        reply = answer(chat["id"], user, text)
    except anthropic.RateLimitError:
        reply = "Hozir so'rovlar ko'p, bir daqiqadan keyin qayta yozing 🙏"
    except anthropic.APIStatusError as e:
        log.error("Claude API xatosi %s: %s", e.status_code, e.message)
        reply = "Texnik nosozlik bo'ldi. Iltimos, birozdan keyin yana yozing."
    except anthropic.APIConnectionError:
        reply = "Texnik nosozlik bo'ldi. Iltimos, birozdan keyin yana yozing."
    send(chat["id"], reply)


def main() -> None:
    if not TOKEN or not os.getenv("ANTHROPIC_API_KEY", "").strip():
        log.warning("BOT_TOKEN yoki ANTHROPIC_API_KEY hali kiritilmagan - bot ishga tushmadi.")
        return
    me = tg("getMe")
    log.info("Bot ishga tushdi: @%s", me["username"])
    deadline, offset = time.time() + RUN_SECONDS, None
    while time.time() < deadline:
        try:
            updates = tg("getUpdates", http_timeout=70, offset=offset, timeout=50, allowed_updates=["message"])
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("getUpdates xatosi: %s", e)
            time.sleep(5)
            continue
        for update in updates:
            offset = update["update_id"] + 1  # Telegram tasdiqlangan xabarlarni qayta yubormaydi
            try:
                handle(update)
            except Exception as e:  # bitta xabar xatosi botni to'xtatmasin
                log.exception("Xabarni qayta ishlashda xato: %r", e)
    if offset:  # oxirgi xabarlarni "o'qildi" deb belgilaymiz
        tg("getUpdates", offset=offset, timeout=0)


if __name__ == "__main__":
    main()
