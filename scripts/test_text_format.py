import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.generator import DeepSeekGenerator  # noqa: E402
from app.prompts import format_post_html  # noqa: E402
from app.store import Store  # noqa: E402


async def main() -> int:
    store = Store()
    company = store.get_default_company()
    generator = DeepSeekGenerator(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        images_dir=settings.images_dir,
        image_base_url=settings.anymodel_base_url,
        image_api_key=settings.anymodel_api_key,
        image_model=settings.anymodel_image_model,
        image_quality=settings.anymodel_image_quality,
    )
    try:
        drafts = await generator.generate_text_variants(
            company,
            "Подготовь пост про архитектурное стекло для фасадов бизнес-центра. "
            "Акцент на естественный свет и энергоэффективность. "
            "Аудитория — архитекторы и проектировщики.",
            "История пуста.",
            count=1,
        )
        text = drafts[0].full_text
        output = ROOT / "data" / "text_format.txt"
        output.write_text(
            f"PLAIN:\n{text}\n\nHTML:\n{format_post_html(text)}\n\nLEN: {len(text)}",
            encoding="utf-8",
        )
        print("TEXT_LEN", len(text), "OUT", output)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
