from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.generator import DeepSeekGenerator  # noqa: E402
from app.prompts import parse_post_response  # noqa: E402
from app.store import Store  # noqa: E402


async def main() -> int:
    store = Store()
    company = store.get_default_company()
    if company is None:
        print("Нет компании")
        return 1
    generator = DeepSeekGenerator(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        images_dir=settings.images_dir,
        image_base_url=settings.anymodel_base_url,
        image_api_key=settings.anymodel_api_key,
        image_model=settings.anymodel_image_model,
    )
    target = ROOT / "data" / "images" / "real-test.png"
    raw, image = await generator.generate_post(
        company=company,
        request=(
            "Подготовь пост про архитектурное стекло для фасадов бизнес-центра. "
            "Акцент на естественный свет и энергоэффективность. Аудитория — архитекторы и проектировщики."
        ),
        history="История пуста.",
        image_path=target,
    )
    draft = parse_post_response(raw)
    out = ROOT / "data" / "last_generation.txt"
    out.write_text(
        f"HEADLINE: {draft.headline}\n\n{draft.full_text}\n",
        encoding="utf-8",
    )
    print("HEADLINE_LEN:", len(draft.headline))
    print("TEXT_LEN:", len(draft.full_text))
    print("TEXT_FILE:", out)
    print("IMAGE:", image, Path(image).stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
