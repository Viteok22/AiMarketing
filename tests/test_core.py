from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import relative_path, resolve_path, settings
from app.generator import DeepSeekGenerator
from app.image_gen import AnyModelImageGenerator
from app.prompts import (
    classify_intent,
    fit_caption,
    format_post_html,
    parse_post_response,
    shorten_post,
    split_caption,
)
from app.store import Store
from app.trends import TrendAnalyzer, fetch_channel_posts


class PromptTests(unittest.TestCase):
    def test_parse_structured_response(self) -> None:
        raw = """ЗАГОЛОВОК: Стекло для будущего
ТЕКСТ:
Первый абзац.
Второй абзац.
ХЕШТЕГИ: #стекло #архитектура
ИЗОБРАЖЕНИЕ: photorealistic modern glass facade, daylight, no text, no logos"""
        draft = parse_post_response(raw)
        self.assertEqual(draft.headline, "Стекло для будущего")
        self.assertIn("Первый абзац.", draft.body)
        self.assertIn("#стекло", draft.hashtags)
        self.assertIn("Стекло для будущего", draft.full_text)
        self.assertIn("#архитектура", draft.full_text)
        self.assertIn("photorealistic", draft.image_prompt)
        self.assertNotIn("ИЗОБРАЖЕНИЕ", draft.full_text)

    def test_parse_plain_response(self) -> None:
        draft = parse_post_response("Заголовок\n\nОсновной текст без разметки.")
        self.assertEqual(draft.headline, "Заголовок")
        self.assertIn("Основной текст", draft.body)

    def test_classify_question(self) -> None:
        self.assertEqual(classify_intent("Почему ты выбрал такой заголовок?"), "question")
        self.assertEqual(classify_intent("Расскажи, какие форматы сработали", has_last_task=True), "question")

    def test_classify_task(self) -> None:
        self.assertEqual(classify_intent("Подготовь пост про архитектурное стекло"), "task")
        self.assertEqual(classify_intent("Сделай публикацию о стеклопакетах"), "task")
        self.assertEqual(classify_intent("Нужен пост к открытию завода"), "task")

    def test_split_caption(self) -> None:
        short, rest = split_caption("Короткий текст", limit=20)
        self.assertEqual(short, "Короткий текст")
        self.assertEqual(rest, "")

        text = "Слово " * 40
        first, second = split_caption(text, limit=50)
        self.assertLessEqual(len(first), 50)
        self.assertTrue(second)
        self.assertEqual((first + " " + second).replace("  ", " ").strip(), text.strip())

    def test_fit_caption_preserves_headline_and_hashtags(self) -> None:
        raw = "Заголовок\n\n" + "Очень длинный текст. " * 120 + "\n\n#стекло #фасады"
        result = fit_caption(raw, limit=1000)
        self.assertLessEqual(len(result), 1000)
        self.assertTrue(result.startswith("Заголовок"))
        self.assertIn("#стекло", result)
        self.assertIn("…", result)

    def test_shorten_post_preserves_cta_and_hashtags(self) -> None:
        raw = (
            "Заголовок\n\n"
            + "Первый абзац с подробностями. " * 8
            + "\n\n• Пункт один. " * 4
            + "\n\n• Пункт два. " * 4
            + "\n\nКак вы работаете с фасадами?\n\n#стекло #фасады"
        )
        result = shorten_post(raw, limit=650)
        self.assertLessEqual(len(result), 650)
        self.assertTrue(result.startswith("Заголовок"))
        self.assertIn("Как вы работаете с фасадами?", result)
        self.assertIn("#стекло", result)

    def test_format_post_html(self) -> None:
        html = format_post_html("Заголовок\n\nТекст <важно> & детали\n\n#тег")
        self.assertIn("<b>Заголовок</b>", html)
        self.assertIn("&lt;важно&gt;", html)
        self.assertIn("&amp;", html)
        self.assertIn("#тег", html)

    def test_relative_image_paths(self) -> None:
        absolute = settings.base_dir / "data" / "images" / "test.png"
        self.assertEqual(relative_path(absolute), "data/images/test.png")
        self.assertEqual(resolve_path("data/images/test.png"), absolute.resolve())


class TrendTests(unittest.TestCase):
    def test_parse_channel_html(self) -> None:
        html = """
        <div class="tgme_widget_message" data-post="test/1">
          <div class="tgme_widget_message_text">Post one</div>
          <span class="tgme_widget_message_views">1.5K</span>
          <div class="tgme_widget_message_reactions">
            <span class="tgme_widget_message_reaction">
              <span class="tgme_widget_message_reaction_emoji">👍</span>
              <span class="tgme_widget_message_reaction_count">3</span>
            </span>
          </div>
          <time datetime="2026-01-01T10:00:00+00:00"></time>
        </div>
        <div class="tgme_widget_message" data-post="test/2">
          <div class="tgme_widget_message_text">Post two</div>
          <span class="tgme_widget_message_views">2</span>
          <time datetime="2026-01-02T10:00:00+00:00"></time>
        </div>
        """
        response = MagicMock()
        response.text = html
        response.raise_for_status.return_value = None
        with patch("app.trends.requests.get", return_value=response):
            posts = fetch_channel_posts("test")
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["views"], 1500)
        self.assertEqual(posts[0]["reactions"], {"👍": 3})
        self.assertEqual(posts[0]["score"], 1650)
        self.assertEqual(posts[1]["views"], 2)

    def test_parse_ideas(self) -> None:
        summary = "ВЫВОДЫ:\nЧто-то работает.\n\nИДЕИ:\n1. Первая идея\n2. Вторая идея\n3. Третья идея"
        ideas = TrendAnalyzer._parse_ideas(summary)
        self.assertEqual(len(ideas), 3)
        self.assertEqual(ideas[0], "Первая идея")
        self.assertNotIn("ИДЕИ", TrendAnalyzer._clean_summary(summary))


class GeneratorTests(unittest.TestCase):
    def test_anymodel_not_configured_without_key(self) -> None:
        generator = AnyModelImageGenerator(
            base_url="https://anymodel.org/v1",
            api_key="",
            model="gpt-image-2.5",
        )
        self.assertFalse(generator.configured)

    def test_deepseek_not_started_without_key(self) -> None:
        generator = DeepSeekGenerator(
            base_url="https://api.deepseek.com/v1",
            api_key="",
            model="deepseek-chat",
            images_dir=Path("data/images"),
        )
        self.assertFalse(generator.started)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = Store(db_path=self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_company_and_task_flow(self) -> None:
        company = self.store.create_company(
            {"name": "Тестовая компания", "description": "Проверка"}
        )
        self.assertIsNotNone(self.store.get_company(company.id))
        self.assertTrue(self.store.bind_user(42, company.id))
        self.assertEqual(self.store.get_company_for_user(42).id, company.id)
        self.assertIsNone(self.store.get_company_for_user(99))

        task = self.store.create_task(company.id, 42, "Подготовь пост")
        self.assertEqual(task.status, "drafting")
        self.store.update_task(
            task.id, status="pending_approval", post_text="Готовый пост"
        )
        updated = self.store.get_task(task.id)
        self.assertEqual(updated.status, "pending_approval")
        self.assertEqual(updated.post_text, "Готовый пост")
        self.assertEqual(self.store.get_pending_task(42).id, task.id)

    def test_variants_and_persistence(self) -> None:
        company = self.store.create_company({"name": "Persist Co"})
        task = self.store.create_task(company.id, 7, "Сделай пост")
        text1 = self.store.add_variant(
            task.id, "text", 1, 1, content="Первый текст", image_prompt="prompt one"
        )
        text2 = self.store.add_variant(
            task.id, "text", 1, 2, content="Второй текст", image_prompt="prompt two"
        )
        image1 = self.store.add_variant(task.id, "image", 1, 1, image_path="one.png")
        self.store.select_variant(task.id, "text", text2.id)
        self.store.select_variant(task.id, "image", image1.id)
        self.store.update_task(task.id, status="pending_approval")
        self.store.save_publication(task.id, "@chan", -100123, 77)

        self.store.close()
        reopened = Store(db_path=self.db_path)
        try:
            persisted = reopened.get_task(task.id)
            self.assertEqual(persisted.selected_text_id, text2.id)
            self.assertEqual(persisted.post_text, "Второй текст")
            self.assertEqual(persisted.image_path, "one.png")
            variants = reopened.list_variants(task.id, kind="text", batch_no=1)
            self.assertEqual(len(variants), 2)
            self.assertEqual(reopened.get_publication_by_message(-100123, 77), task.id)
        finally:
            reopened.close()

    def test_reactions_summary(self) -> None:
        company = self.store.create_company({"name": "Reaction Co"})
        task = self.store.create_task(company.id, 1, "Пост")
        self.store.save_user_reactions(task.id, 1, ["\U0001F44D", "\U0001F525"])
        self.store.save_user_reactions(task.id, 2, ["\U0001F44D", "\U0001F44E"])
        summary = self.store.get_reaction_summary(task.id)
        self.assertEqual(summary["positive"], 3)
        self.assertEqual(summary["negative"], 1)
        self.assertEqual(summary["total"], 4)

    def test_schedule_and_scheduled_publication(self) -> None:
        company = self.store.create_company({"name": "Schedule Co"})
        schedule = self.store.upsert_schedule(
            company.id, enabled=True, interval_minutes=1, prompt="Автопост", user_id=1
        )
        self.assertEqual(schedule["enabled"], 1)
        self.assertTrue(schedule["next_run_at"])
        due = self.store.list_due_schedules(
            now=datetime.now(timezone.utc) + timedelta(minutes=2)
        )
        self.assertTrue(any(item["company_id"] == company.id for item in due))
        self.store.mark_schedule_run(company.id, 1)
        self.assertTrue(self.store.get_schedule(company.id)["last_run_at"])

        task = self.store.create_task(company.id, 1, "Пост")
        self.store.update_task(
            task.id,
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        due_tasks = self.store.list_due_scheduled_tasks()
        self.assertTrue(any(item.id == task.id for item in due_tasks))

    def test_metrics_and_insights(self) -> None:
        company = self.store.create_company({"name": "Metrics Co"})
        task = self.store.create_task(company.id, 1, "Пост")
        self.store.update_task(task.id, status="published")
        self.store.save_publication(task.id, "@test", -100123, 42)
        self.store.save_post_metrics(task.id, 150, {"\U0001F44D": 3})
        publication = self.store.list_publications(limit=1, company_id=company.id)[0]
        self.assertEqual(publication["views"], 150)
        self.assertEqual(self.store.get_post_metrics(task.id)["views"], 150)
        summary = self.store.get_reaction_summary(task.id)
        self.assertEqual(summary["positive"], 3)
        self.assertEqual(summary["source"], "counts")

        # Даже если событие reaction не пришло, /stats берёт реакции из t.me/s.
        self.store.db.execute(
            "DELETE FROM reaction_counts WHERE task_id = ?", (task.id,)
        )
        fallback = self.store.get_reaction_summary(task.id)
        self.assertEqual(fallback["positive"], 3)
        self.assertEqual(fallback["source"], "metrics")

        self.store.save_trend_insight("Вывод", ["Идея"], company.id)
        self.assertEqual(
            self.store.get_latest_trend_insight(company.id)["summary"], "Вывод"
        )
        other = self.store.create_company({"name": "Other Co"})
        self.assertIsNone(self.store.get_latest_trend_insight(other.id))

        self.store.save_analytics_insight(company.id, "AI-вывод")
        self.assertEqual(
            self.store.get_latest_analytics_insight(company.id)["summary"], "AI-вывод"
        )

    def test_channel_posts_and_scheduled_list(self) -> None:
        company = self.store.create_company({"name": "Channel Co"})
        self.store.save_channel_posts(
            company.id,
            [
                {
                    "message_id": 10,
                    "text": "Пост вне бота",
                    "views": 25,
                    "reactions": {"\u2764": 2, "\U0001F44E": 1},
                    "posted_at": "2026-11-14T10:00:00+00:00",
                    "url": "https://t.me/test/10",
                }
            ],
        )
        posts = self.store.list_channel_posts(company.id)
        self.assertEqual(posts[0]["views"], 25)
        self.assertEqual(posts[0]["reactions"]["\u2764"], 2)

        task = self.store.create_task(company.id, 1, "Отложенный пост")
        self.store.update_task(
            task.id,
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        scheduled = self.store.list_scheduled_tasks(user_id=1)
        self.assertTrue(any(item.id == task.id for item in scheduled))
        self.assertEqual(self.store.list_scheduled_tasks(user_id=2), [])

    def test_calendar_entries_and_quick_edit(self) -> None:
        company = self.store.create_company({"name": "Calendar Co"})
        planned = datetime.now(timezone.utc) + timedelta(hours=2)
        entry = self.store.create_calendar_entry(
            company.id, planned, "Пост про стекло", user_id=1
        )
        self.assertEqual(entry["status"], "planned")
        entries = self.store.list_calendar_entries(
            company.id,
            start=datetime.now(timezone.utc),
            end=datetime.now(timezone.utc) + timedelta(days=1),
        )
        self.assertEqual(len(entries), 1)
        due = self.store.list_due_calendar_entries(
            now=datetime.now(timezone.utc) + timedelta(hours=3)
        )
        self.assertTrue(any(item["id"] == entry["id"] for item in due))

        task = self.store.create_task(company.id, 1, "Пост")
        self.store.update_calendar_entry(
            entry["id"], status="draft", task_id=task.id
        )
        self.store.update_task(task.id, status="published")
        self.assertEqual(
            self.store.get_calendar_entry(entry["id"])["status"], "published"
        )

        second = self.store.create_calendar_entry(
            company.id, planned, "Отменяемая тема", user_id=1
        )
        self.assertTrue(self.store.cancel_calendar_entry(second["id"], company.id))
        self.assertEqual(
            self.store.get_calendar_entry(second["id"])["status"], "cancelled"
        )

        text = self.store.add_variant(task.id, "text", 1, 0, content="Старый текст")
        self.store.select_variant(task.id, "text", text.id)
        self.store.update_variant(text.id, content="Новый текст")
        self.assertEqual(self.store.get_variant(text.id).content, "Новый текст")
        self.assertEqual(self.store.get_task(task.id).post_text, "Новый текст")

    def test_interrupted_task_marked_failed(self) -> None:
        company = self.store.create_company({"name": "Crash Co"})
        task = self.store.create_task(company.id, 1, "Пост")
        self.store.close()
        reopened = Store(db_path=self.db_path)
        try:
            reopened.recover_interrupted_tasks()
            self.assertEqual(reopened.get_task(task.id).status, "failed")
            self.assertIn("Повторить", reopened.get_task(task.id).error)
        finally:
            reopened.close()
            self.store = Store(db_path=self.db_path)


if __name__ == "__main__":
    unittest.main()
