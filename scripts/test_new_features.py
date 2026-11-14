import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.analytics as analytics_module  # noqa: E402
import app.scheduler as scheduler_module  # noqa: E402
from app.store import Store  # noqa: E402


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))
        return SimpleNamespace(chat_id=chat_id, message_id=1)


class FakeGenerator:
    async def complete(self, prompt, max_tokens=1000, temperature=0.4):
        return "AI-вывод: тест"


async def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    store = Store(db_path=Path(tmp.name) / "features.db")
    old_store = scheduler_module.store
    old_publish = scheduler_module._publish
    old_generate = scheduler_module._generate_task_variants
    old_fetch = analytics_module.fetch_channel_posts
    scheduler_module.store = store
    try:
        company = store.create_company({"name": "Feature Co", "channel_id": "@test"})
        task = store.create_task(company.id, 1, "Пост")
        store.update_task(
            task.id,
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            post_text="Заголовок\n\nТекст\n\n#тег",
            image_path="image.png",
        )

        async def fake_publish(bot, task, company):
            return SimpleNamespace(chat=SimpleNamespace(id=-100123), message_id=77)

        scheduler_module._publish = fake_publish
        bot = FakeBot()
        scheduler = scheduler_module.Scheduler(bot)
        await scheduler._publish_due_tasks()
        published = store.get_task(task.id)
        print("PUBLISHED", published.status, published.published_message_id)
        assert published.status == "published"
        assert published.published_message_id == 77
        assert store.get_publication_by_message(-100123, 77) == task.id

        schedule = store.upsert_schedule(
            company.id, enabled=True, interval_minutes=2, prompt="Автопост", user_id=1
        )
        print("SCHEDULE", schedule["enabled"], bool(schedule["next_run_at"]))
        assert schedule["enabled"] == 1

        async def fake_generate(*args, **kwargs):
            return None

        scheduler_module._generate_task_variants = fake_generate
        created = await scheduler.run_company_now(company.id)
        print("RUN_NOW", created)
        assert created.startswith("t-")

        store.save_publication(task.id, "@test", -100123, 77)
        store.save_post_metrics(task.id, 10, {"\U0001F44D": 2})

        def fake_fetch(channel, limit=50):
            return [
                {
                    "id": "test/77",
                    "channel": "test",
                    "message_id": 77,
                    "views": 1234,
                    "reactions": {"\U0001F44D": 5},
                    "score": 1234,
                    "url": "https://t.me/test/77",
                    "posted_at": None,
                    "text": "text",
                }
            ]

        analytics_module.fetch_channel_posts = fake_fetch
        service = analytics_module.AnalyticsService(store, FakeGenerator())
        updated = await service.refresh_metrics(company.id)
        print("METRICS_UPDATED", updated, store.get_post_metrics(task.id)["views"])
        assert updated == 1
        assert store.get_post_metrics(task.id)["views"] == 1234
        summary = await service.refresh_insight(company.id)
        print("INSIGHT", summary)
        assert "AI-вывод" in summary
        print("FEATURES_OK")
        return 0
    finally:
        scheduler_module.store = old_store
        scheduler_module._publish = old_publish
        scheduler_module._generate_task_variants = old_generate
        analytics_module.fetch_channel_posts = old_fetch
        store.close()
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
