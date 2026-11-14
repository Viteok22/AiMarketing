from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

ARCHIVE_DIR = Path("C:/Temp/opencode")


def pick_archive() -> Path | None:
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        return candidate if candidate.exists() else None
    archives = sorted(ARCHIVE_DIR.glob("ai-marketer-*.zip"), key=lambda p: p.stat().st_mtime)
    return archives[-1] if archives else None


def main() -> int:
    if not settings.telegram_bot_token or not settings.admin_telegram_id:
        print("Не заданы TELEGRAM_BOT_TOKEN или ADMIN_TELEGRAM_ID")
        return 1
    archive = pick_archive()
    if archive is None:
        print("Нет архива ai-marketer-*.zip")
        return 1
    size_mb = archive.stat().st_size / 1024 / 1024
    caption = (
        f"📦 Архив сервиса AI-маркетолог ({size_mb:.1f} МБ)\n\n"
        "Внутри: код, SQLite-снимок компаний, задач, реакций, расписаний и метрик, "
        "изображения, скрипты, README.md и INSTALL_OTHER_PC.md.\n\n"
        "Новое: недельный контент-календарь с автогенерацией, быстрые правки текста "
        "в боте (каждая добавляет новый вариант, старые остаются), меню кнопок, "
        "реакции и просмотры из t.me/s (включая посты вне бота), автопостинг по "
        "расписанию, отложенная публикация, AI-вывод, пер-компанийные тренды. "
        "Каждый менеджер видит только одну назначенную компанию.\n\n"
        "Важно: .env с токенами и API-ключами не включён. На новом ПК заполните "
        "его заново или перенесите отдельно. Не запускайте старый и новый бот "
        "с одним токеном одновременно."
    )
    proxies = None
    if settings.telegram_proxy:
        proxies = {"http": settings.telegram_proxy, "https": settings.telegram_proxy}
    with archive.open("rb") as handle:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument",
            data={"chat_id": settings.admin_telegram_id, "caption": caption},
            files={"document": (archive.name, handle, "application/zip")},
            proxies=proxies,
            timeout=300,
        )
    data = response.json()
    if not data.get("ok"):
        print("ERROR:", data.get("description"))
        return 2
    result = data["result"]
    print("ARCHIVE", archive)
    print("OK: message_id=", result["message_id"])
    print("file_id=", result.get("document", {}).get("file_id"))
    print("size=", result.get("document", {}).get("file_size"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
