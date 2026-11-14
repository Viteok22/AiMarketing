from __future__ import annotations

import base64
import io
import logging
import re
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

BRAND_DARK = (8, 54, 66)
BRAND_DARK_2 = (5, 38, 47)
BRAND_BLUE = (86, 166, 194)
BRAND_LIGHT = (214, 239, 246)
BRAND_YELLOW = (244, 196, 79)
BRAND_WHITE = (247, 251, 252)

_FONT_BOLD_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
_FONT_REGULAR_CANDIDATES = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

_LOGO_PATH = Path(__file__).resolve().parent / "static" / "brand" / "logo.png"

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\uFE0F\u200D]+"
)

_DEFAULT_PROMPT = (
    "graphic corporate social media background, 3D render, abstract translucent "
    "glass shapes and infographic cards, deep teal and light blue brand palette, "
    "soft studio light, clean empty space for a headline, no people, "
    "no text, no logos, no watermark, high quality"
)


class ImageGenerationError(RuntimeError):
    """Не удалось сгенерировать изображение через anymodel."""


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = _FONT_BOLD_CANDIDATES if bold else _FONT_REGULAR_CANDIDATES
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _load_logo() -> Image.Image | None:
    try:
        return Image.open(_LOGO_PATH).convert("RGB")
    except OSError:
        return None


def _clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = _EMOJI_RE.sub(" ", text)
    text = re.sub(r"[*_`#]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _brand_name(company_name: str) -> str:
    name = re.sub(
        r"^(АО|ООО|ПАО|ЗАО|ОАО)\s+", "", (company_name or "").strip(), flags=re.I
    )
    name = name.replace("«", "").replace("»", "").replace('"', "").strip()
    return name.upper() or "КОМПАНИЯ"


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;:!?")
    return cut + "…"


def _wrap_text(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_headline(
    draw: ImageDraw.ImageDraw, text: str, max_width: int, max_lines: int = 3
) -> tuple[ImageFont.ImageFont, list[str], int]:
    for size in (78, 70, 62, 54, 46, 40):
        font = _load_font(size, bold=True)
        lines = _wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines, size
    font = _load_font(40, bold=True)
    lines = _wrap_text(draw, text, font, max_width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _trim(lines[-1], max(8, len(lines[-1]) - 1))
    return font, lines, 40


def _apply_brand_overlay(
    image: Image.Image,
    headline: str,
    subtitle: str,
    company_name: str,
) -> Image.Image:
    """Накладывает фирменную плашку, заголовок и подзаголовок на фон."""

    image = image.convert("RGB")
    width, height = image.size
    brand = _brand_name(company_name)
    headline_text = _clean_text(headline) or brand

    # Затемнение снизу, чтобы белый текст читался на любом фоне.
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    start_y = int(height * 0.48)
    for y in range(start_y, height):
        ratio = (y - start_y) / max(1, height - start_y)
        alpha = int(238 * (ratio**0.85))
        shade_draw.line([(0, y), (width, y)], fill=(*BRAND_DARK_2, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), shade).convert("RGB")
    draw = ImageDraw.Draw(image)

    margin_x = int(width * 0.065)

    # Логотип сверху слева на белой подложке.
    logo = _load_logo()
    logo_x, logo_y = int(width * 0.04), int(height * 0.035)
    if logo is not None:
        max_logo_w = int(width * 0.195)
        ratio = max_logo_w / max(1, logo.width)
        logo = logo.resize(
            (max_logo_w, max(1, int(logo.height * ratio))), Image.LANCZOS
        )
        pad = int(width * 0.018)
        plate_w = logo.width + pad * 2
        plate_h = logo.height + pad * 2
        draw.rounded_rectangle(
            [logo_x, logo_y, logo_x + plate_w, logo_y + plate_h],
            radius=int(plate_h * 0.18),
            fill=BRAND_WHITE,
        )
        image.paste(logo, (logo_x + pad, logo_y + pad))
    else:
        # Запасной вариант, если файл логотипа не найден.
        brand_font = _load_font(max(18, int(width * 0.025)), bold=True)
        brand_width = int(draw.textlength(brand, font=brand_font))
        pill_h = int(height * 0.062)
        pill_w = brand_width + int(pill_h * 2.1)
        draw.rounded_rectangle(
            [logo_x, logo_y, logo_x + pill_w, logo_y + pill_h],
            radius=int(pill_h * 0.28),
            fill=BRAND_DARK,
        )
        mono_size = int(pill_h * 0.72)
        mono_x = logo_x + int(pill_h * 0.14)
        mono_y = logo_y + (pill_h - mono_size) // 2
        draw.rounded_rectangle(
            [mono_x, mono_y, mono_x + mono_size, mono_y + mono_size],
            radius=int(mono_size * 0.22),
            fill=BRAND_BLUE,
        )
        mono_font = _load_font(max(14, int(mono_size * 0.62)), bold=True)
        mono_bbox = draw.textbbox((0, 0), "S", font=mono_font)
        mono_w = mono_bbox[2] - mono_bbox[0]
        mono_h = mono_bbox[3] - mono_bbox[1]
        draw.text(
            (
                mono_x + (mono_size - mono_w) / 2,
                mono_y + (mono_size - mono_h) / 2 - mono_bbox[1],
            ),
            "S",
            font=mono_font,
            fill=BRAND_WHITE,
        )
        brand_bbox = draw.textbbox((0, 0), brand, font=brand_font)
        draw.text(
            (
                mono_x + mono_size + int(pill_h * 0.22),
                logo_y
                + (pill_h - (brand_bbox[3] - brand_bbox[1])) / 2
                - brand_bbox[1],
            ),
            brand,
            font=brand_font,
            fill=BRAND_WHITE,
        )

    # Нижний блок: подзаголовок и футер.
    footer_font = _load_font(max(14, int(width * 0.021)), bold=True)
    footer_text = "#" + brand.capitalize()
    footer_y = height - int(height * 0.055)
    draw.text((margin_x, footer_y), footer_text, font=footer_font, fill=BRAND_YELLOW)

    sub_text = _trim(_clean_text(subtitle), 130)
    sub_lines: list[str] = []
    sub_font = _load_font(max(16, int(width * 0.027)))
    if sub_text:
        sub_lines = _wrap_text(draw, sub_text, sub_font, int(width * 0.87))[:2]
        sub_line_h = int(getattr(sub_font, "size", int(width * 0.027)) * 1.3)
        sub_y = footer_y - int(height * 0.022) - len(sub_lines) * sub_line_h
        for line in sub_lines:
            draw.text(
                (margin_x + 2, sub_y + 2), line, font=sub_font, fill=(0, 0, 0)
            )
            draw.text((margin_x, sub_y), line, font=sub_font, fill=BRAND_LIGHT)
            sub_y += sub_line_h
    else:
        sub_line_h = 0

    # Заголовок и жёлтый акцент над ним.
    headline_font, headline_lines, headline_size = _fit_headline(
        draw, headline_text, int(width * 0.87), max_lines=3
    )
    headline_line_h = int(headline_size * 1.14)
    headline_block_h = len(headline_lines) * headline_line_h
    headline_y = (
        footer_y
        - int(height * 0.022)
        - (len(sub_lines) * sub_line_h if sub_lines else 0)
        - int(height * 0.028)
        - headline_block_h
    )
    accent_y = headline_y - int(height * 0.028)
    draw.rounded_rectangle(
        [margin_x, accent_y, margin_x + int(width * 0.12), accent_y + max(6, int(height * 0.009))],
        radius=4,
        fill=BRAND_YELLOW,
    )
    y = headline_y
    for line in headline_lines:
        draw.text(
            (margin_x + 3, y + 3), line, font=headline_font, fill=(0, 0, 0)
        )
        draw.text((margin_x, y), line, font=headline_font, fill=BRAND_WHITE)
        y += headline_line_h

    return image


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
                    self._save_response(
                        response.json(), target, headline, subtitle, company_name
                    )
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
                        self._save_response(
                            alt.json(), target, headline, subtitle, company_name
                        )
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

    def _save_response(
        self,
        payload: dict,
        target: Path,
        headline: str = "",
        subtitle: str = "",
        company_name: str = "",
    ) -> None:
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
            try:
                image = _apply_brand_overlay(
                    image, headline, subtitle, company_name or ""
                )
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось наложить брендированный слой")
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
            prompt = _DEFAULT_PROMPT
        prompt = re.sub(r"\s+", " ", prompt).strip().strip('"').strip("'")
        prompt = re.sub(r"\bphotorealistic\b", "stylized 3D render", prompt, flags=re.I)
        lower = prompt.lower()
        graphic_markers = ("graphic", "illustration", "3d", "render", "poster", "collage")
        if not any(marker in lower for marker in graphic_markers):
            prompt = "graphic design, 3D render, " + prompt
        lower = prompt.lower()
        if "no people" not in lower:
            prompt += (
                ", no people, no faces, no human figures, no portraits, "
                "no documentary photography"
            )
        if "no text" not in lower:
            prompt += ", no text, no logos, no watermark"
        if "clean" not in lower and "empty space" not in lower:
            prompt += ", clean empty space for a headline"
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
