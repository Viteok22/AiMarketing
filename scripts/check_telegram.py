from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


def api(method: str, **params):
    proxies = None
    if settings.telegram_proxy:
        proxies = {"http": settings.telegram_proxy, "https": settings.telegram_proxy}
    response = requests.get(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}",
        params=params,
        proxies=proxies,
        timeout=20,
    )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or "Telegram API error")
    return data["result"]


def main() -> int:
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN не задан в .env")
        return 1

    try:
        me = api("getMe")
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось проверить токен: {exc}")
        return 2
    print(f"Бот: @{me['username']} (id={me['id']})")

    channel = settings.telegram_channel_id
    if not channel:
        print("TELEGRAM_CHANNEL_ID не задан в .env")
        return 1

    try:
        chat = api("getChat", chat_id=channel)
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось открыть канал {channel}: {exc}")
        print("Проверьте, что канал существует и бот добавлен в него.")
        return 3

    print(f"Канал: {chat.get('title')} ({chat.get('username') or chat.get('id')})")

    try:
        member = api("getChatMember", chat_id=channel, user_id=me["id"])
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось проверить права бота: {exc}")
        return 4

    status = member.get("status")
    can_post = bool(member.get("can_post_messages"))
    print(f"Статус бота в канале: {status}, can_post_messages={can_post}")

    if status not in {"administrator", "creator"} or not can_post:
        print("Нужно добавить бота администратором канала с правом «Публикация сообщений».")
        return 5

    print("OK: бот может публиковать в канал.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
