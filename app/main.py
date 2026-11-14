from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import runtime
from app.admin import router as admin_router
from app.config import settings
from app.scheduler import Scheduler
from app.telegram_bot import router as telegram_router, set_bot_commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot: Bot | None = None
    polling_task: asyncio.Task | None = None
    maintenance_task: asyncio.Task | None = None
    scheduler_task: asyncio.Task | None = None

    runtime.store.recover_interrupted_tasks()

    if settings.telegram_bot_token:
        session = AiohttpSession(proxy=settings.telegram_proxy or None)
        bot = Bot(token=settings.telegram_bot_token, session=session)
        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher.include_router(telegram_router)
        try:
            await set_bot_commands(bot)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось установить команды бота")

        async def _run_bot() -> None:
            while True:
                try:
                    runtime.bot_started = True
                    logger.info("Telegram-бот запущен")
                    await dispatcher.start_polling(
                        bot,
                        allowed_updates=[
                            "message",
                            "callback_query",
                            "message_reaction",
                            "message_reaction_count",
                        ],
                    )
                    runtime.bot_started = False
                    logger.warning("Telegram-бот остановился, перезапуск через 10 секунд")
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    runtime.bot_started = False
                    logger.exception("Telegram-бот остановлен из-за ошибки, перезапуск через 10 секунд")
                await asyncio.sleep(10)

        async def _daily_maintenance() -> None:
            while True:
                now = datetime.now()
                target = now.replace(hour=9, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())
                try:
                    for company in runtime.store.list_companies():
                        try:
                            await runtime.analytics_service.refresh_metrics(company.id)
                        except Exception:  # noqa: BLE001
                            logger.exception("Ошибка обновления метрик %s", company.id)
                    reports = runtime.store.list_reaction_reports(limit=10)
                    if settings.admin_telegram_id and reports:
                        lines = ["📊 Аналитика за сутки"]
                        for report in reports:
                            reactions = report["reactions"]
                            counts = ", ".join(
                                f"{emoji} {count}"
                                for emoji, count in (reactions.get("counts") or {}).items()
                            ) or "—"
                            lines.append(
                                f"«{report['request'][:60]}»: {counts}, "
                                f"просмотры {report.get('views', 0)} "
                                f"(позитив {reactions['positive']}, негатив {reactions['negative']})"
                            )
                        await bot.send_message(
                            settings.admin_telegram_id, "\n".join(lines)[:4000]
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("Ошибка ежедневного отчёта")
                try:
                    if not runtime.trend_analyzer.is_fresh(hours=24):
                        await runtime.trend_analyzer.refresh()
                except Exception:  # noqa: BLE001
                    logger.exception("Ошибка ежедневного анализа трендов")

        polling_task = asyncio.create_task(_run_bot())
        maintenance_task = asyncio.create_task(_daily_maintenance())
        runtime.scheduler = Scheduler(bot)
        scheduler_task = asyncio.create_task(runtime.scheduler.loop())
        app.state.bot = bot
        app.state.dispatcher = dispatcher
    else:
        logger.warning("TELEGRAM_BOT_TOKEN не задан — Telegram-бот не запущен")

    for problem in settings.validate():
        logger.warning("Настройки: %s", problem)

    try:
        yield
    finally:
        runtime.bot_started = False
        if polling_task is not None:
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        if maintenance_task is not None:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
        runtime.scheduler = None
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:  # noqa: BLE001
                pass
        await runtime.generator.close()
        runtime.store.close()
        logger.info("Приложение остановлено")


app = FastAPI(title="Автономный AI-маркетолог", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    same_site="lax",
    https_only=False,
)
app.mount(
    "/static",
    StaticFiles(directory=str(settings.base_dir / "app" / "static")),
    name="static",
)
app.include_router(admin_router)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.app_host, port=settings.app_port, log_level="info")
