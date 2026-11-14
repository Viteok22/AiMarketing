from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.telegram_bot import _refresh_metrics_for, _stats_payload  # noqa: E402


async def main() -> int:
    if not settings.telegram_bot_token or not settings.admin_telegram_id:
        print("Не заданы TELEGRAM_BOT_TOKEN или ADMIN_TELEGRAM_ID")
        return 1
    company_id = "c-salavatsteklo"
    error = await _refresh_metrics_for(company_id)
    text, keyboard = _stats_payload(company_id, settings.admin_telegram_id, error)
    markup = {
        "inline_keyboard": [
            [
                {"text": button.text, "callback_data": button.callback_data}
                for button in row
            ]
            for row in keyboard.inline_keyboard
        ]
    }
    proxies = None
    if settings.telegram_proxy:
        proxies = {"http": settings.telegram_proxy, "https": settings.telegram_proxy}
    response = requests.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        data={
            "chat_id": settings.admin_telegram_id,
            "text": text,
            "reply_markup": json.dumps(markup, ensure_ascii=False),
            "link_preview_options": json.dumps({"is_disabled": True}),
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
    raise SystemExit(asyncio.run(main()))
