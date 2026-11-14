import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.runtime import store  # noqa: E402
from app.telegram_bot import _handle_new_task  # noqa: E402


class FakeMessage:
    def __init__(self, bot: Bot, user_id: int) -> None:
        self.bot = bot
        self.from_user = SimpleNamespace(id=user_id, username="test")
        self.chat = SimpleNamespace(id=user_id)

    async def answer(self, text: str, **kwargs):
        return await self.bot.send_message(self.chat.id, text, **kwargs)


async def main() -> int:
    company = store.get_default_company()
    if company is None:
        print("Нет компании")
        return 1
    session = AiohttpSession(proxy=settings.telegram_proxy or None)
    bot = Bot(token=settings.telegram_bot_token, session=session)
    try:
        await _handle_new_task(
            FakeMessage(bot, settings.admin_telegram_id),
            company,
            "Подготовь пост про архитектурное стекло для фасадов бизнес-центра. "
            "Акцент на естественный свет и энергоэффективность. "
            "Аудитория — архитекторы и проектировщики.",
        )
        print("OK: варианты отправлены в Telegram")
        return 0
    finally:
        await bot.session.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
