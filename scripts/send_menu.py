from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

KEYBOARD = {
    "keyboard": [
        [{"text": "✍️ Новая задача"}, {"text": "📊 Статистика"}],
        [{"text": "🔥 Тренды"}, {"text": "🕒 Запланировано"}],
        [{"text": "🗓 Календарь"}, {"text": "❓ Помощь"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def main() -> int:
    if not settings.telegram_bot_token or not settings.admin_telegram_id:
        print("Не заданы TELEGRAM_BOT_TOKEN или ADMIN_TELEGRAM_ID")
        return 1
    proxies = None
    if settings.telegram_proxy:
        proxies = {"http": settings.telegram_proxy, "https": settings.telegram_proxy}
    response = requests.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        data={
            "chat_id": settings.admin_telegram_id,
            "text": (
                "Меню обновлено: новая задача, статистика, тренды, контент-календарь "
                "и запланированные посты — на кнопках внизу.\n\n"
                "В черновике появились «⚡ Быстрые правки»: короче, заголовок, цифры, "
                "проще, экспертнее и CTA. Каждая правка добавляет новый вариант текста, "
                "старые остаются.\n\n"
                "Переключения компаний в боте нет: доступ только к назначенной компании.\n\n"
                "«📊 Статистика» теперь сама обновляет просмотры и реакции из канала, "
                "включая посты, опубликованные вне бота."
            ),
            "reply_markup": json.dumps(KEYBOARD, ensure_ascii=False),
        },
        proxies=proxies,
        timeout=60,
    )
    data = response.json()
    if not data.get("ok"):
        print("ERROR:", data.get("description"))
        return 2
    print("OK: message_id=", data["result"]["message_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
