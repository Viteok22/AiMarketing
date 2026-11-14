from __future__ import annotations

import html
import re

from app.models import Company, PostDraft

IMAGE_PROMPT_RULES = """- это фон для корпоративного SMM-креатива в Telegram, а не документальная фотография;
- стиль — графическая композиция или 3D-рендер, как у плакатов заказчика: предметные макеты (календарь, блокнот, ноутбук, стеклянная награда), карточки с иконками, инфографика, 3D-стекло, городской коллаж;
- не использовать фотографии людей, портреты, лица, сотрудников, спортивные команды и документальные кадры; люди не нужны даже как фон;
- можно использовать предметные макеты и фотоподложки города или офиса без людей;
- не используй современный стеклянный офисный фасад, если пост не посвящён именно фасадам;
- фирменная палитра: глубокий бирюзовый, светло-голубой, белый, тёплый жёлтый;
- крупный читаемый заголовок, брендовая плашка, минимум мелкого текста;
- композиция квадратная 1:1, оставь крупный чистый участок для заголовка;
- без текста, логотипов и водяных знаков;
- английский язык, 25–45 слов, обязательно добавь: graphic design, 3D render, no people."""


def company_context(company: Company) -> str:
    parts = [
        f"Компания: {company.name}",
        f"Описание: {company.description}" if company.description else "",
        f"Продукция/услуги: {company.products}" if company.products else "",
        f"Целевая аудитория: {company.audience}" if company.audience else "",
        f"Преимущества: {company.advantages}" if company.advantages else "",
        f"Тон и стиль: {company.tone}" if company.tone else "",
        f"Запрещено: {company.forbidden}" if company.forbidden else "",
        f"База знаний:\n{company.knowledge}" if company.knowledge else "",
    ]
    return "\n".join(p for p in parts if p)


def build_post_prompt(
    company: Company,
    request: str,
    history: str,
    feedback: str = "",
    trends: str = "",
    variant_hint: str = "",
    style_examples: str = "",
) -> str:
    extra = ""
    if feedback:
        extra += f"\nОбратная связь по прошлым публикациям:\n{feedback}\n"
    if trends:
        extra += (
            "\nЧто сейчас работает в похожих каналах "
            "(используй форматы, хуки и темы, но не копируй чужой текст):\n"
            f"{trends}\n"
        )
    if variant_hint:
        extra += f"\nДополнительный акцент этого варианта: {variant_hint}\n"
    if style_examples:
        extra += (
            "\nПримеры стиля заказчика (обязательно сохраняй тон, структуру, "
            "эмодзи, обращения и хештеги, но не копируй текст дословно):\n"
            f"{style_examples}\n"
        )
    return f"""Ты — AI-маркетолог компании «{company.name}». Работай только с фактами из блока о компании. Не выдумывай цифры, награды, цены и сроки.

{company_context(company)}

История предыдущих действий с этим пользователем:
{history}
{extra}
Задача пользователя:
{request}

Подготовь публикацию для Telegram-канала компании. Требования:
- русский язык, экспертный и уверенный тон, без канцелярита и пафоса;
- объём основного текста 600–850 знаков;
- цепляющий заголовок до 60 знаков, без точки в конце;
- 2–4 коротких абзаца;
- 3–5 маркеров «•» или эмодзи в начале ключевых пунктов;
- в конце — вопрос к аудитории или призыв обсудить;
- 3–5 уместных хештегов отдельной строкой;
- стиль: тёплый корпоративный, как в примерах заказчика; эмодзи в начале ключевых блоков, короткие абзацы, обращения к коллегам, конкретные люди и события;
- не задавай уточняющих вопросов, сразу выдай готовый материал;
- не используй markdown-разметку, таблицы, HTML и код.

Дополнительно составь промпт для фона изображения:
{IMAGE_PROMPT_RULES}

Ответ строго в таком формате:
ЗАГОЛОВОК: <заголовок>
ТЕКСТ:
<текст публикации>
ХЕШТЕГИ: <хештеги через пробел>
ИЗОБРАЖЕНИЕ: <english prompt for a graphic corporate social media background, 3D render, no people, no text, no logos>
"""


def build_image_prompt(company: Company, post_text: str) -> str:
    brand = company.name
    tone = company.tone or "современный, экспертный, сдержанный"
    return f"""Сгенерируй одно изображение для публикации компании «{brand}» в Telegram.

Смысл публикации:
{post_text[:700]}

Визуальные требования:
- тема: архитектурное или листовое стекло, современный фасад, интерьер или производство;
- стиль: {tone}, фотореалистично, дневной свет, чистые линии;
- композиция: квадратная 1:1, без текста, логотипов и водяных знаков;
- изображение должно выглядеть как качественная иллюстрация для делового канала, а не как коллаж.

Не задавай вопросов. Сразу сгенерируй изображение.
"""


def build_revision_prompt(
    company: Company,
    previous_post: str,
    comment: str,
    feedback: str = "",
    trends: str = "",
    variant_hint: str = "",
    style_examples: str = "",
) -> str:
    extra = ""
    if feedback:
        extra += f"\nОбратная связь по прошлым публикациям:\n{feedback}\n"
    if trends:
        extra += (
            "\nЧто сейчас работает в похожих каналах "
            "(используй форматы и хуки, но не копируй чужой текст):\n"
            f"{trends}\n"
        )
    if variant_hint:
        extra += f"\nДополнительный акцент этого варианта: {variant_hint}\n"
    if style_examples:
        extra += (
            "\nПримеры стиля заказчика (сохраняй тон, структуру, эмодзи, "
            "обращения и хештеги, но не копируй текст дословно):\n"
            f"{style_examples}\n"
        )
    return f"""Ты — AI-маркетолог компании «{company.name}». Нужно доработать публикацию по комментарию ответственного сотрудника.

{company_context(company)}

Предыдущая публикация:
{previous_post}

Комментарий и требуемые изменения:
{comment}
{extra}
Перепиши публикацию с учётом комментария. Сохрани факты, стиль бренда и манеру примеров заказчика. Объём основного текста — 600–850 знаков. Оформление: заголовок до 60 знаков, 2–4 коротких абзаца, 3–5 маркеров «•» или эмодзи, вопрос к аудитории в конце, хештеги отдельной строкой. Без Markdown, HTML и таблиц. Не задавай вопросов.
Дополнительно обнови промпт для фона изображения:
{IMAGE_PROMPT_RULES}

Ответ строго в формате:
ЗАГОЛОВОК: <заголовок>
ТЕКСТ:
<текст публикации>
ХЕШТЕГИ: <хештеги через пробел>
ИЗОБРАЖЕНИЕ: <english prompt for a graphic corporate social media background, 3D render, no people, no text, no logos>
"""


def build_quick_edit_prompt(
    company: Company,
    previous_post: str,
    instruction: str,
    style_examples: str = "",
) -> str:
    style_block = ""
    if style_examples:
        style_block = (
            "\nПримеры стиля заказчика (сохраняй тон, структуру, эмодзи, "
            f"обращения и хештеги, но не копируй дословно):\n{style_examples}\n"
        )
    return f"""Ты — AI-маркетолог компании «{company.name}». Нужно быстро поправить готовую публикацию.

{company_context(company)}

Текущая публикация:
{previous_post}

Что нужно изменить:
{instruction}
{style_block}

Сохрани факты, стиль бренда и формат: заголовок до 60 знаков, 2–4 коротких абзаца, 3–5 маркеров «•» или эмодзи, вопрос к аудитории в конце, 3–5 хештегов отдельной строкой. Главный приоритет — указание выше: если просят короче, смело сокращай; если просят добавить конкретику, добавляй только факты из материалов компании. Не выдумывай цифры, награды, цены и сроки. Без Markdown, HTML и таблиц. Не задавай вопросов.
Дополнительно обнови промпт для фона изображения:
{IMAGE_PROMPT_RULES}

Ответ строго в формате:
ЗАГОЛОВОК: <заголовок>
ТЕКСТ:
<текст публикации>
ХЕШТЕГИ: <хештеги через пробел>
ИЗОБРАЖЕНИЕ: <english prompt for a graphic corporate social media background, 3D render, no people, no text, no logos>
"""


def build_question_prompt(company: Company, history: str, question: str) -> str:
    return f"""Ты — AI-маркетолог компании «{company.name}». Ответь на уточняющий вопрос пользователя, опираясь на историю работы и факты о компании.

{company_context(company)}

История предыдущих действий:
{history}

Вопрос пользователя:
{question}

Ответь коротко (3–8 предложений), по делу, без выдуманных фактов. Если данных не хватает, честно скажи, чего не хватает. Не предлагай заново всю публикацию, если об этом не просят.
"""


def _clean_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s+(?=\S)", "", text)
    return text.strip()


def parse_post_response(raw: str) -> PostDraft:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:markdown|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    headline = ""
    body = text
    hashtags = ""
    image_prompt = ""

    match_headline = re.search(
        r"(?:^|\n)\s*[*#\s]*(?:ЗАГОЛОВОК|Заголовок)[*#\s]*:\s*(.+)", text
    )
    if match_headline:
        headline = _clean_markdown(match_headline.group(1)).strip("*# ")

    match_body = re.search(
        r"(?:^|\n)\s*[*#\s]*(?:ТЕКСТ|Текст)[*#\s]*:\s*(.*?)(?=\n\s*[*#\s]*(?:ХЕШТЕГИ|Хештеги)[*#\s]*:|$)",
        text,
        flags=re.S,
    )
    if match_body:
        body = _clean_markdown(match_body.group(1))

    match_tags = re.search(
        r"(?:^|\n)\s*[*#\s]*(?:ХЕШТЕГИ|Хештеги)[*#\s]*:\s*(.+)", text, flags=re.S
    )
    if match_tags:
        hashtags = _clean_markdown(match_tags.group(1))

    match_image = re.search(
        r"(?:^|\n)\s*[*#\s]*(?:ИЗОБРАЖЕНИЕ|Изображение)[*#\s]*:\s*(.*?)(?=\n\s*[*#\s]*(?:ХЕШТЕГИ|Хештеги)[*#\s]*:|$)",
        text,
        flags=re.S,
    )
    if match_image:
        image_prompt = _clean_markdown(match_image.group(1)).strip().strip('"').strip("'")
        image_prompt = re.sub(r"\s+", " ", image_prompt).strip()
        if len(image_prompt) > 400:
            image_prompt = image_prompt[:397].rstrip() + "…"

    # Если модель поставила блок с изображением внутрь текста или хештегов — убираем.
    image_block = r"\n\s*[*#\s]*(?:ИЗОБРАЖЕНИЕ|Изображение)[*#\s]*:.*$"
    body = re.sub(image_block, "", body, flags=re.S).strip()
    hashtags = re.sub(image_block, "", hashtags, flags=re.S).strip()

    if not (match_headline or match_body or match_tags):
        clean_text = re.sub(image_block, "", text, flags=re.S).strip()
        lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
        if lines:
            headline = _clean_markdown(lines[0]).lstrip("#").strip()
            body = _clean_markdown("\n".join(lines[1:])).strip() or headline

    full_text = "\n\n".join(part for part in (headline, body, hashtags) if part).strip()
    return PostDraft(
        headline=headline,
        body=body,
        hashtags=hashtags,
        full_text=full_text,
        image_prompt=image_prompt,
    )


def fit_caption(text: str, limit: int = 1000) -> str:
    """Подгоняет пост под лимит подписи Telegram, сохраняя заголовок и хештеги."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text

    parts = text.split("\n\n")
    headline = parts[0].strip() if parts else ""
    body_parts = parts[1:]
    hashtags = ""
    if body_parts and body_parts[-1].strip().startswith("#"):
        hashtags = body_parts[-1].strip()
        body_parts = body_parts[:-1]
    body = "\n\n".join(body_parts).strip()

    reserved = len(headline) + len(hashtags)
    if headline and (body or hashtags):
        reserved += 2
    if body and hashtags:
        reserved += 2
    available = max(80, limit - reserved)

    if len(body) > available:
        cut = body[:available].rstrip()
        for separator in (". ", "! ", "? ", "\n", " "):
            position = cut.rfind(separator)
            if position > available * 0.5:
                cut = cut[: position + (1 if separator in {". ", "! ", "? "} else 0)]
                break
        body = cut.rstrip(" ,;:-") + "…"

    result = "\n\n".join(part for part in (headline, body, hashtags) if part).strip()
    if len(result) > limit:
        result = result[: limit - 1].rstrip() + "…"
    return result


def shorten_post(text: str, limit: int = 650) -> str:
    """Сокращает пост, сохраняя заголовок, вопрос к аудитории и хештеги."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text

    parts = text.split("\n\n")
    headline = parts[0].strip() if parts else ""
    tail = parts[1:]
    hashtags = ""
    if tail and tail[-1].strip().startswith("#"):
        hashtags = tail[-1].strip()
        tail = tail[:-1]
    cta = ""
    if tail and "?" in tail[-1]:
        cta = tail[-1].strip()
        tail = tail[:-1]
    body = "\n\n".join(tail).strip()

    reserved = len(headline) + len(hashtags) + len(cta) + 6
    available = max(120, limit - reserved)
    if len(body) > available:
        kept: list[str] = []
        used = 0
        for line in [line for line in body.splitlines() if line.strip()]:
            line_len = len(line) + 1
            if used + line_len > available:
                remaining = available - used
                if remaining > 40:
                    cut = line[:remaining].rstrip()
                    for separator in (". ", "! ", "? ", " "):
                        position = cut.rfind(separator)
                        if position > remaining * 0.5:
                            cut = cut[: position + (1 if separator in {". ", "! ", "? "} else 0)]
                            break
                    kept.append(cut.rstrip(" ,;:-") + "…")
                break
            kept.append(line)
            used += line_len
        body = "\n".join(kept).strip()

    result = "\n\n".join(part for part in (headline, body, cta, hashtags) if part).strip()
    if len(result) > limit:
        result = result[: limit - 1].rstrip() + "…"
    return result


def format_post_html(post_text: str) -> str:
    """Превращает пост в Telegram HTML: жирный заголовок + экранированный текст."""
    text = (post_text or "").strip()
    parts = text.split("\n\n")
    headline = parts[0].strip() if parts else ""
    body_parts = parts[1:]
    hashtags = ""
    if body_parts and body_parts[-1].strip().startswith("#"):
        hashtags = body_parts[-1].strip()
        body_parts = body_parts[:-1]
    body = "\n\n".join(body_parts).strip()

    if not body and not hashtags and "\n" in headline:
        lines = headline.splitlines()
        headline = lines[0].strip()
        body = "\n".join(lines[1:]).strip()

    pieces = []
    if headline:
        pieces.append(f"<b>{html.escape(headline, quote=False)}</b>")
    if body:
        pieces.append(html.escape(body, quote=False))
    if hashtags:
        pieces.append(html.escape(hashtags, quote=False))
    return "\n\n".join(pieces)


TASK_MARKERS = (
    "сделай",
    "сделайте",
    "подготовь",
    "подготовьте",
    "напиши",
    "напишите",
    "создай",
    "создайте",
    "придумай",
    "придумайте",
    "составь",
    "составьте",
    "разработай",
    "разработайте",
    "опубликуй",
    "опубликуйте",
    "сгенерируй",
    "сгенерируйте",
    "предложи",
    "предложите",
    "нужен пост",
    "нужна публикация",
    "сделаем пост",
)

QUESTION_MARKERS = (
    "почему",
    "зачем",
    "как ",
    "что ",
    "когда",
    "где ",
    "кто ",
    "расскажи",
    "объясни",
    "уточни",
    "какой",
    "какая",
    "какие",
    "сколько",
    "можно ли",
)


def classify_intent(text: str, has_last_task: bool = False) -> str:
    lower = (text or "").lower().strip()
    if not lower:
        return "task"
    if "?" in lower:
        return "question"
    if any(marker in lower for marker in TASK_MARKERS):
        return "task"
    if has_last_task and any(marker in lower for marker in QUESTION_MARKERS):
        return "question"
    if has_last_task and len(lower) < 140:
        return "question"
    return "task"


def split_caption(text: str, limit: int = 1024) -> tuple[str, str]:
    text = text.strip()
    if len(text) <= limit:
        return text, ""
    cut = text.rfind("\n", 0, limit)
    if cut < int(limit * 0.6):
        cut = text.rfind(" ", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut].rstrip(), text[cut:].strip()
