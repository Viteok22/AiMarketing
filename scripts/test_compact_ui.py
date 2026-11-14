import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.telegram_bot as bot_module  # noqa: E402
from app.store import Store  # noqa: E402


class FakeBot:
    def __init__(self) -> None:
        self.calls = []
        self._message_id = 100

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kwargs):
        self._message_id += 1
        self.calls.append(("send_photo", self._message_id, (caption or "")[:40]))
        return SimpleNamespace(message_id=self._message_id)

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self._message_id += 1
        self.calls.append(("send_message", self._message_id, text[:40]))
        return SimpleNamespace(message_id=self._message_id)

    async def edit_message_media(self, **kwargs):
        self.calls.append(("edit_media", kwargs.get("message_id")))

    async def edit_message_caption(self, **kwargs):
        self.calls.append(("edit_caption", kwargs.get("message_id"), (kwargs.get("caption") or "")[:40]))

    async def edit_message_text(self, **kwargs):
        self.calls.append(("edit_text", kwargs.get("message_id"), (kwargs.get("text") or "")[:40]))

    async def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", message_id))


async def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    store = Store(db_path=Path(tmp.name) / "ui.db")
    old_store = bot_module.store
    bot_module.store = store
    try:
        company = store.create_company({"name": "UI Co", "channel_id": "@test"})
        task = store.create_task(company.id, 1, "Пост")
        text1 = store.add_variant(task.id, "text", 1, 0, content="Короткий текст", image_prompt="p1")
        text2 = store.add_variant(task.id, "text", 1, 1, content="Другой короткий текст", image_prompt="p2")
        image1 = store.add_variant(task.id, "image", 1, 0, image_path="one.png")
        image2 = store.add_variant(task.id, "image", 1, 1, image_path="two.png")
        store.select_variant(task.id, "text", text1.id)
        store.select_variant(task.id, "image", image1.id)
        store.update_task(task.id, status="pending_approval")
        task = store.get_task(task.id)

        bot = FakeBot()
        await bot_module._send_variant_ui(bot, task, company)
        print("SEND_CALLS", [call[0] for call in bot.calls])
        ui = store.get_task_ui(task.id)
        print("UI", ui)
        assert ui["photo_message_id"] == 101
        assert ui["text_message_id"] is None
        assert any(call[0] == "send_photo" for call in bot.calls)

        # Переключение текста: фото не меняется, должна обновиться подпись.
        bot.calls.clear()
        await bot_module.callback_next_text(
            SimpleNamespace(
                data=f"nexttext:{task.id}",
                from_user=SimpleNamespace(id=1),
                answer=_noop,
            ),
            bot,
        )
        print("NEXT_TEXT_CALLS", [call[0] for call in bot.calls])
        assert any(call[0] == "edit_caption" for call in bot.calls)

        # Переключение фото: должна обновиться медиа.
        bot.calls.clear()
        await bot_module.callback_next_image(
            SimpleNamespace(
                data=f"nextimage:{task.id}",
                from_user=SimpleNamespace(id=1),
                answer=_noop,
            ),
            bot,
        )
        print("NEXT_IMAGE_CALLS", [call[0] for call in bot.calls])
        assert any(call[0] == "edit_media" for call in bot.calls)

        # Длинный текст: фото + отдельное текстовое сообщение.
        long_task = store.create_task(company.id, 1, "Длинный пост")
        long1 = store.add_variant(
            long_task.id,
            "text",
            1,
            0,
            content="Длинный текст. " * 100,
            image_prompt="p-long-1",
        )
        long2 = store.add_variant(
            long_task.id,
            "text",
            1,
            1,
            content="Другой длинный текст. " * 100,
            image_prompt="p-long-2",
        )
        long_image = store.add_variant(
            long_task.id, "image", 1, 0, image_path="long.png"
        )
        store.select_variant(long_task.id, "text", long1.id)
        store.select_variant(long_task.id, "image", long_image.id)
        store.update_task(long_task.id, status="pending_approval")
        long_task = store.get_task(long_task.id)
        bot.calls.clear()
        await bot_module._send_variant_ui(bot, long_task, company)
        long_ui = store.get_task_ui(long_task.id)
        print("LONG_UI", long_ui)
        assert long_ui["text_message_id"] is not None
        assert any(call[0] == "send_message" for call in bot.calls)

        bot.calls.clear()
        await bot_module.callback_next_text(
            SimpleNamespace(
                data=f"nexttext:{long_task.id}",
                from_user=SimpleNamespace(id=1),
                answer=_noop,
            ),
            bot,
        )
        print("LONG_NEXT_TEXT_CALLS", [call[0] for call in bot.calls])
        assert any(call[0] == "edit_caption" for call in bot.calls)
        assert any(call[0] == "edit_text" for call in bot.calls)

        # Планирование времени и одобрение с отложенной публикацией.
        await bot_module.callback_pubset(
            SimpleNamespace(
                data=f"pubset:{task.id}:60",
                from_user=SimpleNamespace(id=1),
                message=None,
                answer=_noop,
            ),
            bot,
        )
        planned = store.get_task(task.id)
        print("SCHEDULED_AT", bool(planned.scheduled_at))
        assert planned.scheduled_at is not None
        await bot_module.callback_approve(
            SimpleNamespace(
                data=f"approve:{task.id}",
                from_user=SimpleNamespace(id=1),
                message=SimpleNamespace(edit_reply_markup=_noop),
                answer=_noop,
            ),
            bot,
        )
        scheduled = store.get_task(task.id)
        print("SCHEDULED_STATUS", scheduled.status)
        assert scheduled.status == "scheduled"
        assert store.get_publication(task.id) is None
        print("COMPACT_UI_OK")
        return 0
    finally:
        bot_module.store = old_store
        store.close()
        tmp.cleanup()


async def _noop(*args, **kwargs):
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
