"""
Kanallarni bulutda (GitHub Actions) yuritish: har ishga tushishda shu soatning postlarini joylaydi.
Kanal egasi - ikkinchi akkaunt. Kirish kaliti GitHub maxfiy o'zgaruvchisida (TG_SESSION) saqlanadi.

  python bulut.py        - Futbol Pulsi (kanal.py)
  python bulut.py cs2    - CS2 Pulsi (cs2.py)
  python bulut.py kitob  - Kitob Pulsi (kitob.py)
  python bulut.py kitob --loop 330 --until 23
                         - uzluksiz rejim: 330 daqiqa yoki soat 23:00 gacha har daqiqada tekshiradi
                           (GitHub jadvali soatlab kechikadi - postlar aniq vaqtida chiqishi uchun)

Kerakli muhit o'zgaruvchilari: TG_API_ID, TG_API_HASH, TG_SESSION, KANAL_USERNAME, TZ=Asia/Tashkent
"""

import argparse
import asyncio
import importlib
import logging
import os
import subprocess
import time
from datetime import datetime

from telethon import TelegramClient
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("bulut")


def git_save(path) -> None:
    """Holat faylini darhol repoga yozadi: keyingi ishga tushish takroriy post qilmasin."""
    def git(*args: str) -> int:
        return subprocess.run(["git", *args], capture_output=True).returncode

    git("add", str(path))
    if git("diff", "--cached", "--quiet") == 0:
        return
    git("commit", "-m", f"{path.stem.split('_')[0]} holati")
    if git("pull", "--rebase", "-X", "theirs") != 0:
        git("rebase", "--abort")
    if git("push") != 0:
        log.error("Holatni GitHub'ga yozib bo'lmadi")


async def loop(module, client: TelegramClient, minutes: int, until_hour: int) -> None:
    end = time.monotonic() + minutes * 60
    while time.monotonic() < end and datetime.now().hour < until_hour:
        try:
            await module.run_once(client)
        except Exception as e:
            log.error("Xato: %r", e)
        git_save(module.STATE_FILE)
        await asyncio.sleep(62 - datetime.now().second)  # har daqiqaning boshida


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", nargs="?", default="kanal", choices=["kanal", "cs2", "kitob"])
    parser.add_argument("--loop", type=int, default=0, help="uzluksiz rejim davomiyligi, daqiqa")
    parser.add_argument("--until", type=int, default=24, help="shu soatda to'xtaydi (Toshkent vaqti)")
    args = parser.parse_args()
    module = importlib.import_module(args.name)
    client = TelegramClient(StringSession(os.environ["TG_SESSION"]),
                            int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("TG_SESSION eskirgan yoki noto'g'ri - kanal_ochish.py orqali qayta kiring.")
    try:
        if args.loop:
            await loop(module, client, args.loop, args.until)
        else:
            await module.run_once(client)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
