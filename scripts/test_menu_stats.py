from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.telegram_bot as bot_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.store import Store  # noqa: E402


class FakeAnalytics:
    def __init__(self) -> None:
        self.calls = 0

    async def refresh_metrics(self, company_id=None) -> int:
        self.calls += 1
        return 1


class FakeBot:
    def __init__(self) -> None:
        self.calls = []

    async def send_chat_action(self, chat_id, action):
        self.calls.append(("chat_action", action))

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.calls.append(("send_message", text, reply_markup))
        return SimpleNamespace(message_id=1)


class FakeMessage:
    def __init__(self, bot: FakeBot, user_id: int) -> None:
        self.bot = bot
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup, kwargs.get("link_preview_options")))
        return SimpleNamespace(message_id=1)


class FakeCallback:
    def __init__(self, data: str, user_id: int) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.answer_text = ""

    async def answer(self, text="", show_alert=False):
        self.answer_text = text


class FakeHandler:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, event, data):
        self.called = True


async def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    store = Store(db_path=Path(tmp.name) / "menu.db")
    old_store = bot_module.store
    old_analytics = bot_module.analytics_service
    bot_module.store = store
    bot_module.analytics_service = FakeAnalytics()
    try:
        user_id = settings.admin_telegram_id or 1
        company = store.create_company({"name": "Menu Co", "channel_id": "@test"})
        store.bind_user(user_id, company.id)
        task = store.create_task(company.id, user_id, "Пост про стекло")
        store.update_task(task.id, status="published", post_text="Текст поста")
        store.save_publication(task.id, "@test", -100123, 9)
        store.save_post_metrics(task.id, 7, {"\U0001F44D": 2})
        store.save_channel_posts(
            company.id,
            [
                {
                    "message_id": 9,
                    "text": "Пост про стекло",
                    "views": 7,
                    "reactions": {"\U0001F44D": 2},
                    "posted_at": None,
                    "url": "https://t.me/test/9",
                },
                {
                    "message_id": 10,
                    "text": "Пост вне бота",
                    "views": 3,
                    "reactions": {"\U0001F44E": 1},
                    "posted_at": None,
                    "url": "https://t.me/test/10",
                },
            ],
        )

        text, keyboard = bot_module._stats_payload(company.id, user_id)
        print("STATS_TEXT")
        print(text)
        assert "👁 7" in text
        assert "👍 2" in text
        assert "Посты канала вне бота" in text
        assert "Пост вне бота" in text
        assert "👎 1" in text
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        assert "🔄 Обновить" in labels
        assert "🔥 Тренды" in labels

        bot = FakeBot()
        message = FakeMessage(bot, user_id)
        await bot_module._send_stats(message)
        print("SEND_STATS", len(message.answers), bot.calls[0][0])
        assert message.answers and "📊 Статистика" in message.answers[0][0]
        preview = message.answers[0][2]
        print("NO_PREVIEW", bool(preview and preview.is_disabled))
        assert preview is not None and preview.is_disabled
        assert bot_module.analytics_service.calls == 1

        # Без привязки к компании доступ закрыт, чужие данные не показываются.
        anon_id = 999999
        assert bot_module._access_denied(anon_id)
        assert not bot_module._access_denied(user_id)
        anon = FakeMessage(bot, anon_id)
        await bot_module._send_stats(anon)
        assert anon.answers and "Доступ к боту не настроен" in anon.answers[0][0]
        assert bot_module.analytics_service.calls == 1

        # Менеджер другой компании видит только свою компанию.
        other_company = store.create_company({"name": "Other Co", "channel_id": "@other"})
        other_user = 888888
        store.bind_user(other_user, other_company.id)
        assert bot_module._resolve_company_filter("all", other_user) == other_company.id
        other_text, _ = bot_module._stats_payload(other_company.id, other_user)
        assert "Menu Co" not in other_text
        assert "Пост про стекло" not in other_text

        # Чужую задачу нельзя открыть по callback даже при пересылке сообщения.
        middleware = bot_module.TaskOwnerMiddleware()
        denied = FakeCallback(f"approve:{task.id}", other_user)
        denied_handler = FakeHandler()
        await middleware(denied_handler, denied, {})
        print("TASK_ACCESS_DENIED", denied.answer_text)
        assert not denied_handler.called
        assert "Нет доступа" in denied.answer_text

        allowed = FakeCallback(f"approve:{task.id}", user_id)
        allowed_handler = FakeHandler()
        await middleware(allowed_handler, allowed, {})
        assert allowed_handler.called

        scheduled = store.create_task(company.id, user_id, "Отложенный пост")
        store.update_task(
            scheduled.id,
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        sched_text, sched_keyboard = bot_module._scheduled_payload(user_id)
        print("SCHED_TEXT")
        print(sched_text)
        assert "Запланированные публикации" in sched_text
        assert f"sched:cancel:{scheduled.id}" in [
            button.callback_data
            for row in sched_keyboard.inline_keyboard
            for button in row
        ]

        await bot_module._send_scheduled(message)
        assert message.answers and "Запланированные публикации" in message.answers[-1][0]
        print("MENU_OK")
        return 0
    finally:
        bot_module.store = old_store
        bot_module.analytics_service = old_analytics
        store.close()
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
