from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


async def main() -> int:
    if not settings.telegram_bot_token or not settings.telegram_channel_id:
        print("Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
        return 1
    session = AiohttpSession(proxy=settings.telegram_proxy or None)
    bot = Bot(token=settings.telegram_bot_token, session=session)
    try:
        message = await bot.send_message(
            chat_id=settings.telegram_channel_id,
            text="Проверка публикации: AI-маркетолог подключён к каналу.",
        )
        print(f"OK: сообщение отправлено, message_id={message.message_id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 2
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
