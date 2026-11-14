from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.prompts import company_context
from app.trends import fetch_channel_posts

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Сбор просмотров и AI-вывод по публикациям компании."""

    def __init__(self, store, generator) -> None:
        self.store = store
        self.generator = generator

    async def refresh_metrics(self, company_id: str | None = None) -> int:
        """Обновляет просмотры и реакции из t.me/s.

        Сохраняет все посты канала, чтобы реакции учитывались даже для
        публикаций, сделанных не через бота (или потерявших привязку к задаче).
        """
        if company_id:
            companies = [self.store.get_company(company_id)]
        else:
            companies = self.store.list_companies()

        updated = 0
        for company in companies:
            if company is None:
                continue
            channel = (company.channel_id or settings.telegram_channel_id or "").strip()
            if not channel.startswith("@"):
                continue
            name = channel.lstrip("@").lower()
            try:
                posts = await asyncio.to_thread(fetch_channel_posts, name, 50)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось получить метрики @%s: %s", name, exc)
                continue

            self.store.save_channel_posts(company.id, posts)
            by_message = {post["message_id"]: post for post in posts}
            publications = self.store.list_publications(limit=50, company_id=company.id)
            for publication in publications:
                match = by_message.get(publication["message_id"])
                if match is None:
                    continue
                self.store.save_post_metrics(
                    publication["task_id"],
                    match.get("views", 0),
                    match.get("reactions") or {},
                )
                updated += 1
        return updated

    def list_unlinked_posts(self, company_id: str, limit: int = 10) -> list[dict]:
        linked = {
            publication["message_id"]
            for publication in self.store.list_publications(
                limit=100, company_id=company_id
            )
        }
        return [
            post
            for post in self.store.list_channel_posts(company_id, limit=limit)
            if post["message_id"] not in linked and post.get("reactions")
        ]

    async def refresh_insight(self, company_id: str) -> str:
        company = self.store.get_company(company_id)
        if company is None:
            raise RuntimeError("Компания не найдена")
        reports = self.store.list_reaction_reports(limit=10, company_id=company_id)
        unlinked = self.list_unlinked_posts(company_id)
        if not reports and not unlinked:
            summary = (
                "Пока нет опубликованных постов с метриками. "
                "Опубликуйте пост и обновите просмотры, затем повторите AI-вывод."
            )
            self.store.save_analytics_insight(company_id, summary)
            return summary

        lines = []
        for report in reports:
            reactions = report["reactions"]
            counts = ", ".join(
                f"{emoji} {count}" for emoji, count in reactions["counts"].items()
            ) or "нет"
            lines.append(
                f"«{report['request'][:90]}»: просмотры {report.get('views', 0)}, "
                f"реакции {counts}, позитив {reactions['positive']}, "
                f"негатив {reactions['negative']}"
            )
        for post in unlinked:
            reactions = post.get("reactions") or {}
            counts = ", ".join(
                f"{emoji} {count}" for emoji, count in reactions.items()
            ) or "нет"
            lines.append(
                f"Пост канала без задачи «{(post.get('text') or '')[:90]}»: "
                f"просмотры {post.get('views', 0)}, реакции {counts}"
            )

        prompt = f"""Ты — аналитик контента компании «{company.name}». Не выдумывай цифры, используй только данные ниже.

{company_context(company)}

Публикации и метрики:
{chr(10).join(lines)}

Сделай короткий отчёт:
1. 3–5 выводов: какие темы и форматы зашли, что не зашло.
2. 3 рекомендации для следующих постов.
3. Какие темы/форматы масштабировать.

Пиши по-русски, по делу, без общих фраз. Учитывай, что просмотры и реакции — разные сигналы: просмотры показывают охват, реакции — вовлечённость."""
        summary = await self.generator.complete(prompt, max_tokens=1200, temperature=0.4)
        self.store.save_analytics_insight(company_id, summary)
        return summary
