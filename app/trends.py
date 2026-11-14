from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.prompts import company_context

logger = logging.getLogger(__name__)


class TrendError(RuntimeError):
    """Не удалось собрать или проанализировать тренды."""


def _parse_count(value: str) -> int:
    value = (value or "").strip().replace(",", ".").upper()
    match = re.search(r"([\d.]+)\s*([KMB]?)", value)
    if not match:
        return 0
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    return int(number * multiplier)


def _extract_reactions(post) -> dict[str, int]:
    container = post.select_one(".tgme_widget_message_reactions")
    if not container:
        return {}
    reactions: dict[str, int] = {}
    items = container.select(".tgme_widget_message_reaction")
    if not items:
        # fallback: текст вида "👍 5 ❤️ 2"
        raw = container.get_text(" ", strip=True)
        for emoji, count in re.findall(r"([^\d\s]{1,4})\s*([\d.,]+[KMB]?)", raw):
            reactions[emoji] = _parse_count(count)
        return reactions
    for item in items:
        emoji_el = item.select_one(".tgme_widget_message_reaction_emoji")
        count_el = item.select_one(".tgme_widget_message_reaction_count")
        if emoji_el and count_el:
            reactions[emoji_el.get_text(strip=True)] = _parse_count(
                count_el.get_text(strip=True)
            )
        else:
            raw = item.get_text(" ", strip=True)
            match = re.match(r"([^\d\s]{1,4})\s*([\d.,]+[KMB]?)", raw)
            if match:
                reactions[match.group(1)] = _parse_count(match.group(2))
    return reactions


def fetch_channel_posts(channel: str, limit: int = 20) -> list[dict]:
    channel = channel.strip().lstrip("@").lower()
    if not channel:
        return []
    url = f"https://t.me/s/{channel}"
    proxies = None
    if settings.telegram_proxy:
        proxies = {"http": settings.telegram_proxy, "https": settings.telegram_proxy}
    response = requests.get(
        url,
        timeout=30,
        proxies=proxies,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    posts: list[dict] = []
    for post in soup.select("div.tgme_widget_message")[-limit:]:
        data_post = post.get("data-post") or ""
        if "/" not in data_post:
            continue
        post_channel, _, raw_id = data_post.partition("/")
        post_channel = post_channel.lower()
        try:
            message_id = int(raw_id)
        except ValueError:
            continue
        text_el = post.select_one(".tgme_widget_message_text")
        text = text_el.get_text("\n", strip=True) if text_el else ""
        if not text:
            continue
        views_el = post.select_one(".tgme_widget_message_views")
        views = _parse_count(views_el.get_text(strip=True)) if views_el else 0
        time_el = post.select_one("time")
        posted_at = time_el.get("datetime") if time_el else None
        reactions = _extract_reactions(post)
        score = views + sum(reactions.values()) * 50
        posts.append(
            {
                "id": f"{post_channel}/{message_id}",
                "channel": post_channel,
                "message_id": message_id,
                "text": text,
                "views": views,
                "reactions": reactions,
                "reactions_json": json.dumps(reactions, ensure_ascii=False),
                "score": score,
                "url": f"https://t.me/{post_channel}/{message_id}",
                "posted_at": posted_at,
            }
        )
    return posts


class TrendAnalyzer:
    def __init__(self, store, generator) -> None:
        self.store = store
        self.generator = generator
        self._lock = asyncio.Lock()

    def is_fresh(self, hours: int = 24, company_id: str | None = None) -> bool:
        insight = self.store.get_latest_trend_insight(company_id)
        if not insight:
            return False
        try:
            created = datetime.fromisoformat(insight["created_at"])
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - created < timedelta(hours=hours)

    async def refresh(self, company_id: str | None = None) -> dict:
        async with self._lock:
            sources = self.store.list_trend_sources()
            if not sources:
                raise TrendError("Не добавлено ни одного публичного канала для анализа.")
            all_posts: list[dict] = []
            errors: list[str] = []
            for source in sources:
                channel = source["channel"]
                try:
                    posts = await asyncio.to_thread(fetch_channel_posts, channel)
                    all_posts.extend(posts)
                    logger.info("Тренды: %s — %s постов", channel, len(posts))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"@{channel}: {exc}")
                    logger.warning("Не удалось прочитать @%s: %s", channel, exc)
            if not all_posts:
                raise TrendError(
                    "Не удалось получить посты ни из одного канала. "
                    + ("; ".join(errors[:3]) if errors else "")
                )
            self.store.save_trend_posts(all_posts)
            # Берём по 2 лучших поста с каждого канала, чтобы анализ не был
            # перекошен в сторону одного крупного источника.
            by_channel: dict[str, list[dict]] = {}
            for post in all_posts:
                by_channel.setdefault(post["channel"], []).append(post)
            candidates: list[dict] = []
            for channel_posts in by_channel.values():
                candidates.extend(
                    sorted(channel_posts, key=lambda post: post["score"], reverse=True)[:2]
                )
            top = sorted(candidates, key=lambda post: post["score"], reverse=True)[:8]
            company = (
                self.store.get_company(company_id)
                if company_id
                else self.store.get_default_company()
            )
            if company is None:
                raise TrendError("Компания для анализа трендов не найдена.")
            prompt = self._build_prompt(company, top)
            summary = await self.generator.complete(prompt, max_tokens=1400, temperature=0.5)
            ideas = self._parse_ideas(summary)
            self.store.save_trend_insight(summary, ideas, company.id)
            return {
                "summary": summary,
                "ideas": ideas,
                "posts": top,
                "errors": errors,
                "company_id": company.id,
            }

    def latest(self, company_id: str | None = None) -> dict | None:
        return self.store.get_latest_trend_insight(company_id)

    @staticmethod
    def _clean_summary(summary: str) -> str:
        return re.split(r"\nИДЕИ\s*:", summary or "", maxsplit=1, flags=re.I)[0].strip()

    def display_summary(self, insight: dict | None) -> str:
        if not insight:
            return ""
        return self._clean_summary(insight.get("summary") or "")

    def format_for_bot(self, insight: dict | None) -> str:
        if not insight:
            return "Анализ трендов ещё не делался. Нажмите «Обновить» в панели или отправьте /trends."
        text = self.display_summary(insight)
        ideas = insight.get("ideas") or []
        if ideas:
            text += "\n\n💡 Идеи для компании:\n"
            text += "\n".join(f"{index}. {idea}" for index, idea in enumerate(ideas, 1))
        return text[:3900]

    def format_for_prompt(self, insight: dict | None) -> str:
        if not insight:
            return ""
        text = self.display_summary(insight)
        ideas = insight.get("ideas") or []
        if ideas:
            text += "\nИдеи: " + "; ".join(ideas[:3])
        return text[:1800]

    def _build_prompt(self, company, posts: list[dict]) -> str:
        context = company_context(company) if company else "Компания: не указана"
        lines = [
            "Ты — аналитик контента и AI-маркетолог. Изучи популярные посты публичных Telegram-каналов.",
            context,
            "",
            "Популярные посты (текст, просмотры, реакции):",
        ]
        for post in posts:
            reactions = post.get("reactions") or {}
            reaction_text = ", ".join(f"{emoji} {count}" for emoji, count in reactions.items()) or "нет"
            lines.append(
                f"\n@{post['channel']} · просмотры {post['views']} · реакции: {reaction_text}\n"
                f"{post['text'][:900]}"
            )
        lines.append(
            "\nСделай анализ:\n"
            "1. 4–5 выводов: какие темы, форматы, хуки и структуры работают.\n"
            "2. 3 идеи оригинальных постов для компании. Не копируй чужой текст дословно; "
            "используй только принципы и форматы.\n"
            "3. Короткий итог, что делать в следующем посте.\n\n"
            "Ответь по-русски в формате:\n"
            "ВЫВОДЫ:\n<текст>\n\nИДЕИ:\n1. <идея>\n2. <идея>\n3. <идея>"
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_ideas(summary: str) -> list[str]:
        match = re.search(r"ИДЕИ\s*:\s*(.+)", summary, flags=re.S | re.I)
        if not match:
            return []
        block = match.group(1)
        ideas = []
        for line in block.splitlines():
            line = line.strip().lstrip("-•").strip()
            if not line:
                continue
            line = re.sub(r"^\d+[.)]\s*", "", line).strip()
            if line:
                ideas.append(line)
        return ideas[:3]
