import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.image_gen import BrandedImageGenerator

generator = BrandedImageGenerator()
samples = [
    (
        "АО «Салаватстекло»",
        "Свет и энергоэффективность: стекло для фасадов бизнес-центра",
        "Фасад бизнес-центра — это микроклимат внутри и расходы на годы вперёд.",
        "task-1",
    ),
    (
        "АО «Салаватстекло»",
        "Почему архитекторы выбирают крупные форматы стекла",
        "Крупноформатное стекло помогает раскрыть замысел без лишних швов.",
        "task-2",
    ),
    (
        "АО «Салаватстекло»",
        "60 лет опыта в стекольной отрасли",
        "Собственное производство полного цикла и лаборатория для проверки характеристик.",
        "task-3",
    ),
]

for index, (company, headline, subtitle, seed) in enumerate(samples, 1):
    target = ROOT / "data" / "images" / f"variant-{index}.png"
    generator.generate(company, headline, target, subtitle=subtitle, seed=seed)
    print(target, target.stat().st_size)
