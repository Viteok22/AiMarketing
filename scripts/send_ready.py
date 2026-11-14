from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

TEXT = (
    "Бот готов: DeepSeek подключён, канал @salavatsteklo_demo проверен.\n\n"
    "Отправьте задачу, например:\n"
    "«Подготовь пост про архитектурное стекло для фасадов бизнес-центра. "
    "Акцент на естественный свет и энергоэффективность. Аудитория — архитекторы и проектировщики.»\n\n"
    "Я пришлю черновик с обложкой и кнопками согласования."
)


def main() -> int:
    if not settings.telegram_bot_token or not settings.admin_telegram_id:
        print("Не заданы токен или ADMIN_TELEGRAM_ID")
        return 1
    proxies = None
    if settings.telegram_proxy:
        proxies = {"http": settings.telegram_proxy, "https": settings.telegram_proxy}
    response = requests.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        data={"chat_id": settings.admin_telegram_id, "text": TEXT},
        proxies=proxies,
        timeout=30,
    )
    data = response.json()
    if not data.get("ok"):
        print("ERROR:", data.get("description"))
        return 2
    print("OK: message_id=", data["result"]["message_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
