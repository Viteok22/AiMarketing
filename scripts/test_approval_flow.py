import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.telegram_bot as bot_module  # noqa: E402
from app.store import Store  # noqa: E402


async def noop(*args, **kwargs):
    return None


async def fake_publish(bot, task, company):
    return SimpleNamespace(chat=SimpleNamespace(id=-100123), message_id=999)


async def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    store = Store(db_path=Path(tmp.name) / "approval.db")
    old_store = bot_module.store
    old_publish = bot_module._publish
    bot_module.store = store
    bot_module._publish = fake_publish
    try:
        company = store.create_company({"name": "Test Co", "channel_id": "@test"})
        task = store.create_task(company.id, 1, "Сделай пост")
        text1 = store.add_variant(task.id, "text", 1, 0, content="Текст 1", image_prompt="p1")
        text2 = store.add_variant(task.id, "text", 1, 1, content="Текст 2", image_prompt="p2")
        image1 = store.add_variant(task.id, "image", 1, 0, image_path="one.png")
        image2 = store.add_variant(task.id, "image", 1, 1, image_path="two.png")
        store.select_variant(task.id, "text", text2.id)
        store.select_variant(task.id, "image", image2.id)
        store.update_task(task.id, status="pending_approval")

        callback = SimpleNamespace(
            data=f"approve:{task.id}",
            from_user=SimpleNamespace(id=1),
            message=SimpleNamespace(edit_text=noop),
            answer=noop,
        )
        fake_bot = SimpleNamespace(send_message=noop)
        await bot_module.callback_approve(callback, fake_bot)
        updated = store.get_task(task.id)
        print("STATUS", updated.status)
        print("PUBLISHED_ID", updated.published_message_id, updated.published_chat_id)
        print("TEXT", updated.post_text, "IMAGE", updated.image_path)
        print("PUBLICATION", store.get_publication_by_message(-100123, 999))
        assert updated.status == "published"
        assert updated.published_message_id == 999
        assert updated.published_chat_id == -100123
        assert updated.post_text == "Текст 2"
        assert updated.image_path == "two.png"
        assert store.get_publication_by_message(-100123, 999) == task.id
        print("APPROVAL_OK")
        return 0
    finally:
        bot_module.store = old_store
        bot_module._publish = old_publish
        store.close()
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
