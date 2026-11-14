from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.generator import DeepSeekGenerator  # noqa: E402


async def main() -> int:
    generator = DeepSeekGenerator(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        images_dir=settings.images_dir,
    )
    ok = await generator.check_login()
    print(
        json.dumps(
            {
                "ok": ok,
                "base_url": settings.deepseek_base_url,
                "model": settings.deepseek_model,
                "key_set": bool(settings.deepseek_api_key),
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
