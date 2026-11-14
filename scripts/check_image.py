from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.image_gen import AnyModelImageGenerator, ImageGenerationError  # noqa: E402


def main() -> int:
    generator = AnyModelImageGenerator(
        base_url=settings.anymodel_base_url,
        api_key=settings.anymodel_api_key,
        model=settings.anymodel_image_model,
    )
    if not generator.configured:
        print("ANYMODEL_API_KEY не задан в .env")
        return 1
    target = settings.images_dir / "anymodel-test.png"
    try:
        generator.generate(
            prompt=(
                "photorealistic architectural photography, modern glass facade of a business center, "
                "natural daylight, clean minimal composition, no text, no logos"
            ),
            company_name="АО «Салаватстекло»",
            headline="Стекло для современного фасада",
            target=target,
        )
        print(f"OK: {target} ({target.stat().st_size} bytes)")
        return 0
    except ImageGenerationError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
