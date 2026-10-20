"""
Muhokama guruhi moderatori: so'kinish, haqorat, reklama va flud uchun xabarni o'chiradi
va yozuvchini vaqtincha "ovozsiz" qiladi (yoza olmaydi).

kanal.py dagi har daqiqalik tsikldan chaqiriladi: oxirgi tekshiruvdan keyingi yangi xabarlarni ko'rib chiqadi.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import ChannelParticipantsAdmins, PeerChannel, User

log = logging.getLogger("moderator")

MUTE_MINUTES = 10
REPEAT_MUTE = timedelta(days=1)  # 3-marta va undan keyin
REPEAT_LIMIT = 3
FLOOD_COUNT, FLOOD_SECONDS = 6, 30  # 30 soniyada 6 tadan ko'p xabar - flud

# O'zbekcha (lotin/kirill) va ruscha so'kinish va haqoratlarning o'zaklari. So'z boshidan tekshiriladi.
_APOS = "[ʻ‘’'`]?"
BAD_WORDS = re.compile(
    r"(?<![a-zа-яё])("
    # o'zbekcha
    rf"jalab|qanjiq|haromi|harom ?zoda|sik(?!l)|s1k|siktir|"
    rf"qo{_APOS}taq|ko{_APOS}tak|kotak|dalbayob|dolbayob|ahmoq|axmoq|tentak|mol ?ekansan|"
    r"жалаб|қанжиқ|ҳароми|сикай|сиктир|қўтоқ|кўтак|"
    # ruscha
    r"бля|блят|хуй|хуё|хуе|пизд|еба|ёба|ебл|ебу|сука|суки|мудак|мудил|долбо|гандон|шлюх|чмо|дебил|"
    r"blya|blyat|xuy|huy|pizd|yeba|ebat|suka|mudak|dolboy|gandon"
    r")",
    re.IGNORECASE,
)
# Reklama: havola, boshqa kanal/guruhga chaqiriq, stavka
AD_PATTERN = re.compile(
    r"(https?://|t\.me/|telegram\.me/|www\.|\.(com|uz|ru|net|org)\b|"
    r"obuna bo|kanalimga|guruhimga|подпишись|подписывайтесь|заработ|pul ishla|"
    r"1xbet|melbet|stavka|ставк|kazino|казино|promokod|промокод)",
    re.IGNORECASE,
)
MENTION = re.compile(r"(?<!\w)@(\w{4,})")


def violation(text: str) -> str | None:
    if BAD_WORDS.search(text):
        return "so'kinish yoki haqorat"
    if AD_PATTERN.search(text):
        return "reklama"
    return None


async def advertises_chat(client: TelegramClient, text: str, own_username: str) -> bool:
    """@nom boshqa kanal yoki guruhga ishora qilsa - reklama. Odamlarni belgilash (@ali) mumkin."""
    for name in MENTION.findall(text):
        if name.lower() == own_username.lower():
            continue
        try:
            entity = await client.get_entity(name)
        except (ValueError, RPCError):
            continue
        if not isinstance(entity, User):
            return True
    return False


async def find_group(client: TelegramClient, channel, state: dict):
    if not state.get("group_id"):
        full = await client(GetFullChannelRequest(channel))
        if not full.full_chat.linked_chat_id:
            return None
        state["group_id"] = full.full_chat.linked_chat_id
    return await client.get_entity(PeerChannel(state["group_id"]))


async def punish(client: TelegramClient, group, msg, sender: User, reason: str, mod: dict) -> None:
    strikes = mod.setdefault("strikes", {})
    key = str(sender.id)
    strikes[key] = strikes.get(key, 0) + 1
    duration = REPEAT_MUTE if strikes[key] >= REPEAT_LIMIT else timedelta(minutes=MUTE_MINUTES)
    try:
        await client.delete_messages(group, msg.id)
        await client.edit_permissions(group, sender, until_date=duration, send_messages=False)
    except RPCError as e:
        log.warning("Moderatsiya amalga oshmadi (%s): %r", sender.id, e)
        return
    name = sender.first_name or "Foydalanuvchi"
    muddat = "1 kunga" if duration == REPEAT_MUTE else f"{MUTE_MINUTES} daqiqaga"
    await client.send_message(
        group,
        f"⚠️ <a href=\"tg://user?id={sender.id}\">{name}</a>, {reason} uchun xabaringiz o'chirildi "
        f"va {muddat} yozish cheklandi.\nIltimos, bir-biringizni hurmat qiling 🤝",
        parse_mode="html")
    log.info("Moderatsiya: %s (%s) - %s, %s", name, sender.id, reason, muddat)


async def moderate(client: TelegramClient, channel, state: dict, own_username: str) -> None:
    group = await find_group(client, channel, state)
    if group is None:
        return
    mod = state.setdefault("moderator", {})
    me = await client.get_me()
    if "admins" not in mod:
        mod["admins"] = [u.id async for u in client.iter_participants(group, filter=ChannelParticipantsAdmins)]
    last_id = mod.get("last_id")
    if last_id is None:  # birinchi ishga tushish: eski xabarlarga tegmaymiz
        latest = await client.get_messages(group, limit=1)
        mod["last_id"] = latest[0].id if latest else 0
        return

    recent: dict[int, list[datetime]] = {}
    async for msg in client.iter_messages(group, min_id=last_id, reverse=True, limit=300):
        mod["last_id"] = max(mod["last_id"], msg.id)
        sender = await msg.get_sender()
        # kanal postlari (avto-uzatish), adminlar, botning o'zi - tekshirilmaydi
        if not isinstance(sender, User) or sender.id == me.id or sender.id in mod["admins"] or sender.bot:
            continue
        text = msg.message or ""
        reason = violation(text) if text else None
        if not reason and "@" in text and await advertises_chat(client, text, own_username):
            reason = "reklama"
        times = recent.setdefault(sender.id, [])
        times.append(msg.date)
        times[:] = [t for t in times if msg.date - t <= timedelta(seconds=FLOOD_SECONDS)]
        if not reason and len(times) > FLOOD_COUNT:
            reason, times[:] = "flud (juda ko'p xabar)", []
        if reason:
            await punish(client, group, msg, sender, reason, mod)
    # eski jazolar hisobini haftada bir tozalaymiz
    if datetime.now(timezone.utc).weekday() == 0 and mod.get("reset_day") != datetime.now().date().isoformat():
        mod["strikes"], mod["reset_day"] = {}, datetime.now().date().isoformat()
