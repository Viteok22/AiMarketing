from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.chatgpt_web import ChatGPTSession  # noqa: E402


async def main() -> int:
    print("=" * 64)
    print("Вход в ChatGPT для AI-маркетолога")
    print("=" * 64)
    print(f"Профиль: {settings.chatgpt_profile_dir}")
    print("Откроется отдельное окно Chrome. Войдите в свой аккаунт ChatGPT.")
    print("После появления поля «Спросить ChatGPT» окно можно закрыть.")
    print("Ожидание входа — до 15 минут...")
    print("=" * 64)

    session = ChatGPTSession(
        profile_dir=settings.chatgpt_profile_dir,
        headless=False,
        url=settings.chatgpt_url,
    )
    try:
        await session.start()
        if await session.check_login():
            print("LOGIN_OK: сессия ChatGPT активна.")
            (ROOT / "data" / "chatgpt_login_status.txt").write_text(
                f"LOGIN_OK {datetime.now().isoformat(timespec='seconds')}",
                encoding="utf-8",
            )
            await session.close()
            return 0

        print("Войдите в аккаунт в открытом окне. Ждём...")
        for _ in range(180):
            if await session.is_logged_in(wait_ms=1000):
                print("LOGIN_OK: сессия ChatGPT активна.")
                (ROOT / "data" / "chatgpt_login_status.txt").write_text(
                    f"LOGIN_OK {datetime.now().isoformat(timespec='seconds')}",
                    encoding="utf-8",
                )
                await session.close()
                return 0
            await asyncio.sleep(5)

        print("LOGIN_TIMEOUT: вход не выполнен за 15 минут.")
        (ROOT / "data" / "chatgpt_login_status.txt").write_text(
            f"LOGIN_TIMEOUT {datetime.now().isoformat(timespec='seconds')}",
            encoding="utf-8",
        )
        await session.close()
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        await session.close()
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
