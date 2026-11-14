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

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, **kwargs):
        self.calls.append(("send_photo", caption, parse_mode))
        return SimpleNamespace(chat=SimpleNamespace(id=-100123), message_id=42)

    async def send_message(self, chat_id, text, parse_mode=None, **kwargs):
        self.calls.append(("send_message", text, parse_mode))
        return SimpleNamespace(chat=SimpleNamespace(id=-100123), message_id=43)


async def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    store = Store(db_path=Path(tmp.name) / "publish.db")
    try:
        company = store.create_company({"name": "Test Co", "channel_id": "@test"})
        task = store.create_task(company.id, 1, "Пост")
        text = (
            "Свет и энергоэффективность: стекло для фасада БЦ\n\n"
            + "Фасад бизнес-центра — это инженерная система. " * 40
            + "\n\n• Первый тезис\n• Второй тезис\n\n"
            "Какой параметр для вас ключевой? Поделитесь в комментариях.\n\n"
            "#стекло #фасады #энергоэффективность"
        )
        store.update_task(task.id, post_text=text, image_path="image.png")
        task = store.get_task(task.id)
        bot = FakeBot()
        await bot_module._publish(bot, task, company)
        print("CALLS", [call[0] for call in bot.calls])
        assert len(bot.calls) == 1
        assert bot.calls[0][0] == "send_photo"
        assert bot.calls[0][2] == "HTML"
        caption = bot.calls[0][1]
        assert "<b>" in caption
        assert caption.count("<b>") == 1
        print("CAPTION_LEN", len(caption))
        print("SINGLE_POST_OK")
        return 0
    finally:
        store.close()
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
