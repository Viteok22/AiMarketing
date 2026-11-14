from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import requests

from app.image_gen import AnyModelImageGenerator, ImageGenerationError
from app.models import Company, PostDraft
from app.prompts import (
    build_post_prompt,
    build_question_prompt,
    build_quick_edit_prompt,
    build_revision_prompt,
    parse_post_response,
)

logger = logging.getLogger(__name__)


class DeepSeekError(RuntimeError):
    """Ошибка DeepSeek API."""


class DeepSeekGenerator:
    """Текст — через DeepSeek API, изображение — anymodel GPT Image 2.5."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        images_dir: Path,
        image_base_url: str = "",
        image_api_key: str = "",
        image_model: str = "gpt-image-2.5",
        image_quality: str = "low",
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.images_dir = Path(images_dir)
        self.image_gen = AnyModelImageGenerator(
            base_url=image_base_url,
            api_key=image_api_key,
            model=image_model,
            quality=image_quality,
        )
        self._last_check: bool | None = None
        self._last_check_at = 0.0

    @property
    def started(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    @property
    def image_configured(self) -> bool:
        return self.image_gen.configured

    async def check_login(self) -> bool:
        if not self.started:
            return False
        now = time.monotonic()
        if self._last_check is not None and now - self._last_check_at < 30:
            return self._last_check
        try:
            await asyncio.to_thread(self._chat, "Ответь ровно одним словом: ок", 8, 0.0)
            self._last_check = True
        except Exception:  # noqa: BLE001
            self._last_check = False
        self._last_check_at = now
        return self._last_check

    def _chat(self, prompt: str, max_tokens: int = 1400, temperature: float = 0.7) -> str:
        if not self.started:
            raise DeepSeekError(
                "DeepSeek API не настроен: заполните DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY и DEEPSEEK_MODEL в .env"
            )
        url = self.base_url + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты — AI-маркетолог. Пиши по-русски, кратко и по делу. "
                        "Не выдумывай факты, цифры и характеристики, которых нет в материалах компании."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        last_error = ""
        for attempt in range(2):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=120)
                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    if isinstance(content, list):
                        content = "".join(str(part) for part in content)
                    return str(content).strip()
                last_error = f"HTTP {response.status_code}: {response.text[:400]}"
                if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    time.sleep(2)
                    continue
                break
            except requests.RequestException as exc:
                last_error = f"Сетевая ошибка: {exc}"
                if attempt == 0:
                    time.sleep(2)
                    continue
                break
        raise DeepSeekError(last_error or "DeepSeek API не ответил")

    async def complete(self, prompt: str, max_tokens: int = 1200, temperature: float = 0.5) -> str:
        """Публичный вызов DeepSeek для аналитики трендов и отчётов."""
        return await asyncio.to_thread(self._chat, prompt, max_tokens, temperature)

    async def generate_text_variants(
        self,
        company: Company,
        request: str,
        history: str,
        count: int = 2,
        feedback: str = "",
        trends: str = "",
        revision: tuple[str, str] | None = None,
        style_examples: str = "",
    ) -> list[PostDraft]:
        hints = [
            "акцент на практической пользе, цифрах и инженерных решениях",
            "акцент на архитектурной выразительности и эмоции от проекта",
            "акцент на надёжности, опыте и экспертизе компании",
        ]
        visual_hints = [
            "графический SMM-коллаж: календарь или блокнот на столе, карточки с иконками, фирменные цвета, без людей",
            "графический постер: ноутбук с дашбордом или стеклянная награда, 3D-стекло, карточки-шаги, без людей",
            "графический городской коллаж: силуэт города, стеклянная лента, предметные макеты, без людей",
        ]

        async def one(index: int) -> PostDraft:
            hint = (
                f"{hints[index % len(hints)]}; "
                f"визуальный акцент: {visual_hints[index % len(visual_hints)]}"
            )
            if revision:
                prompt = build_revision_prompt(
                    company,
                    revision[0],
                    revision[1],
                    feedback=feedback,
                    trends=trends,
                    variant_hint=hint,
                    style_examples=style_examples,
                )
            else:
                prompt = build_post_prompt(
                    company,
                    request,
                    history,
                    feedback=feedback,
                    trends=trends,
                    variant_hint=hint,
                    style_examples=style_examples,
                )
            raw = await asyncio.to_thread(
                self._chat, prompt, 1400, 0.60 + index * 0.08
            )
            draft = parse_post_response(raw)
            if len(draft.full_text) < 30:
                raise DeepSeekError(f"DeepSeek вернул слишком короткий вариант: {raw!r}")
            return draft

        results = await asyncio.gather(
            *[one(index) for index in range(max(1, count))], return_exceptions=True
        )
        drafts: list[PostDraft] = []
        errors: list[str] = []
        for index, item in enumerate(results):
            if isinstance(item, PostDraft):
                drafts.append(item)
            else:
                errors.append(str(item))
                logger.warning("Вариант текста %s не сгенерирован: %s", index, item)

        # Если параллельный проход не дал ничего (rate limit, пустой ответ),
        # пробуем те же варианты ещё раз последовательно.
        if not drafts:
            logger.warning("Параллельная генерация не дала вариантов, повторяю последовательно")
            for index in range(max(1, count)):
                try:
                    drafts.append(await one(index))
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
                    logger.warning(
                        "Последовательная генерация варианта %s не удалась: %s",
                        index,
                        exc,
                    )
        if not drafts:
            raise DeepSeekError(errors[0] if errors else "DeepSeek не вернул варианты")

        # Параллельные запросы иногда упираются в rate limit. Добираем
        # недостающие варианты последовательно, чтобы не терять выбор 2 из 2.
        next_index = len(drafts)
        for _ in range(max(1, count)):
            if len(drafts) >= count:
                break
            try:
                drafts.append(await one(next_index))
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                logger.warning(
                    "Повторная генерация варианта %s не удалась: %s", next_index, exc
                )
            next_index += 1
        return drafts[:count]

    async def generate_image_variants(
        self,
        company: Company,
        drafts: list[PostDraft],
        image_dir: Path,
        prefix: str,
        count: int = 2,
    ) -> list[str]:
        if not drafts:
            raise ImageGenerationError("Нет текстовых вариантов для генерации изображения")
        images: list[str] = []
        last_error: Exception | None = None
        for index in range(max(1, count)):
            draft = drafts[index % len(drafts)]
            target = Path(image_dir) / f"{prefix}-{index + 1}.png"
            try:
                saved = await asyncio.to_thread(
                    self.image_gen.generate,
                    draft.image_prompt,
                    company.name,
                    draft.headline or company.name,
                    target,
                    draft.body,
                    f"{prefix}-{index + 1}",
                )
                images.append(str(saved))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                text = str(exc).lower()
                if any(marker in text for marker in ("402", "balance", "баланс", "токен")):
                    break
        if not images:
            raise ImageGenerationError(
                str(last_error or "Не удалось сгенерировать ни одного изображения")
            )
        return images

    async def generate_variants(
        self,
        company: Company,
        request: str,
        history: str,
        image_dir: Path,
        image_prefix: str,
        text_count: int = 2,
        image_count: int = 2,
        feedback: str = "",
        trends: str = "",
        revision: tuple[str, str] | None = None,
        style_examples: str = "",
    ) -> tuple[list[PostDraft], list[str]]:
        drafts = await self.generate_text_variants(
            company,
            request,
            history,
            count=text_count,
            feedback=feedback,
            trends=trends,
            revision=revision,
            style_examples=style_examples,
        )
        images = await self.generate_image_variants(
            company,
            drafts,
            image_dir,
            image_prefix,
            count=image_count,
        )
        return drafts, images

    async def generate_post(
        self, company: Company, request: str, history: str, image_path: Path
    ) -> tuple[str, str]:
        prompt = build_post_prompt(company, request, history)
        raw = await asyncio.to_thread(self._chat, prompt, 1400, 0.7)
        draft = parse_post_response(raw)
        if len(draft.full_text) < 30:
            raise DeepSeekError(f"DeepSeek вернул слишком короткий ответ: {raw!r}")
        saved = self.image_gen.generate(
            prompt=draft.image_prompt,
            company_name=company.name,
            headline=draft.headline or company.name,
            target=Path(image_path),
            subtitle=draft.body,
            seed=str(image_path),
        )
        return raw, str(saved)

    async def revise_post(
        self,
        company: Company,
        previous_post: str,
        comment: str,
        image_path: Path,
        regenerate_image: bool = False,
    ) -> tuple[str, str]:
        prompt = build_revision_prompt(company, previous_post, comment)
        raw = await asyncio.to_thread(self._chat, prompt, 1400, 0.6)
        draft = parse_post_response(raw)
        if len(draft.full_text) < 30:
            raise DeepSeekError(f"DeepSeek вернул слишком короткий ответ: {raw!r}")
        # Обложка всегда пересобирается: в ней напечатан заголовок,
        # поэтому после правок она должна соответствовать новому тексту.
        saved = self.image_gen.generate(
            prompt=draft.image_prompt,
            company_name=company.name,
            headline=draft.headline or company.name,
            target=Path(image_path),
            subtitle=draft.body,
            seed=str(image_path),
        )
        return raw, str(saved)

    async def quick_edit(
        self,
        company: Company,
        post_text: str,
        instruction: str,
        style_examples: str = "",
    ) -> PostDraft:
        """Быстрая правка выбранного текста без перегенерации изображения."""
        prompt = build_quick_edit_prompt(
            company, post_text, instruction, style_examples=style_examples
        )
        raw = await asyncio.to_thread(self._chat, prompt, 1400, 0.55)
        draft = parse_post_response(raw)
        if len(draft.full_text) < 30:
            raise DeepSeekError(f"DeepSeek вернул слишком короткий ответ: {raw!r}")
        return draft

    async def answer_question(self, company: Company, history: str, question: str) -> str:
        prompt = build_question_prompt(company, history, question)
        return await asyncio.to_thread(self._chat, prompt, 700, 0.4)

    async def close(self) -> None:
        return None
