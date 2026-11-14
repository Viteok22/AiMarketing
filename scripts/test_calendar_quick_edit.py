from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.scheduler as scheduler_module  # noqa: E402
import app.telegram_bot as bot_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import PostDraft  # noqa: E402
from app.store import Store  # noqa: E402


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))
        return SimpleNamespace(message_id=1)

    async def send_chat_action(self, chat_id, action):
        return None


class FakeMessage:
    def __init__(self, bot: FakeBot, user_id: int) -> None:
        self.bot = bot
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return SimpleNamespace(message_id=1)


class FakeCallbackMessage:
    def __init__(self) -> None:
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeCallback:
    def __init__(self, data: str, user_id: int, bot: FakeBot) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.bot = bot
        self.message = FakeCallbackMessage()
        self.answer_text = ""

    async def answer(self, text="", show_alert=False):
        self.answer_text = text


async def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    store = Store(db_path=Path(tmp.name) / "calendar.db")
    old_bot_store = bot_module.store
    old_sched_store = scheduler_module.store
    old_generate = scheduler_module._generate_task_variants
    old_refresh = bot_module._refresh_compact_ui
    old_quick = bot_module.generator.quick_edit
    bot_module.store = store
    scheduler_module.store = store
    try:
        user_id = settings.admin_telegram_id or 1
        company = store.create_company(
            {"name": "Calendar Co", "channel_id": "@test"}
        )
        store.bind_user(user_id, company.id)

        # Календарь в боте.
        entry = store.create_calendar_entry(
            company.id,
            datetime.now(timezone.utc) + timedelta(hours=2),
            "Тема недели: стекло для фасадов",
            user_id=user_id,
        )
        text, keyboard = bot_module._calendar_payload(user_id)
        print("CALENDAR_TEXT")
        print(text)
        assert "Тема недели" in text
        assert "Запланировано" in text
        assert any(
            button.callback_data.startswith("calendar:refresh:")
            for row in keyboard.inline_keyboard
            for button in row
        )
        message = FakeMessage(FakeBot(), user_id)
        await bot_module._send_calendar(message)
        assert message.answers and "Тема недели" in message.answers[0][0]

        # Автогенерация из календаря.
        async def fake_generate(bot, task, company, request, history, **kwargs):
            store.update_task(
                task.id, status="pending_approval", post_text="Черновик"
            )

        scheduler_module._generate_task_variants = fake_generate
        scheduler = scheduler_module.Scheduler(FakeBot())
        auto_entry = store.create_calendar_entry(
            company.id,
            datetime.now(timezone.utc) - timedelta(minutes=1),
            "Автотема",
            user_id=user_id,
        )
        task_id = await scheduler.run_calendar_entry_now(auto_entry["id"])
        saved = store.get_calendar_entry(auto_entry["id"])
        print("AUTO_ENTRY", saved["status"], saved["task_id"])
        assert saved["status"] == "draft"
        assert saved["task_id"] == task_id
        store.update_task(task_id, status="published")
        assert store.get_calendar_entry(auto_entry["id"])["status"] == "published"

        # Быстрая правка текста в боте.
        task = store.create_task(company.id, user_id, "Пост для правки")
        text_variant = store.add_variant(
            task.id, "text", 1, 0, content="Старый текст"
        )
        store.add_variant(task.id, "text", 1, 1, content="Второй текст")
        image_variant = store.add_variant(
            task.id, "image", 1, 0, image_path="one.png"
        )
        store.select_variant(task.id, "text", text_variant.id)
        store.select_variant(task.id, "image", image_variant.id)
        store.update_task(task.id, status="pending_approval")
        task = store.get_task(task.id)
        texts, images = bot_module._selected_variants(task)
        main_labels = [
            button.text
            for row in bot_module._compact_keyboard(task, texts, images).inline_keyboard
            for button in row
        ]
        edit_labels = [
            button.text
            for row in bot_module._quick_edit_keyboard(task.id).inline_keyboard
            for button in row
        ]
        print("QUICK_EDIT_BUTTONS", main_labels[-5:], edit_labels)
        assert "⚡ Быстрые правки" in main_labels
        assert "✂️ Короче" in edit_labels
        assert "📣 CTA" in edit_labels

        async def fake_quick_edit(company, post_text, instruction):
            return PostDraft(
                headline="Новый заголовок",
                body="Новый текст",
                hashtags="#тег",
                full_text="Новый заголовок\n\nНовый текст\n\n#тег",
                image_prompt="new image prompt",
            )

        refreshed = []

        async def fake_refresh(bot, updated_task):
            refreshed.append(updated_task.id)

        bot_module.generator.quick_edit = fake_quick_edit
        bot_module._refresh_compact_ui = fake_refresh
        bot = FakeBot()
        callback = FakeCallback(f"qedit:{task.id}:shorter", user_id, bot)
        await bot_module.callback_quick_edit(callback, bot)
        updated_task = store.get_task(task.id)
        variants_after = store.list_variants(
            task.id, kind="text", batch_no=store.latest_batch(task.id)
        )
        selected = store.get_variant(updated_task.selected_text_id or "")
        print(
            "QUICK_EDIT",
            len(variants_after),
            selected.content if selected else "",
            callback.answer_text,
        )
        assert len(variants_after) == 3
        assert store.get_variant(text_variant.id).content == "Старый текст"
        assert selected is not None and "Новый заголовок" in selected.content
        assert "Новый заголовок" in updated_task.post_text
        images_after = store.list_variants(
            task.id, kind="image", batch_no=store.latest_batch(task.id)
        )
        nav_labels = [
            button.text
            for row in bot_module._compact_keyboard(
                updated_task, variants_after, images_after
            ).inline_keyboard
            for button in row
        ]
        print("QUICK_EDIT_NAV", nav_labels)
        assert "🔄 Другой текст (3/3)" in nav_labels
        assert refreshed == [task.id]
        assert any("добавлен новый вариант" in message for _, message in bot.messages)
        print("CALENDAR_QUICK_EDIT_OK")
        return 0
    finally:
        bot_module.store = old_bot_store
        scheduler_module.store = old_sched_store
        scheduler_module._generate_task_variants = old_generate
        bot_module._refresh_compact_ui = old_refresh
        bot_module.generator.quick_edit = old_quick
        store.close()
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
