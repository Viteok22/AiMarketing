from __future__ import annotations

from app.analytics import AnalyticsService
from app.config import settings
from app.generator import DeepSeekGenerator
from app.store import Store
from app.trends import TrendAnalyzer

store = Store()
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
trend_analyzer = TrendAnalyzer(store, generator)
analytics_service = AnalyticsService(store, generator)
# Обратная совместимость имени в старых импортах.
chatgpt = generator
scheduler = None
bot_started = False
