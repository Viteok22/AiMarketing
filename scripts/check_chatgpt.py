from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.chatgpt_web import ChatGPTSession  # noqa: E402
from app.config import settings  # noqa: E402


async def main() -> int:
    session = ChatGPTSession(
        profile_dir=settings.chatgpt_profile_dir,
        headless=settings.chatgpt_headless,
        url=settings.chatgpt_url,
    )
    try:
        logged_in = await session.check_login()
        print(json.dumps({"logged_in": logged_in}, ensure_ascii=False))
        return 0 if logged_in else 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"logged_in": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        await session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
