from __future__ import annotations

import asyncio
import base64
import io
import re
import time
from pathlib import Path
from typing import Optional

from PIL import Image
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from app.models import Company
from app.prompts import build_image_prompt, build_post_prompt, build_question_prompt, build_revision_prompt


class ChatGPTError(RuntimeError):
    """Базовая ошибка браузерного клиента ChatGPT."""


class ChatGPTLoginRequired(ChatGPTError):
    """В профиле браузера нет активной сессии ChatGPT."""


class ChatGPTTimeout(ChatGPTError):
    """ChatGPT не ответил за отведённое время."""


class ChatGPTSession:
    """Браузерный клиент ChatGPT.

    Работает через отдельный профиль Chrome, чтобы не мешать основному браузеру
    пользователя. Профиль создаётся один раз, вход выполняется вручную.
    """

    COMPOSER = 'div.ProseMirror[contenteditable="true"]'
    ASSISTANT_HEADING = 'h4[data-conversation-role="assistant"]'
    ASSISTANT_MARKDOWN = '[data-markdown-text-style="assistant-message"]'
    GENERATED_IMAGE = 'button[data-testid="generated-image-preview"] img'
    STOP_BUTTONS = 'button[aria-label*="Останов"], button[data-testid="stop-button"]'

    def __init__(self, profile_dir: Path, headless: bool = False, url: str = "https://chatgpt.com/") -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.url = url
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._started = False
        self._lock = asyncio.Lock()
        self._last_error = ""

    @property
    def started(self) -> bool:
        return self._started

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._started:
            return
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="chrome",
                headless=self.headless,
                viewport=None,
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            await self._playwright.stop()
            self._playwright = None
            raise ChatGPTError(f"Не удалось запустить Chrome: {exc}") from exc

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        await self._page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
        await self._page.wait_for_timeout(2000)
        self._started = True

    async def close(self) -> None:
        self._started = False
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
            self._context = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None
        self._page = None

    async def ensure_ready(self) -> None:
        await self.start()
        if not await self.is_logged_in(wait_ms=8000):
            raise ChatGPTLoginRequired(
                "В профиле ChatGPT нет активной сессии. Откройте окно ChatGPT и войдите в аккаунт."
            )

    async def check_login(self) -> bool:
        await self.start()
        return await self.is_logged_in()

    async def is_logged_in(self, wait_ms: int = 3000) -> bool:
        if self._page is None:
            return False
        try:
            await self._page.locator(self.COMPOSER).first.wait_for(state="visible", timeout=wait_ms)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ chat
    async def new_chat(self) -> None:
        if self._page is None:
            raise ChatGPTError("Браузер не запущен")
        clicked = False
        for name in ("Новый чат", "New chat"):
            button = self._page.get_by_role("button", name=name).first
            if await button.count():
                try:
                    await button.click(timeout=5000)
                    await self._page.wait_for_timeout(1200)
                    clicked = True
                    break
                except Exception:  # noqa: BLE001
                    pass
        if not clicked:
            await self._page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
            await self._page.wait_for_timeout(1500)
        await self._page.locator(self.COMPOSER).first.wait_for(state="visible", timeout=30000)
        # Если старый диалог всё ещё на экране, принудительно открываем корень.
        if await self.count_assistant_messages() > 0:
            await self._page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
            await self._page.wait_for_timeout(1500)
            await self._page.locator(self.COMPOSER).first.wait_for(state="visible", timeout=30000)

    async def send_message(self, text: str) -> None:
        if self._page is None:
            raise ChatGPTError("Браузер не запущен")
        composer = self._page.locator(self.COMPOSER).first
        await composer.wait_for(state="visible", timeout=30000)
        await composer.click()
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.press("Backspace")
        await self._page.keyboard.insert_text(text)
        await self._page.wait_for_timeout(500)
        await self._page.keyboard.press("Enter")
        await self._page.wait_for_timeout(1200)

        # Если сообщение не отправилось (кнопка ещё disabled), пробуем Enter ещё раз.
        try:
            remaining = await composer.inner_text()
            if text[:40].strip() and text[:40].strip() in remaining:
                await self._page.keyboard.press("Enter")
        except Exception:  # noqa: BLE001
            pass

    async def count_assistant_messages(self) -> int:
        if self._page is None:
            return 0
        return await self._page.locator(self.ASSISTANT_HEADING).count()

    async def count_generated_images(self) -> int:
        if self._page is None:
            return 0
        return await self._page.locator(self.GENERATED_IMAGE).count()

    async def _is_generating(self) -> bool:
        if self._page is None:
            return False
        stop = self._page.locator(self.STOP_BUTTONS)
        try:
            if await stop.count() == 0:
                return False
            return await stop.first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    async def get_last_assistant_text(self) -> str:
        if self._page is None:
            return ""
        heading = self._page.locator(self.ASSISTANT_HEADING).last
        if await heading.count() == 0:
            return ""
        parent = heading.locator("..")
        markdown = parent.locator(self.ASSISTANT_MARKDOWN)
        if await markdown.count():
            text = await markdown.last.inner_text()
        else:
            text = await parent.inner_text()
        text = re.sub(r"^\s*ChatGPT сказал:\s*", "", text).strip()
        return text

    async def wait_for_text_response(self, before: int, timeout: float = 240000) -> str:
        deadline = time.monotonic() + timeout / 1000
        last_text = ""
        stable_since: Optional[float] = None
        while time.monotonic() < deadline:
            count = await self.count_assistant_messages()
            if count > before:
                if await self._is_generating():
                    stable_since = None
                    await asyncio.sleep(1.5)
                    continue
                text = await self.get_last_assistant_text()
                if text:
                    if text == last_text:
                        if stable_since is None:
                            stable_since = time.monotonic()
                        elif time.monotonic() - stable_since >= 2.0:
                            return text
                    else:
                        last_text = text
                        stable_since = None
                else:
                    stable_since = None
            await asyncio.sleep(1.5)
        raise ChatGPTTimeout(
            "ChatGPT не прислал текстовый ответ за отведённое время. "
            "Проверьте окно браузера: возможно, он задал вопрос или уперся в лимит."
        )

    async def wait_for_image_or_error(
        self, images_before: int, assistants_before: int, timeout: float = 240000
    ) -> None:
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if await self.count_generated_images() > images_before:
                image = self._page.locator(self.GENERATED_IMAGE).last
                try:
                    width = await image.evaluate("el => el.naturalWidth")
                    if width and width > 200:
                        return
                except Exception:  # noqa: BLE001
                    pass
            if await self.count_assistant_messages() > assistants_before:
                text = await self.get_last_assistant_text()
                low = text.lower()
                if any(
                    marker in low
                    for marker in (
                        "лимит",
                        "недоступ",
                        "не могу сгенерировать",
                        "достигнут",
                        "не получится создать изображение",
                        "image generation limit",
                    )
                ):
                    raise ChatGPTError(text)
            await asyncio.sleep(2)
        raise ChatGPTTimeout(
            "ChatGPT не сгенерировал изображение за отведённое время. "
            "Проверьте окно браузера и лимиты бесплатного тарифа."
        )

    async def save_latest_image(self, target: Path) -> str:
        if self._page is None:
            raise ChatGPTError("Браузер не запущен")
        image = self._page.locator(self.GENERATED_IMAGE).last
        await image.wait_for(state="visible", timeout=30000)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Пытаемся забрать оригинал из blob, чтобы не потерять разрешение.
        data_url = await image.evaluate(
            """async (el) => {
                try {
                    const response = await fetch(el.src);
                    const blob = await response.blob();
                    return await new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onload = () => resolve(reader.result);
                        reader.onerror = reject;
                        reader.readAsDataURL(blob);
                    });
                } catch (error) {
                    return null;
                }
            }"""
        )
        if isinstance(data_url, str) and data_url.startswith("data:image") and "," in data_url:
            raw = base64.b64decode(data_url.split(",", 1)[1])
            try:
                with Image.open(io.BytesIO(raw)) as source:
                    source.convert("RGB").save(target, format="PNG")
            except Exception:  # noqa: BLE001
                target.write_bytes(raw)
        else:
            await image.screenshot(path=str(target))
        return str(target)

    # ------------------------------------------------------------------ public API
    async def generate_post(
        self, company: Company, request: str, history: str, image_path: Path, timeout: float = 240000
    ) -> tuple[str, str]:
        async with self._lock:
            self._last_error = ""
            try:
                await self.ensure_ready()
                await self.new_chat()
                text = await self._send_and_wait_text(
                    build_post_prompt(company, request, history), timeout
                )
                if len(text.strip()) < 40:
                    raise ChatGPTError(f"ChatGPT вернул слишком короткий ответ: {text!r}")

                images_before = await self.count_generated_images()
                assistants_before = await self.count_assistant_messages()
                await self.send_message(build_image_prompt(company, text))
                await self.wait_for_image_or_error(images_before, assistants_before, timeout)
                saved = await self.save_latest_image(image_path)
                return text, saved
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise

    async def revise_post(
        self,
        company: Company,
        previous_post: str,
        comment: str,
        image_path: Path,
        regenerate_image: bool = False,
        timeout: float = 240000,
    ) -> tuple[str, str]:
        async with self._lock:
            try:
                await self.ensure_ready()
                await self.new_chat()
                text = await self._send_and_wait_text(
                    build_revision_prompt(company, previous_post, comment), timeout
                )
                if regenerate_image:
                    images_before = await self.count_generated_images()
                    assistants_before = await self.count_assistant_messages()
                    await self.send_message(build_image_prompt(company, text))
                    await self.wait_for_image_or_error(images_before, assistants_before, timeout)
                    saved = await self.save_latest_image(image_path)
                    return text, saved
                return text, str(image_path)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise

    async def answer_question(self, company: Company, history: str, question: str, timeout: float = 180000) -> str:
        async with self._lock:
            try:
                await self.ensure_ready()
                await self.new_chat()
                return await self._send_and_wait_text(
                    build_question_prompt(company, history, question), timeout
                )
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise

    async def _send_and_wait_text(self, prompt: str, timeout: float) -> str:
        before = await self.count_assistant_messages()
        await self.send_message(prompt)
        return await self.wait_for_text_response(before, timeout)
