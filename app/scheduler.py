from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot

from app import runtime
from app.config import settings
from app.runtime import store
from app.telegram_bot import _generate_task_variants, _publish

logger = logging.getLogger(__name__)


class Scheduler:
    """Автогенерация черновиков и отложенная публикация."""

    METRICS_INTERVAL = 300

    def __init__(self, bot: Bot | None = None) -> None:
        self.bot = bot
        self._lock = asyncio.Lock()
        self._last_metrics_at = 0.0

    async def _create_draft(
        self, company, user_id: int, prompt: str
    ) -> str:
        if self.bot is None:
            raise RuntimeError("Telegram-бот не запущен")
        task = store.create_task(company.id, int(user_id), prompt)
        store.add_history(int(user_id), "user", prompt)
        await _generate_task_variants(
            self.bot,
            task,
            company,
            request=prompt,
            history=store.get_history_text(int(user_id)),
        )
        return task.id

    async def run_company_now(self, company_id: str) -> str:
        company = store.get_company(company_id)
        if company is None:
            raise RuntimeError("Компания не найдена")
        schedule = store.get_schedule(company_id) or {}
        user_id = schedule.get("user_id") or settings.admin_telegram_id
        if not user_id:
            raise RuntimeError("Не задан получатель автопостинга")
        prompt = (schedule.get("prompt") or "").strip() or (
            "Подготовь пост на актуальную тему для компании. Используй свежие тренды, "
            "обратную связь по реакциям и базу знаний. Не выдумывай факты."
        )
        return await self._create_draft(company, int(user_id), prompt)

    async def run_calendar_entry_now(self, entry_id: str) -> str:
        entry = store.get_calendar_entry(entry_id)
        if entry is None:
            raise RuntimeError("Запись календаря не найдена")
        if entry["status"] not in {"planned", "failed"}:
            raise RuntimeError("Запись уже запущена, отменена или опубликована")
        company = store.get_company(entry["company_id"])
        if company is None:
            store.update_calendar_entry(
                entry_id, status="failed", error="Компания не найдена"
            )
            raise RuntimeError("Компания не найдена")
        user_id = entry.get("user_id") or settings.admin_telegram_id
        if not user_id:
            store.update_calendar_entry(
                entry_id, status="failed", error="Не задан получатель"
            )
            raise RuntimeError("Не задан получатель публикации")
        prompt = (entry.get("topic") or "").strip() or (
            "Подготовь пост на актуальную тему для компании. Используй свежие тренды, "
            "обратную связь по реакциям и базу знаний. Не выдумывай факты."
        )
        store.update_calendar_entry(entry_id, status="generating", error="")
        try:
            task_id = await self._create_draft(company, int(user_id), prompt)
        except Exception as exc:  # noqa: BLE001
            store.update_calendar_entry(entry_id, status="failed", error=str(exc))
            raise
        task = store.get_task(task_id)
        if task is None or task.status == "failed":
            store.update_calendar_entry(
                entry_id,
                status="failed",
                error=(task.error if task else "Задача не создана"),
            )
        else:
            store.update_calendar_entry(
                entry_id, status="draft", task_id=task_id, error=""
            )
        return task_id

    async def _publish_due_tasks(self) -> None:
        due = store.list_due_scheduled_tasks()
        for task in due:
            company = store.get_company(task.company_id)
            if company is None:
                store.update_task(task.id, status="failed", error="Компания не найдена")
                continue
            store.update_task(task.id, status="publishing")
            try:
                published = await _publish(self.bot, task, company)
                channel = (company.channel_id or settings.telegram_channel_id).strip()
                store.save_publication(
                    task.id, channel, published.chat.id, published.message_id
                )
                store.update_task(
                    task.id,
                    status="published",
                    published_message_id=published.message_id,
                    published_chat_id=published.chat.id,
                    error="",
                )
                await self.bot.send_message(
                    task.user_id,
                    "✅ Запланированный пост опубликован. "
                    "Реакции и просмотры — по кнопке «📊 Статистика».",
                )
                logger.info("Scheduled publication %s published", task.id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Scheduled publication failed: %s", task.id)
                store.update_task(task.id, status="failed", error=str(exc))
                try:
                    await self.bot.send_message(
                        task.user_id,
                        f"❌ Не удалось опубликовать запланированный пост: {exc}",
                    )
                except Exception:  # noqa: BLE001
                    pass

    async def _run_due_schedules(self) -> None:
        due = store.list_due_schedules()
        for schedule in due:
            company_id = schedule["company_id"]
            # Занимаем слот до генерации, чтобы не запустить дважды.
            store.mark_schedule_run(company_id, int(schedule["interval_minutes"]))
            try:
                task_id = await self.run_company_now(company_id)
                logger.info("Scheduled draft %s created for %s", task_id, company_id)
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled draft failed for %s", company_id)

    async def _run_due_calendar(self) -> None:
        due = store.list_due_calendar_entries()
        for entry in due:
            try:
                task_id = await self.run_calendar_entry_now(entry["id"])
                logger.info(
                    "Calendar draft %s created from entry %s", task_id, entry["id"]
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Calendar entry failed: %s", entry["id"])
                user_id = entry.get("user_id") or settings.admin_telegram_id
                if self.bot is not None and user_id:
                    try:
                        await self.bot.send_message(
                            int(user_id),
                            f"❌ Не удалось подготовить пост из календаря: {exc}",
                        )
                    except Exception:  # noqa: BLE001
                        pass

    async def _refresh_metrics_periodically(self) -> None:
        now = time.monotonic()
        if now - self._last_metrics_at < self.METRICS_INTERVAL:
            return
        self._last_metrics_at = now
        for company in store.list_companies():
            try:
                await runtime.analytics_service.refresh_metrics(company.id)
            except Exception:  # noqa: BLE001
                logger.exception("Периодическое обновление метрик %s", company.id)

    async def loop(self) -> None:
        if self.bot is None:
            return
        while True:
            try:
                if not runtime.bot_started:
                    await asyncio.sleep(10)
                    continue
                async with self._lock:
                    await self._publish_due_tasks()
                    await self._run_due_schedules()
                    await self._run_due_calendar()
                await self._refresh_metrics_periodically()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(30)
