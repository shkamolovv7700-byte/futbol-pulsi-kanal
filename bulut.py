"""
Kanalni bulutda (GitHub Actions) yuritish: har ishga tushishda shu soatning postini joylaydi.
Kanal egasi - ikkinchi akkaunt. Kirish kaliti GitHub maxfiy o'zgaruvchisida (TG_SESSION) saqlanadi.

Kerakli muhit o'zgaruvchilari: TG_API_ID, TG_API_HASH, TG_SESSION, KANAL_USERNAME, TZ=Asia/Tashkent
"""

import asyncio
import logging
import os

from telethon import TelegramClient
from telethon.sessions import StringSession

import kanal

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logging.getLogger("telethon").setLevel(logging.WARNING)


async def main() -> None:
    client = TelegramClient(StringSession(os.environ["TG_SESSION"]),
                            int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("TG_SESSION eskirgan yoki noto'g'ri - kanal_ochish.py orqali qayta kiring.")
    try:
        await kanal.run_once(client)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
