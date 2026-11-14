from __future__ import annotations

import base64
import io
import logging
import re
import time
from pathlib import Path

import requests
from PIL import Image

logger = logging.getLogger(__name__)


class ImageGenerationError(RuntimeError):
    """Не удалось сгенерировать изображение через anymodel."""


class AnyModelImageGenerator:
    """Генерация изображений через anymodel (OpenAI-совместимый Images API)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "gpt-image-2.5",
        quality: str = "low",
        timeout: int = 180,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or "gpt-image-2.5").strip()
        self.quality = (quality or "low").strip()
        self.timeout = timeout
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def generate(
        self,
        prompt: str,
        company_name: str,
        headline: str,
        target: Path,
        subtitle: str = "",
        seed: str = "",
    ) -> Path:
        if not self.configured:
            raise ImageGenerationError(
                "ANYMODEL_API_KEY не задан. Добавьте ключ anymodel в .env, чтобы генерировать изображения."
            )
        clean_prompt = self._normalize_prompt(prompt)
        url = self.base_url + "/images/generations"
        payload = {
            "model": self.model,
            "prompt": clean_prompt,
            "size": "1024x1024",
            "quality": self.quality,
            "n": 1,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        target = Path(target)
        last_error = ""

        for attempt in range(2):
            try:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout
                )
                if response.status_code == 200:
                    self._save_response(response.json(), target)
                    self.last_error = ""
                    return target

                # some OpenAI-compatible gateways expect x-api-key instead of Bearer
                if response.status_code == 401:
                    alt_headers = {
                        "x-api-key": self.api_key,
                        "Content-Type": "application/json",
                    }
                    alt = requests.post(
                        url, headers=alt_headers, json=payload, timeout=self.timeout
                    )
                    if alt.status_code == 200:
                        self._save_response(alt.json(), target)
                        self.last_error = ""
                        return target
                    response = alt

                last_error = self._error_text(response)
            except requests.RequestException as exc:
                last_error = f"Сетевая ошибка: {exc}"
            if attempt == 0:
                time.sleep(2)

        self.last_error = last_error
        raise ImageGenerationError(last_error or "anymodel не вернул изображение")

    def _save_response(self, payload: dict, target: Path) -> None:
        items = payload.get("data") if isinstance(payload, dict) else None
        if not items:
            raise ImageGenerationError(f"Неожиданный ответ anymodel: {str(payload)[:300]}")
        item = items[0]
        if item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            image_response = requests.get(item["url"], timeout=self.timeout)
            image_response.raise_for_status()
            raw = image_response.content
        else:
            raise ImageGenerationError("В ответе anymodel нет b64_json или url")

        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGB")
            image = self._fit_square(image)
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "PNG", optimize=True)

    @staticmethod
    def _fit_square(image: Image.Image) -> Image.Image:
        width, height = image.size
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        image = image.crop((left, top, left + side, top + side))
        return image.resize((1024, 1024), Image.LANCZOS)

    @staticmethod
    def _normalize_prompt(prompt: str) -> str:
        prompt = (prompt or "").strip()
        if not prompt:
            prompt = (
                "photorealistic architectural photography, modern glass facade, "
                "natural daylight, clean minimal composition, no people, no text, no logos"
            )
        prompt = re.sub(r"\s+", " ", prompt).strip().strip('"').strip("'")
        lower = prompt.lower()
        if "photorealistic" not in lower and "photo" not in lower:
            prompt = "photorealistic, " + prompt
        if "no text" not in lower:
            prompt += ", no text, no logos"
        return prompt[:1000]

    @staticmethod
    def _error_text(response: requests.Response) -> str:
        try:
            payload = response.json()
            error = payload.get("error")
            if isinstance(error, dict):
                return f"HTTP {response.status_code}: {error.get('message') or error}"
            if error:
                return f"HTTP {response.status_code}: {error}"
        except Exception:  # noqa: BLE001
            pass
        return f"HTTP {response.status_code}: {response.text[:300]}"
