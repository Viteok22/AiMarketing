from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    MessageReactionCountUpdated,
    MessageReactionUpdated,
    ReactionTypeEmoji,
    ReplyKeyboardMarkup,
)

from app.config import resolve_path, settings
from app.models import (
    CALENDAR_STATUS_LABELS,
    Company,
    PostDraft,
    STATUS_LABELS,
    Task,
    Variant,
)
from app.prompts import (
    classify_intent,
    fit_caption,
    format_post_html,
    parse_post_response,
    shorten_post,
)
from app.runtime import analytics_service, generator, store, trend_analyzer
from app.time_parse import PublishTimeError, parse_custom_publish_time
from app.trends import TrendError

logger = logging.getLogger(__name__)
router = Router()


class TaskOwnerMiddleware(BaseMiddleware):
    """Не даёт открыть или изменить чужую задачу по callback-кнопке."""

    async def __call__(self, handler, event, data):
        if event.data:
            for part in event.data.split(":"):
                if part.startswith("t-"):
                    task = store.get_task(part)
                    company = _user_company(event.from_user.id)
                    if task is not None and (
                        task.user_id != event.from_user.id
                        or company is None
                        or task.company_id != company.id
                    ):
                        await event.answer(
                            "Нет доступа к этой задаче", show_alert=True
                        )
                        return
                    break
        return await handler(event, data)


router.callback_query.middleware(TaskOwnerMiddleware())

TEXT_VARIANTS = 2
IMAGE_VARIANTS = 2

BTN_NEW = "✍️ Новая задача"
BTN_STATS = "📊 Статистика"
BTN_TRENDS = "🔥 Тренды"
BTN_SCHEDULED = "🕒 Запланировано"
BTN_CALENDAR = "🗓 Календарь"
BTN_HELP = "❓ Помощь"

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
NO_ACCESS_TEXT = (
    "Доступ к боту не настроен.\n\n"
    "Администратор должен привязать ваш Telegram ID к одной компании в веб-панели."
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_STATS)],
        [KeyboardButton(text=BTN_TRENDS), KeyboardButton(text=BTN_SCHEDULED)],
        [KeyboardButton(text=BTN_CALENDAR), KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


class RevisionStates(StatesGroup):
    waiting_comment = State()


class PublishTimeStates(StatesGroup):
    waiting_custom = State()


def _channel(company: Company) -> str:
    return (company.channel_id or settings.telegram_channel_id).strip()


def _channel_link(channel: str, message_id: int) -> str:
    if channel.startswith("@"):
        return f"https://t.me/{channel.lstrip('@')}/{message_id}"
    return ""


def _selected_variants(task: Task) -> tuple[list[Variant], list[Variant]]:
    batch = store.latest_batch(task.id)
    texts = store.list_variants(task.id, kind="text", batch_no=batch)
    images = store.list_variants(task.id, kind="image", batch_no=batch)
    return texts, images


def _variant_label(task: Task, texts: list[Variant], images: list[Variant]) -> str:
    text_index = next(
        (index + 1 for index, item in enumerate(texts) if item.id == task.selected_text_id),
        1 if texts else 0,
    )
    image_index = next(
        (index + 1 for index, item in enumerate(images) if item.id == task.selected_image_id),
        1 if images else 0,
    )
    if not texts and not images:
        return "варианты не найдены"
    return f"Текст {text_index} · Фото {image_index}"


def _control_text(task: Task, texts: list[Variant], images: list[Variant]) -> str:
    return (
        "Выберите текст и изображение.\n"
        f"Сейчас: {_variant_label(task, texts, images)}\n\n"
        "Можно смешивать: например, текст 1 + фото 2.\n"
        "После выбора нажмите «Одобрить выбранное»."
    )


def _variant_keyboard(
    task: Task, texts: list[Variant], images: list[Variant]
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    text_buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if item.id == task.selected_text_id else ''}Текст {index + 1}",
            callback_data=f"text:{task.id}:{item.id}",
        )
        for index, item in enumerate(texts)
    ]
    for index in range(0, len(text_buttons), 2):
        rows.append(text_buttons[index : index + 2])
    image_buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if item.id == task.selected_image_id else ''}Фото {index + 1}",
            callback_data=f"image:{task.id}:{item.id}",
        )
        for index, item in enumerate(images)
    ]
    for index in range(0, len(image_buttons), 2):
        rows.append(image_buttons[index : index + 2])
    rows.append(
        [InlineKeyboardButton(text="🖼 Ещё фото", callback_data=f"moreimg:{task.id}")]
    )
    rows.append(
        [
            InlineKeyboardButton(text="✅ Одобрить выбранное", callback_data=f"approve:{task.id}"),
            InlineKeyboardButton(text="✏️ На доработку", callback_data=f"revise:{task.id}"),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{task.id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _retry_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Повторить", callback_data=f"retry:{task_id}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{task_id}")],
        ]
    )


def _format_counts(reactions: dict) -> str:
    counts = reactions.get("counts") or {}
    if not counts:
        return "реакций пока нет"
    return ", ".join(f"{emoji} {count}" for emoji, count in counts.items())


def _build_feedback_text(company_id: str | None = None, limit: int = 5) -> str:
    reports = store.list_reaction_reports(limit=limit, company_id=company_id)
    lines = []
    for report in reports:
        reactions = report["reactions"]
        if reactions["total"] == 0:
            continue
        lines.append(
            f"«{report['request'][:70]}»: {_format_counts(reactions)} "
            f"(позитив {reactions['positive']}, негатив {reactions['negative']})"
        )
    return "\n".join(lines)


def _latest_trends_text(company_id: str | None = None) -> str:
    insight = store.get_latest_trend_insight(company_id)
    if insight is None or not trend_analyzer.is_fresh(hours=24, company_id=company_id):
        return ""
    return trend_analyzer.format_for_prompt(insight)


def _short(text: str, limit: int = 60) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _is_admin(user_id: int) -> bool:
    return bool(settings.admin_telegram_id) and user_id == settings.admin_telegram_id


def _user_company(user_id: int) -> Company | None:
    company = store.get_company_for_user(user_id)
    if company is None and _is_admin(user_id):
        company = store.get_default_company()
    return company


def _access_denied(user_id: int) -> bool:
    return not _is_admin(user_id) and _user_company(user_id) is None


def _resolve_company_filter(raw: str | None, user_id: int) -> str | None:
    """Всегда возвращает одну компанию, назначенную пользователю.

    Переключение компаний в боте отключено: менеджер видит только свою.
    """
    company = _user_company(user_id)
    return company.id if company else None


def _stats_keyboard(company_id: str | None) -> InlineKeyboardMarkup:
    param = company_id or "mine"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить", callback_data=f"stats:refresh:{param}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔥 Тренды", callback_data=f"trends:show:{param}"
                ),
                InlineKeyboardButton(text="✍️ Новая задача", callback_data="menu:new"),
            ],
        ]
    )


def _trends_keyboard(company_id: str | None) -> InlineKeyboardMarkup:
    param = company_id or "all"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить анализ",
                    callback_data=f"trends:refresh:{param}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика", callback_data=f"stats:refresh:{param}"
                )
            ],
        ]
    )


def _unlinked_channel_posts(
    company_id: str | None, per_company: int = 4
) -> list[dict]:
    companies = (
        [store.get_company(company_id)] if company_id else store.list_companies()
    )
    result: list[dict] = []
    for company in companies:
        if company is None:
            continue
        linked = {
            publication["message_id"]
            for publication in store.list_publications(
                limit=100, company_id=company.id
            )
        }
        shown = 0
        for post in store.list_channel_posts(company.id, limit=20):
            if post["message_id"] in linked or not post.get("reactions"):
                continue
            post["company_name"] = company.name
            result.append(post)
            shown += 1
            if shown >= per_company:
                break
    return result[:8]


def _stats_payload(
    company_id: str | None, user_id: int, error: str = ""
) -> tuple[str, InlineKeyboardMarkup]:
    if _access_denied(user_id):
        return NO_ACCESS_TEXT, InlineKeyboardMarkup(inline_keyboard=[])
    if company_id is None and not _is_admin(user_id):
        company_id = _resolve_company_filter("mine", user_id)
    reports = store.list_reaction_reports(limit=8, company_id=company_id)
    title = "все компании"
    if company_id:
        company = store.get_company(company_id)
        title = company.name if company else "компания"

    total_views = sum(int(report.get("views") or 0) for report in reports)
    total_reactions = sum(report["reactions"]["total"] for report in reports)
    lines = [
        f"📊 Статистика: {title}",
        f"Обновлено: {datetime.now():%d.%m %H:%M}",
    ]
    if error:
        lines.append(f"⚠️ Метрики не обновились: {_short(error, 120)}")

    unlinked = _unlinked_channel_posts(company_id)
    if not reports and not unlinked:
        lines.append("")
        lines.append(
            "Пока нет постов с метриками. Опубликуйте пост и нажмите «Обновить»."
        )

    for report in reports:
        reactions = report["reactions"]
        lines.append("")
        lines.append(f"«{_short(report['request'], 70)}»")
        lines.append(f"👁 {report.get('views', 0)} · {_format_counts(reactions)}")
        lines.append(
            f"Позитив {reactions['positive']} · Негатив {reactions['negative']} "
            f"· Всего {reactions['total']}"
        )
        link = _channel_link(
            report.get("channel_id") or "", int(report.get("message_id") or 0)
        )
        if link:
            lines.append(link)

    if unlinked:
        lines.append("")
        lines.append("Посты канала вне бота:")
        for post in unlinked:
            counts = ", ".join(
                f"{emoji} {count}" for emoji, count in post["reactions"].items()
            )
            prefix = f"{post['company_name']}: " if company_id is None else ""
            lines.append(
                f"• {prefix}«{_short(post.get('text') or '', 55)}» — "
                f"👁 {post.get('views', 0)} · {counts}"
            )
            if post.get("url"):
                lines.append(f"  {post['url']}")

    lines.append("")
    lines.append(f"Итого: 👁 {total_views} · реакций {total_reactions}")
    return "\n".join(lines)[:4000], _stats_keyboard(company_id)


async def _refresh_metrics_for(company_id: str | None) -> str:
    try:
        await analytics_service.refresh_metrics(company_id)
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка обновления метрик")
        return str(exc)


async def _send_stats(
    message: Message, raw_filter: str | None = None, user_id: int | None = None
) -> None:
    uid = user_id or (message.from_user.id if message.from_user else 0)
    if _access_denied(uid):
        await message.answer(NO_ACCESS_TEXT)
        return
    company_id = _resolve_company_filter(raw_filter or "mine", uid)
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception:  # noqa: BLE001
        pass
    error = await _refresh_metrics_for(company_id)
    text, keyboard = _stats_payload(company_id, uid, error)
    await message.answer(
        text, reply_markup=keyboard, link_preview_options=NO_PREVIEW
    )


async def _load_trends(
    company_id: str | None, force: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    try:
        if force or not trend_analyzer.is_fresh(hours=24, company_id=company_id):
            await trend_analyzer.refresh(company_id=company_id)
        insight = store.get_latest_trend_insight(company_id)
        return trend_analyzer.format_for_bot(insight), _trends_keyboard(company_id)
    except TrendError as exc:
        return f"Не удалось обновить тренды: {exc}", _trends_keyboard(company_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка анализа трендов")
        return f"Не удалось проанализировать тренды: {exc}", _trends_keyboard(
            company_id
        )


async def _send_trends(
    message: Message,
    raw_filter: str | None = None,
    user_id: int | None = None,
    force: bool = False,
) -> None:
    uid = user_id or (message.from_user.id if message.from_user else 0)
    if _access_denied(uid):
        await message.answer(NO_ACCESS_TEXT)
        return
    company_id = _resolve_company_filter(raw_filter or "mine", uid)
    if force or not trend_analyzer.is_fresh(hours=24, company_id=company_id):
        await message.answer("Смотрю публичные каналы и анализирую популярные посты...")
    text, keyboard = await _load_trends(company_id, force=force)
    await message.answer(text, reply_markup=keyboard)


def _scheduled_payload(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    company = _user_company(user_id)
    tasks = (
        store.list_scheduled_tasks(company_id=company.id) if company else []
    )
    if not tasks:
        return (
            "🕒 Запланированных публикаций нет.\n\n"
            "Выберите время в карточке согласования: «🕒 Время» → «+1 час».",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📊 Статистика", callback_data="stats:refresh:mine"
                        )
                    ]
                ]
            ),
        )
    lines = ["🕒 Запланированные публикации:"]
    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks[:10]:
        when = (
            task.scheduled_at.astimezone().strftime("%d.%m %H:%M")
            if task.scheduled_at
            else "—"
        )
        company = store.get_company(task.company_id)
        lines.append("")
        lines.append(f"{when} · {company.name if company else '—'}")
        lines.append(f"«{_short(task.request, 70)}»")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Отменить на {when}",
                    callback_data=f"sched:cancel:{task.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="📊 Статистика", callback_data="stats:refresh:mine"
            )
        ]
    )
    return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_scheduled(message: Message, user_id: int | None = None) -> None:
    uid = user_id or (message.from_user.id if message.from_user else 0)
    if _access_denied(uid):
        await message.answer(NO_ACCESS_TEXT)
        return
    text, keyboard = _scheduled_payload(uid)
    await message.answer(text, reply_markup=keyboard)


def _calendar_payload(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    company = _user_company(user_id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data=f"calendar:refresh:{company.id if company else 'mine'}",
                ),
                InlineKeyboardButton(
                    text="📊 Статистика", callback_data="stats:refresh:mine"
                ),
            ]
        ]
    )
    if company is None:
        return NO_ACCESS_TEXT, keyboard
    now = datetime.now().astimezone()
    entries = store.list_calendar_entries(
        company.id, start=now, end=now + timedelta(days=7)
    )
    lines = [f"🗓 Календарь: {company.name}", "Ближайшие 7 дней:"]
    if not entries:
        lines.append("")
        lines.append(
            "Тем пока нет. Администратор добавляет их в веб-панели: "
            "карточка компании → «Контент-календарь»."
        )
    for entry in entries[:12]:
        when = entry["planned_at_dt"].strftime("%d.%m %H:%M")
        status = CALENDAR_STATUS_LABELS.get(entry["status"], entry["status"])
        lines.append("")
        lines.append(f"{when} · {status}")
        lines.append(f"«{_short(entry['topic'], 70)}»")
        if entry.get("error"):
            lines.append(f"⚠️ {_short(entry['error'], 90)}")
    return "\n".join(lines)[:3900], keyboard


async def _send_calendar(message: Message, user_id: int | None = None) -> None:
    uid = user_id or (message.from_user.id if message.from_user else 0)
    if _access_denied(uid):
        await message.answer(NO_ACCESS_TEXT)
        return
    text, keyboard = _calendar_payload(uid)
    await message.answer(text, reply_markup=keyboard)


async def _notify_admin(bot: Bot, text: str) -> None:
    if not settings.admin_telegram_id:
        return
    try:
        await bot.send_message(settings.admin_telegram_id, f"⚠️ {text}")
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось уведомить администратора")


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать работу"),
            BotCommand(command="menu", description="Показать меню кнопок"),
            BotCommand(command="status", description="Статус последней задачи"),
            BotCommand(command="stats", description="Реакции на публикации"),
            BotCommand(command="trends", description="Анализ популярных каналов"),
            BotCommand(command="calendar", description="Контент-календарь"),
            BotCommand(command="help", description="Как поставить задачу"),
            BotCommand(command="cancel", description="Отменить текущее действие"),
        ]
    )


def _current_variant(
    selected_id: str | None, variants: list[Variant]
) -> tuple[Variant | None, int]:
    if not variants:
        return None, 0
    for index, item in enumerate(variants):
        if item.id == selected_id:
            return item, index
    return variants[0], 0


def _compact_caption(
    task: Task, texts: list[Variant], images: list[Variant]
) -> tuple[str, bool]:
    text, text_index = _current_variant(task.selected_text_id, texts)
    image, image_index = _current_variant(task.selected_image_id, images)
    if text is None or image is None:
        return "Нет вариантов", True
    if len(text.content) <= 1024:
        return text.content, True
    return (
        f"🖼 Фото {image_index + 1} из {len(images)} · "
        f"Текст {text_index + 1} из {len(texts)}",
        False,
    )


def _publish_time_label(task: Task) -> str:
    scheduled = task.scheduled_at
    if scheduled:
        if scheduled.tzinfo is None:
            scheduled = scheduled.astimezone()
        now = datetime.now(scheduled.tzinfo)
        if scheduled > now:
            return scheduled.strftime("%d.%m %H:%M")
    return "сейчас после одобрения"


def _preview_caption(task: Task, text: Variant, company: Company | None) -> str:
    body = format_post_html(fit_caption(text.content, limit=900))
    channel = _channel(company) if company else settings.telegram_channel_id
    channel = channel or "не подключён"
    return (
        f"{body}\n\n———\n"
        f"Площадка: Telegram-канал ({channel})\n"
        f"Время: {_publish_time_label(task)}"
    )


QUICK_EDITS = {
    "shorter": (
        "✂️ Короче",
        "Сократи текст до 500–650 знаков, сохрани факты, структуру, вопрос и хештеги. Убери воду.",
    ),
    "headline": (
        "🎯 Заголовок",
        "Перепиши заголовок: короче, конкретнее и цепляюще, до 60 знаков. Остальной текст не меняй без необходимости.",
    ),
    "numbers": (
        "🔢 Цифры",
        "Добавь 2–3 конкретных факта или цифры только из базы знаний компании. Не выдумывай данные.",
    ),
    "simple": (
        "💬 Проще",
        "Упрости язык: короче предложения, меньше канцелярита, сложное объясни простыми словами.",
    ),
    "expert": (
        "🧠 Экспертнее",
        "Сделай тон более экспертным и уверенным, добавь инженерную конкретику из материалов компании.",
    ),
    "cta": (
        "📣 CTA",
        "Усиль финальный вопрос и призыв к обсуждению, сделай его конкретным для аудитории.",
    ),
}


def _quick_edit_keyboard(task_id: str) -> InlineKeyboardMarkup:
    rows = []
    for index in range(0, len(QUICK_EDITS), 2):
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"qedit:{task_id}:{key}",
                )
                for key, (label, _) in list(QUICK_EDITS.items())[index : index + 2]
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="✖️ Закрыть", callback_data="qeditclose")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _compact_keyboard(
    task: Task, texts: list[Variant], images: list[Variant]
) -> InlineKeyboardMarkup:
    _, text_index = _current_variant(task.selected_text_id, texts)
    _, image_index = _current_variant(task.selected_image_id, images)
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []
    if len(texts) > 1:
        navigation.append(
            InlineKeyboardButton(
                text=f"🔄 Другой текст ({text_index + 1}/{len(texts)})",
                callback_data=f"nexttext:{task.id}",
            )
        )
    if len(images) > 1:
        navigation.append(
            InlineKeyboardButton(
                text=f"🔄 Другое фото ({image_index + 1}/{len(images)})",
                callback_data=f"nextimage:{task.id}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [InlineKeyboardButton(text="🖼 Ещё фото", callback_data=f"moreimg:{task.id}")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⚡ Быстрые правки", callback_data=f"qeditmenu:{task.id}"
            )
        ]
    )
    time_label = _publish_time_label(task)
    rows.append(
        [
            InlineKeyboardButton(
                text=f"🕒 Время: {time_label}",
                callback_data=f"pubtime:{task.id}",
            )
        ]
    )
    approve_label = (
        "✅ Опубликовать сейчас"
        if time_label == "сейчас после одобрения"
        else f"✅ Запланировать на {time_label}"
    )
    rows.append(
        [
            InlineKeyboardButton(text=approve_label, callback_data=f"approve:{task.id}"),
            InlineKeyboardButton(text="✏️ На доработку", callback_data=f"revise:{task.id}"),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{task.id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _delete_old_ui(bot: Bot, task: Task) -> None:
    ui = store.get_task_ui(task.id)
    if not ui:
        return
    message_ids = {
        ui.get("text_message_id"),
        ui.get("photo_message_id"),
        ui.get("control_message_id"),
    }
    for message_id in message_ids:
        if not message_id:
            continue
        try:
            await bot.delete_message(task.user_id, message_id)
        except Exception:  # noqa: BLE001
            pass
    store.clear_task_ui(task.id)


async def _send_variant_ui(bot: Bot, task: Task, company: Company | None = None) -> None:
    """Показывает один текст и одно фото с кнопками переключения."""
    await _delete_old_ui(bot, task)
    texts, images = _selected_variants(task)
    text, text_index = _current_variant(task.selected_text_id, texts)
    image, _ = _current_variant(task.selected_image_id, images)
    if text is None or image is None:
        await bot.send_message(
            task.user_id,
            "Не удалось получить варианты. Нажмите «Повторить» или поставьте задачу заново.",
        )
        return
    caption, in_caption = _compact_caption(task, texts, images)
    if in_caption:
        caption = _preview_caption(task, text, company)
    else:
        caption = f"{caption}\n\nВремя: {_publish_time_label(task)}"
    photo_message = await bot.send_photo(
        task.user_id,
        FSImage(image.image_path),
        caption=caption,
        parse_mode="HTML" if in_caption else None,
        reply_markup=_compact_keyboard(task, texts, images),
    )
    text_message = None
    if not in_caption:
        text_message = await bot.send_message(
            task.user_id,
            f"🧩 Текст {text_index + 1} из {len(texts)}\n\n"
            f"{format_post_html(text.content)}",
            parse_mode="HTML",
        )
    store.save_task_ui(
        task.id,
        text_message_id=text_message.message_id if text_message else None,
        photo_message_id=photo_message.message_id,
        control_message_id=photo_message.message_id,
        text_variant_id=text.id,
        image_variant_id=image.id,
    )


async def _refresh_compact_ui(bot: Bot, task: Task) -> None:
    texts, images = _selected_variants(task)
    text, text_index = _current_variant(task.selected_text_id, texts)
    image, _ = _current_variant(task.selected_image_id, images)
    if text is None or image is None:
        return
    ui = store.get_task_ui(task.id)
    if not ui or not ui.get("photo_message_id"):
        await _send_variant_ui(bot, task)
        return

    company = store.get_company(task.company_id)
    caption, in_caption = _compact_caption(task, texts, images)
    if in_caption:
        caption = _preview_caption(task, text, company)
    else:
        caption = f"{caption}\n\nВремя: {_publish_time_label(task)}"
    keyboard = _compact_keyboard(task, texts, images)
    photo_message_id = ui["photo_message_id"]
    image_changed = ui.get("image_variant_id") != image.id
    parse_mode = "HTML" if in_caption else None

    if image_changed:
        try:
            await bot.edit_message_media(
                chat_id=task.user_id,
                message_id=photo_message_id,
                media=InputMediaPhoto(
                    media=FSImage(image.image_path),
                    caption=caption,
                    parse_mode=parse_mode,
                ),
                reply_markup=keyboard,
            )
        except Exception:  # noqa: BLE001
            try:
                await bot.edit_message_caption(
                    chat_id=task.user_id,
                    message_id=photo_message_id,
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=keyboard,
                )
            except Exception:  # noqa: BLE001
                pass
    else:
        try:
            await bot.edit_message_caption(
                chat_id=task.user_id,
                message_id=photo_message_id,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=keyboard,
            )
        except Exception:  # noqa: BLE001
            pass

    if in_caption:
        if ui.get("text_message_id"):
            try:
                await bot.delete_message(task.user_id, ui["text_message_id"])
            except Exception:  # noqa: BLE001
                pass
            store.save_task_ui(task.id, text_message_id=None)
    elif ui.get("text_message_id"):
        try:
            await bot.edit_message_text(
                chat_id=task.user_id,
                message_id=ui["text_message_id"],
                text=f"🧩 Текст {text_index + 1} из {len(texts)}\n\n"
                f"{format_post_html(text.content)}",
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            pass
    else:
        text_message = await bot.send_message(
            task.user_id,
            f"🧩 Текст {text_index + 1} из {len(texts)}\n\n"
            f"{format_post_html(text.content)}",
            parse_mode="HTML",
        )
        store.save_task_ui(task.id, text_message_id=text_message.message_id)

    store.save_task_ui(
        task.id,
        text_variant_id=text.id,
        image_variant_id=image.id,
    )


def FSImage(path: str):
    return FSInputFile(str(resolve_path(path)))


async def _generate_task_variants(
    bot: Bot,
    task: Task,
    company: Company,
    request: str,
    history: str,
    revision: tuple[str, str] | None = None,
    keep_status_on_error: bool = False,
) -> None:
    progress = await bot.send_message(
        task.user_id,
        "Генерирую 2 варианта текста и 2 изображения: DeepSeek + anymodel GPT Image 2.5. "
        "Обычно это занимает 1–2 минуты.",
    )
    try:
        drafts, images = await generator.generate_variants(
            company=company,
            request=request,
            history=history,
            image_dir=settings.images_dir,
            image_prefix=task.id,
            text_count=TEXT_VARIANTS,
            image_count=IMAGE_VARIANTS,
            feedback=_build_feedback_text(company.id),
            trends=_latest_trends_text(company.id),
            revision=revision,
        )
        batch = store.next_batch(task.id)
        text_variants = [
            store.add_variant(
                task.id,
                "text",
                batch,
                index,
                content=fit_caption(draft.full_text, limit=1000),
                image_prompt=draft.image_prompt,
            )
            for index, draft in enumerate(drafts)
        ]
        image_variants = [
            store.add_variant(task.id, "image", batch, index, image_path=image)
            for index, image in enumerate(images)
        ]
        store.select_variant(task.id, "text", text_variants[0].id)
        store.select_variant(task.id, "image", image_variants[0].id)
        store.update_task(
            task.id,
            status="pending_approval",
            error="",
            revision_comment=revision[1] if revision else "",
        )
        store.add_history(task.user_id, "assistant", text_variants[0].content)
        try:
            await bot.delete_message(task.user_id, progress.message_id)
        except Exception:  # noqa: BLE001
            pass
        updated = store.get_task(task.id)
        if updated:
            await _send_variant_ui(bot, updated, company)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка генерации вариантов")
        try:
            await bot.delete_message(task.user_id, progress.message_id)
        except Exception:  # noqa: BLE001
            pass
        if keep_status_on_error:
            store.update_task(task.id, error=str(exc))
            await bot.send_message(
                task.user_id,
                f"Не удалось применить правки: {exc}\n\n"
                "Старый черновик остался на согласовании — можно одобрить его или попробовать правки ещё раз.",
            )
        else:
            store.update_task(task.id, status="failed", error=str(exc))
            await bot.send_message(
                task.user_id,
                f"Не получилось подготовить материал: {exc}\n\n"
                "Проверьте DEEPSEEK_API_KEY, ANYMODEL_API_KEY и балансы API. "
                "Можно повторить задачу кнопкой ниже.",
                reply_markup=_retry_keyboard(task.id),
            )
        await _notify_admin(bot, f"Ошибка генерации задачи {task.id}: {exc}")


async def _handle_new_task(message: Message, company: Company, text: str) -> None:
    history = store.get_history_text(message.from_user.id)
    task = store.create_task(company.id, message.from_user.id, text)
    logger.info("New task %s from user_id=%s", task.id, message.from_user.id)
    store.add_history(message.from_user.id, "user", text)
    await _generate_task_variants(
        message.bot,
        task,
        company,
        request=text,
        history=history,
        revision=None,
    )


async def _revise_task(message: Message, company: Company, task: Task, comment: str) -> None:
    history = store.get_history_text(task.user_id)
    previous_post = task.post_text
    if not previous_post:
        texts, _ = _selected_variants(task)
        previous_post = texts[0].content if texts else task.request
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception:  # noqa: BLE001
        pass
    await _generate_task_variants(
        message.bot,
        task,
        company,
        request=task.request,
        history=history,
        revision=(previous_post, comment),
        keep_status_on_error=True,
    )


# --------------------------------------------------------------------- commands
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    logger.info(
        "Start: user_id=%s username=%s",
        message.from_user.id,
        message.from_user.username,
    )
    company = _user_company(message.from_user.id)
    if company is None:
        await message.answer(NO_ACCESS_TEXT)
        return
    store.bind_user(message.from_user.id, company.id)
    await message.answer(
        f"Здравствуйте! Я AI-маркетолог компании «{company.name}».\n\n"
        "Поставьте задачу обычным языком, например:\n"
        "• «Подготовь пост про архитектурное стекло для фасадов бизнес-центра»\n"
        "• «Нужна публикация о преимуществах стеклопакетов для застройщиков»\n\n"
        "Я подготовлю 2 варианта текста и 2 изображения, пришлю их с кнопками выбора. "
        "Публикация начнётся только после «Одобрить выбранное».\n\n"
        "Все основные действия — на кнопках внизу.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Как поставить задачу:\n"
        "1. Напишите, что нужно подготовить, для какой аудитории и с каким акцентом.\n"
        "2. Я пришлю 2 варианта текста и 2 изображения.\n"
        "3. Выберите кнопками «Текст 1/2» и «Фото 1/2» — можно смешивать.\n"
        "4. Нажмите «Одобрить выбранное» или «На доработку».\n\n"
        "В карточке черновика нажмите «🕒 Время» — можно выбрать «сейчас», "
        "«+1 час», «+3 часа», «завтра в 10:00» или ввести свою дату и время.\n\n"
        "Кнопки меню:\n"
        "• 📊 Статистика — просмотры и реакции, обновляются из канала;\n"
        "• 🔥 Тренды — разбор публичных каналов и идеи;\n"
        "• 🗓 Календарь — темы на ближайшую неделю;\n"
        "• 🕒 Запланировано — отложенные публикации и отмена;\n"
        "• ✍️ Новая задача — начать новый пост.\n\n"
        "В карточке черновика есть «⚡ Быстрые правки»: короче, другой заголовок, "
        "цифры, проще, экспертнее, CTA. Добавляется новый вариант текста, старые "
        "остаются, фото не меняется.\n\n"
        "Доступ дан только к одной компании — той, которую назначил администратор.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer(
        "Меню всегда под полем ввода. Выберите действие кнопкой.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if _access_denied(message.from_user.id):
        await message.answer(NO_ACCESS_TEXT)
        return
    company = _user_company(message.from_user.id)
    task = store.get_last_task(message.from_user.id)
    if task is None:
        await message.answer("Пока нет ни одной задачи. Напишите, что нужно подготовить.")
        return
    label = STATUS_LABELS.get(task.status, task.status)
    extra = ""
    if task.status == "pending_approval":
        texts, images = _selected_variants(task)
        extra = f"\nВыбрано: {_variant_label(task, texts, images)}"
    if task.status == "scheduled" and task.scheduled_at:
        extra += f"\nЗапланировано на {task.scheduled_at.strftime('%d.%m %H:%M')}"
    if task.error:
        extra += f"\nОшибка: {task.error}"
    await message.answer(
        f"Компания: {company.name if company else '—'}\n"
        f"Последняя задача: {task.request[:200]}\n"
        f"Статус: {label}{extra}\n"
        f"Обновлено: {task.updated_at.strftime('%d.%m.%Y %H:%M')}"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await _send_stats(message)


@router.message(Command("trends"))
async def cmd_trends(message: Message) -> None:
    await _send_trends(message)


@router.message(Command("calendar"))
async def cmd_calendar(message: Message) -> None:
    await _send_calendar(message)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Текущее действие отменено. Можно поставить новую задачу.")


# ------------------------------------------------------------------ menu buttons
@router.message(F.text == BTN_STATS)
async def menu_stats(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_stats(message)


@router.message(F.text == BTN_TRENDS)
async def menu_trends(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_trends(message)


@router.message(F.text == BTN_SCHEDULED)
async def menu_scheduled(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_scheduled(message)


@router.message(F.text == BTN_CALENDAR)
async def menu_calendar(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_calendar(message)


@router.message(F.text == BTN_HELP)
async def menu_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await cmd_help(message)


@router.message(F.text == BTN_NEW)
async def menu_new(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Напишите задачу обычным языком: что подготовить, для кого и с каким акцентом.\n\n"
        "Например: «Подготовь пост про архитектурное стекло для фасадов бизнес-центра».",
        reply_markup=MAIN_KEYBOARD,
    )


@router.callback_query(F.data.startswith("stats:"))
async def callback_stats(callback: CallbackQuery) -> None:
    parts = callback.data.split(":", 2)
    raw = parts[2] if len(parts) > 2 else "mine"
    uid = callback.from_user.id
    if _access_denied(uid):
        await callback.answer(NO_ACCESS_TEXT, show_alert=True)
        return
    company_id = _resolve_company_filter(raw, uid)
    await callback.answer("Обновляю метрики...")
    error = await _refresh_metrics_for(company_id)
    text, keyboard = _stats_payload(company_id, uid, error)
    if callback.message:
        try:
            await callback.message.edit_text(
                text, reply_markup=keyboard, link_preview_options=NO_PREVIEW
            )
            return
        except Exception:  # noqa: BLE001
            pass
    await callback.bot.send_message(
        uid, text, reply_markup=keyboard, link_preview_options=NO_PREVIEW
    )


@router.callback_query(F.data.startswith("calendar:refresh:"))
async def callback_calendar_refresh(callback: CallbackQuery) -> None:
    uid = callback.from_user.id
    if _access_denied(uid):
        await callback.answer(NO_ACCESS_TEXT, show_alert=True)
        return
    text, keyboard = _calendar_payload(uid)
    await callback.answer("Обновлено")
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            return
        except Exception:  # noqa: BLE001
            pass
    await callback.bot.send_message(uid, text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("trends:show:"))
async def callback_trends_show(callback: CallbackQuery) -> None:
    raw = callback.data.split(":", 2)[2]
    if _access_denied(callback.from_user.id):
        await callback.answer(NO_ACCESS_TEXT, show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await _send_trends(
            callback.message, raw_filter=raw, user_id=callback.from_user.id
        )


@router.callback_query(F.data.startswith("trends:refresh:"))
async def callback_trends_refresh(callback: CallbackQuery) -> None:
    raw = callback.data.split(":", 2)[2]
    uid = callback.from_user.id
    if _access_denied(uid):
        await callback.answer(NO_ACCESS_TEXT, show_alert=True)
        return
    company_id = _resolve_company_filter(raw, uid)
    await callback.answer("Обновляю анализ...")
    text, keyboard = await _load_trends(company_id, force=True)
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            return
        except Exception:  # noqa: BLE001
            pass
    await callback.bot.send_message(uid, text, reply_markup=keyboard)


@router.callback_query(F.data == "menu:new")
async def callback_menu_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Напишите задачу обычным языком: что подготовить, для кого и с каким акцентом.",
            reply_markup=MAIN_KEYBOARD,
        )


@router.callback_query(F.data.startswith("sched:cancel:"))
async def callback_sched_cancel(callback: CallbackQuery) -> None:
    task_id = callback.data.split(":", 2)[2]
    task = store.get_task(task_id)
    uid = callback.from_user.id
    if task is None or task.status != "scheduled":
        await callback.answer(
            "Запланированная публикация не найдена", show_alert=True
        )
        return
    if not _is_admin(uid) and task.user_id != uid:
        await callback.answer("Нет доступа к этой публикации", show_alert=True)
        return
    store.update_task(task.id, status="rejected", scheduled_at=None)
    await callback.answer("Публикация отменена")
    text, keyboard = _scheduled_payload(uid)
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            return
        except Exception:  # noqa: BLE001
            pass
    await callback.bot.send_message(uid, text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("qeditmenu:"))
async def callback_qedit_menu(callback: CallbackQuery) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None or task.status != "pending_approval":
        await callback.answer(
            "Черновик уже не на согласовании", show_alert=True
        )
        return
    if callback.message:
        await callback.message.answer(
            "⚡ Что поправить в тексте?\n\n"
            "Будет добавлен новый вариант текста, старые останутся. Фото не изменится.",
            reply_markup=_quick_edit_keyboard(task.id),
        )
    await callback.answer()


@router.callback_query(F.data == "qeditclose")
async def callback_qedit_close(callback: CallbackQuery) -> None:
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:  # noqa: BLE001
            pass
    await callback.answer("Закрыто")


@router.callback_query(F.data.startswith("qedit:"))
async def callback_quick_edit(callback: CallbackQuery, bot: Bot) -> None:
    _, task_id, action = callback.data.split(":", 2)
    task = store.get_task(task_id)
    if task is None or task.status != "pending_approval":
        await callback.answer(
            "Черновик уже не на согласовании", show_alert=True
        )
        return
    label, instruction = QUICK_EDITS.get(action, ("", ""))
    if not label:
        await callback.answer("Неизвестная правка", show_alert=True)
        return
    variant = store.get_variant(task.selected_text_id or "")
    if variant is None:
        await callback.answer("Текст не найден", show_alert=True)
        return
    company = store.get_company(task.company_id)
    if company is None:
        await callback.answer("Компания не найдена", show_alert=True)
        return

    await callback.answer(f"Правлю: {label}")
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:  # noqa: BLE001
            pass
    try:
        await bot.send_chat_action(task.user_id, "typing")
    except Exception:  # noqa: BLE001
        pass
    try:
        draft = await generator.quick_edit(company, variant.content, instruction)
        content = draft.full_text
        if action == "shorter":
            content = shorten_post(content, limit=650)
        batch = store.latest_batch(task.id)
        batch_texts = store.list_variants(
            task.id, kind="text", batch_no=batch
        )
        next_index = max(
            (item.index_no for item in batch_texts), default=-1
        ) + 1
        new_variant = store.add_variant(
            task.id,
            "text",
            batch,
            next_index,
            content=fit_caption(content, limit=1000),
            image_prompt=draft.image_prompt or variant.image_prompt,
        )
        store.select_variant(task.id, "text", new_variant.id)
        store.add_history(
            task.user_id,
            "assistant",
            f"Быстрая правка ({label}), вариант {next_index + 1}: {draft.full_text[:600]}",
        )
        updated = store.get_task(task.id)
        if updated:
            await _refresh_compact_ui(bot, updated)
        all_texts = store.list_variants(task.id, kind="text", batch_no=batch)
        position = next(
            (
                index + 1
                for index, item in enumerate(all_texts)
                if item.id == new_variant.id
            ),
            len(all_texts),
        )
        await bot.send_message(
            task.user_id,
            f"✅ {label}: добавлен новый вариант текста "
            f"({position} из {len(all_texts)}).\n\n"
            "Старые варианты доступны кнопкой «Другой текст». Фото не изменилось.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка быстрой правки")
        await bot.send_message(
            task.user_id,
            f"❌ Не удалось применить правку: {exc}",
        )


# --------------------------------------------------------------------- revisions
@router.message(RevisionStates.waiting_comment)
async def revision_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = data.get("task_id")
    await state.clear()
    task = store.get_task(task_id) if task_id else None
    if task is None:
        await message.answer("Не нашёл задачу для доработки. Поставьте новую задачу.")
        return
    company = _user_company(message.from_user.id)
    if (
        task.user_id != message.from_user.id
        or company is None
        or task.company_id != company.id
    ):
        await message.answer(NO_ACCESS_TEXT)
        return
    comment = (message.text or "").strip()
    if not comment:
        await message.answer("Напишите комментарий текстом — что именно изменить.")
        return
    store.add_history(task.user_id, "user", f"Правки: {comment}")
    await message.answer("Принял правки. Генерирую новые варианты текста и изображений...")
    await _revise_task(message, company, task, comment)


@router.message(PublishTimeStates.waiting_custom, F.text)
async def custom_publish_time(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = data.get("custom_time_task_id")
    task = store.get_task(task_id) if task_id else None
    if task is None:
        await state.clear()
        await message.answer(
            "Не нашёл задачу для планирования. Откройте черновик и нажмите «Время» ещё раз."
        )
        return
    company = _user_company(message.from_user.id)
    if (
        task.user_id != message.from_user.id
        or company is None
        or task.company_id != company.id
    ):
        await state.clear()
        await message.answer(NO_ACCESS_TEXT)
        return
    try:
        scheduled = parse_custom_publish_time(message.text or "")
    except PublishTimeError as exc:
        await message.answer(f"{exc}\n\nВведите время ещё раз или отправьте /cancel.")
        return
    store.update_task(task.id, scheduled_at=scheduled)
    await state.clear()
    await message.answer(
        f"✅ Время публикации: {scheduled.strftime('%d.%m %H:%M')}.\n"
        "Когда будете готовы, нажмите кнопку планирования под черновиком."
    )
    updated = store.get_task(task.id)
    if updated:
        await _refresh_compact_ui(message.bot, updated)


# --------------------------------------------------------------------- free text
@router.message(F.text)
async def on_text(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == PublishTimeStates.waiting_custom.state:
        await custom_publish_time(message, state)
        return
    if current_state == RevisionStates.waiting_comment.state:
        await revision_comment(message, state)
        return
    text = (message.text or "").strip()
    if not text:
        return
    company = _user_company(message.from_user.id)
    if company is None:
        await message.answer(NO_ACCESS_TEXT)
        return
    pending = store.get_pending_task(message.from_user.id)
    if pending is not None and pending.company_id == company.id:
        store.add_history(message.from_user.id, "user", f"Правки: {text}")
        await message.answer(
            "У вас есть черновик на согласовании. Принял текст как комментарий к доработке. "
            "Генерирую новые варианты."
        )
        await _revise_task(message, company, pending, text)
        return
    has_last_task = store.get_last_task(message.from_user.id) is not None
    intent = classify_intent(text, has_last_task)
    if intent == "question":
        await _answer_question(message, company, text)
    else:
        await _handle_new_task(message, company, text)


async def _answer_question(message: Message, company: Company, text: str) -> None:
    history = store.get_history_text(message.from_user.id)
    store.add_history(message.from_user.id, "user", text)
    await message.answer("Смотрю историю предыдущих действий и готовлю ответ...")
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception:  # noqa: BLE001
        pass
    try:
        answer = await generator.answer_question(company, history, text)
        store.add_history(message.from_user.id, "assistant", answer)
        await message.answer(answer)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка ответа на вопрос")
        await message.answer(f"Не получилось ответить: {exc}")
        await _notify_admin(message.bot, f"Ошибка ответа на вопрос: {exc}")


# ---------------------------------------------------------------- variant callbacks
@router.callback_query(F.data.startswith("nexttext:"))
async def callback_next_text(callback: CallbackQuery, bot: Bot) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    texts, _ = _selected_variants(task)
    if len(texts) < 2:
        await callback.answer("Других текстов нет", show_alert=True)
        return
    _, current_index = _current_variant(task.selected_text_id, texts)
    next_index = (current_index + 1) % len(texts)
    store.select_variant(task.id, "text", texts[next_index].id)
    task = store.get_task(task.id)
    await _refresh_compact_ui(bot, task)
    await callback.answer(f"Текст {next_index + 1} из {len(texts)}")


@router.callback_query(F.data.startswith("nextimage:"))
async def callback_next_image(callback: CallbackQuery, bot: Bot) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    texts, images = _selected_variants(task)
    if len(images) < 2:
        await callback.answer("Других фото нет", show_alert=True)
        return
    _, current_index = _current_variant(task.selected_image_id, images)
    next_index = (current_index + 1) % len(images)
    store.select_variant(task.id, "image", images[next_index].id)
    task = store.get_task(task.id)
    await _refresh_compact_ui(bot, task)
    await callback.answer(f"Фото {next_index + 1} из {len(images)}")


@router.callback_query(F.data.startswith("text:"))
async def callback_text(callback: CallbackQuery) -> None:
    _, task_id, variant_id = callback.data.split(":", 2)
    task = store.get_task(task_id)
    variant = store.get_variant(variant_id)
    if task is None or variant is None or variant.task_id != task.id:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    if variant.batch_no != store.latest_batch(task.id):
        await callback.answer("Этот вариант уже не актуален", show_alert=True)
        return
    store.select_variant(task.id, "text", variant.id)
    task = store.get_task(task.id)
    texts, images = _selected_variants(task)
    if callback.message:
        try:
            await callback.message.edit_text(
                _control_text(task, texts, images),
                reply_markup=_variant_keyboard(task, texts, images),
            )
        except Exception:  # noqa: BLE001
            pass
    index = next(
        (position + 1 for position, item in enumerate(texts) if item.id == variant.id), "?"
    )
    await callback.answer(f"Выбран текст {index}")


@router.callback_query(F.data.startswith("image:"))
async def callback_image(callback: CallbackQuery) -> None:
    _, task_id, variant_id = callback.data.split(":", 2)
    task = store.get_task(task_id)
    variant = store.get_variant(variant_id)
    if task is None or variant is None or variant.task_id != task.id:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    if variant.batch_no != store.latest_batch(task.id):
        await callback.answer("Этот вариант уже не актуален", show_alert=True)
        return
    store.select_variant(task.id, "image", variant.id)
    task = store.get_task(task.id)
    texts, images = _selected_variants(task)
    if callback.message:
        try:
            await callback.message.edit_text(
                _control_text(task, texts, images),
                reply_markup=_variant_keyboard(task, texts, images),
            )
        except Exception:  # noqa: BLE001
            pass
    index = next(
        (position + 1 for position, item in enumerate(images) if item.id == variant.id), "?"
    )
    await callback.answer(f"Выбрано фото {index}")


@router.callback_query(F.data.startswith("moreimg:"))
async def callback_more_image(callback: CallbackQuery, bot: Bot) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    company = store.get_company(task.company_id)
    if company is None:
        await callback.answer("Компания не найдена", show_alert=True)
        return
    texts, images = _selected_variants(task)
    if not texts:
        await callback.answer("Сначала нужен вариант текста", show_alert=True)
        return
    text_variant = next(
        (item for item in texts if item.id == task.selected_text_id), texts[0]
    )
    draft = parse_post_response(text_variant.content)
    draft.image_prompt = text_variant.image_prompt
    await callback.answer("Генерирую ещё фото...")
    try:
        generated = await generator.generate_image_variants(
            company=company,
            drafts=[draft],
            image_dir=settings.images_dir,
            prefix=f"{task.id}-more-{len(images) + 1}",
            count=1,
        )
        batch = store.latest_batch(task.id)
        new_variant = store.add_variant(
            task.id, "image", batch, len(images), image_path=generated[0]
        )
        store.select_variant(task.id, "image", new_variant.id)
        task = store.get_task(task.id)
        await _refresh_compact_ui(bot, task)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка генерации дополнительного фото")
        await bot.send_message(
            task.user_id,
            f"Не удалось сгенерировать ещё фото: {exc}",
        )


@router.callback_query(F.data.startswith("pubtime:"))
async def callback_pubtime(callback: CallbackQuery, bot: Bot) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сейчас", callback_data=f"pubset:{task.id}:now"),
                InlineKeyboardButton(text="+1 час", callback_data=f"pubset:{task.id}:60"),
                InlineKeyboardButton(text="+3 часа", callback_data=f"pubset:{task.id}:180"),
            ],
            [
                InlineKeyboardButton(
                    text="Завтра в 10:00", callback_data=f"pubset:{task.id}:tomorrow"
                ),
                InlineKeyboardButton(
                    text="🕘 Своё время", callback_data=f"pubcustom:{task.id}"
                ),
            ],
            [
                InlineKeyboardButton(text="Отмена", callback_data=f"pubcancel:{task.id}"),
            ],
        ]
    )
    await bot.send_message(
        task.user_id,
        "Когда опубликовать пост?",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pubcustom:"))
async def callback_pubcustom(callback: CallbackQuery, state: FSMContext) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    if task.status != "pending_approval":
        await callback.answer(
            f"Задача уже в статусе: {STATUS_LABELS.get(task.status, task.status)}",
            show_alert=True,
        )
        return
    await state.set_state(PublishTimeStates.waiting_custom)
    await state.update_data(custom_time_task_id=task.id)
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:  # noqa: BLE001
            pass
    await callback.bot.send_message(
        callback.from_user.id,
        "Введите дату и время публикации.\n\n"
        "Примеры:\n"
        "• 15.11 18:30\n"
        "• 15.11.2026 18:30\n"
        "• 2026-11-15 18:30\n"
        "• завтра 10:00\n"
        "• через 2 часа\n\n"
        "Отмена — /cancel.",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pubset:"))
async def callback_pubset(callback: CallbackQuery, bot: Bot) -> None:
    _, task_id, value = callback.data.split(":", 2)
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    now = datetime.now().astimezone()
    if value == "now":
        scheduled = None
    elif value == "tomorrow":
        scheduled = (now + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
    else:
        try:
            scheduled = now + timedelta(minutes=int(value))
        except ValueError:
            scheduled = None
    store.update_task(task.id, scheduled_at=scheduled)
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:  # noqa: BLE001
            pass
    task = store.get_task(task.id)
    await _refresh_compact_ui(bot, task)
    await callback.answer(
        "Время: сейчас" if scheduled is None else f"Время: {scheduled.strftime('%d.%m %H:%M')}"
    )


@router.callback_query(F.data.startswith("pubcancel:"))
async def callback_pubcancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:  # noqa: BLE001
            pass
    await callback.answer("Отменено")


@router.callback_query(F.data.startswith("cancelpub:"))
async def callback_cancel_scheduled(callback: CallbackQuery, bot: Bot) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None or task.status != "scheduled":
        await callback.answer("Запланированная публикация не найдена", show_alert=True)
        return
    store.update_task(task.id, status="rejected", scheduled_at=None)
    if callback.message:
        try:
            await callback.message.edit_text(
                "❌ Запланированная публикация отменена.", reply_markup=None
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer("Отменено")
    await bot.send_message(
        task.user_id, "❌ Запланированная публикация отменена. В канал она не уйдёт."
    )


@router.callback_query(F.data.startswith("approve:"))
async def callback_approve(callback: CallbackQuery, bot: Bot) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    if callback.from_user.id not in {task.user_id, settings.admin_telegram_id}:
        await callback.answer("Нет прав на согласование", show_alert=True)
        return
    if task.status != "pending_approval":
        await callback.answer(
            f"Задача уже в статусе: {STATUS_LABELS.get(task.status, task.status)}",
            show_alert=True,
        )
        return
    company = store.get_company(task.company_id)
    if company is None:
        await callback.answer("Компания не найдена", show_alert=True)
        return

    texts, images = _selected_variants(task)
    text_id = task.selected_text_id if any(item.id == task.selected_text_id for item in texts) else (
        texts[0].id if texts else None
    )
    image_id = task.selected_image_id if any(item.id == task.selected_image_id for item in images) else (
        images[0].id if images else None
    )
    if text_id:
        store.select_variant(task.id, "text", text_id)
    if image_id:
        store.select_variant(task.id, "image", image_id)
    task = store.get_task(task.id)
    if not task.post_text or not task.image_path:
        await callback.answer("Не выбраны текст или изображение", show_alert=True)
        return

    scheduled = task.scheduled_at
    now = datetime.now().astimezone()
    if scheduled and scheduled > now:
        store.update_task(task.id, status="scheduled")
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
        await callback.answer("Запланировано")
        await bot.send_message(
            task.user_id,
            f"✅ Запланировано на {scheduled.strftime('%d.%m %H:%M')}. "
            "Пост опубликуется автоматически.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ Отменить публикацию",
                            callback_data=f"cancelpub:{task.id}",
                        )
                    ]
                ]
            ),
        )
        return

    await callback.answer("Публикую...")
    try:
        published = await _publish(bot, task, company)
        channel = _channel(company)
        store.save_publication(task.id, channel, published.chat.id, published.message_id)
        store.update_task(
            task.id,
            status="published",
            published_message_id=published.message_id,
            published_chat_id=published.chat.id,
            error="",
        )
        logger.info(
            "Published task %s to %s, message_id=%s",
            task.id,
            channel,
            published.message_id,
        )
        texts, images = _selected_variants(task)
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
            if callback.message.text:
                try:
                    await callback.message.edit_text(
                        f"✅ Опубликовано.\n{_variant_label(task, texts, images)}",
                        reply_markup=None,
                    )
                except Exception:  # noqa: BLE001
                    pass
        link = _channel_link(channel, published.message_id)
        suffix = f"\n{link}" if link else ""
        await bot.send_message(
            task.user_id,
            f"✅ Задача выполнена. Материал опубликован в Telegram-канале.{suffix}\n"
            "Реакции на пост смотрите в /stats.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка публикации")
        await bot.send_message(task.user_id, f"❌ Не удалось опубликовать: {exc}")
        await _notify_admin(bot, f"Ошибка публикации задачи {task.id}: {exc}")


@router.callback_query(F.data.startswith("revise:"))
async def callback_revise(callback: CallbackQuery, state: FSMContext) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    if callback.from_user.id not in {task.user_id, settings.admin_telegram_id}:
        await callback.answer("Нет прав на согласование", show_alert=True)
        return
    if task.status != "pending_approval":
        await callback.answer("Задача уже не на согласовании", show_alert=True)
        return
    await state.set_state(RevisionStates.waiting_comment)
    await state.update_data(task_id=task.id)
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Напишите, что изменить: тон, заголовок, факты, структуру или изображение. "
            "Я сгенерирую новые варианты текста и фото."
        )


@router.callback_query(F.data.startswith("reject:"))
async def callback_reject(callback: CallbackQuery) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    if callback.from_user.id not in {task.user_id, settings.admin_telegram_id}:
        await callback.answer("Нет прав на согласование", show_alert=True)
        return
    if task.status not in {"pending_approval", "failed"}:
        await callback.answer("Задача уже не на согласовании", show_alert=True)
        return
    store.update_task(task.id, status="rejected")
    store.add_history(task.user_id, "assistant", "Публикация отклонена пользователем.")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        if callback.message.text:
            try:
                await callback.message.edit_text("❌ Публикация отклонена.", reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
    await callback.answer("Отклонено")
    if callback.message:
        await callback.message.answer(
            "❌ Публикация отклонена. Материал не будет опубликован."
        )


@router.callback_query(F.data.startswith("retry:"))
async def callback_retry(callback: CallbackQuery, bot: Bot) -> None:
    task_id = callback.data.split(":", 1)[1]
    task = store.get_task(task_id)
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    if callback.from_user.id not in {task.user_id, settings.admin_telegram_id}:
        await callback.answer("Нет прав", show_alert=True)
        return
    if task.status not in {"failed", "rejected"}:
        await callback.answer("Повтор доступен только для ошибки или отклонённой задачи", show_alert=True)
        return
    company = store.get_company(task.company_id)
    if company is None:
        await callback.answer("Компания не найдена", show_alert=True)
        return
    await callback.answer("Повторяю...")
    if callback.message:
        try:
            await callback.message.edit_text("🔁 Повторяю генерацию...", reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
    store.update_task(task.id, status="drafting", error="")
    await _generate_task_variants(
        bot,
        task,
        company,
        request=task.request,
        history=store.get_history_text(task.user_id),
        revision=None,
    )


# ---------------------------------------------------------------- reactions
def _reaction_emojis(reactions) -> list[str]:
    result = []
    for reaction in reactions:
        if isinstance(reaction, ReactionTypeEmoji):
            result.append(reaction.emoji)
        else:
            result.append(getattr(reaction, "type", "custom"))
    return result


@router.message_reaction()
async def on_reaction(update: MessageReactionUpdated) -> None:
    task_id = store.get_publication_by_message(update.chat.id, update.message_id)
    if not task_id:
        return
    user_id = update.user.id if update.user else 0
    emojis = _reaction_emojis(update.new_reaction)
    store.save_user_reactions(task_id, user_id, emojis)
    logger.info("Reaction task=%s user=%s emojis=%s", task_id, user_id, emojis)


@router.message_reaction_count()
async def on_reaction_count(update: MessageReactionCountUpdated) -> None:
    task_id = store.get_publication_by_message(update.chat.id, update.message_id)
    if not task_id:
        return
    counts: dict[str, int] = {}
    for item in update.reactions:
        reaction = getattr(item, "type", None)
        if isinstance(reaction, ReactionTypeEmoji):
            emoji = reaction.emoji
        else:
            emoji = getattr(reaction, "type", "custom")
        counts[emoji] = counts.get(emoji, 0) + int(getattr(item, "total_count", 0))
    store.save_reaction_counts(task_id, counts)
    logger.info("Reaction counts task=%s counts=%s", task_id, counts)


# ---------------------------------------------------------------- publishing
async def _publish(bot: Bot, task: Task, company: Company) -> Message:
    channel = _channel(company)
    if not channel:
        raise RuntimeError("Не задан Telegram-канал компании (TELEGRAM_CHANNEL_ID)")
    if not task.image_path:
        raise RuntimeError("У задачи нет изображения — публикация запрещена")
    text = fit_caption(task.post_text.strip(), limit=1000)
    html_text = format_post_html(text)
    if len(text) <= 1024:
        return await bot.send_photo(
            chat_id=channel,
            photo=FSImage(task.image_path),
            caption=html_text,
            parse_mode="HTML",
        )
    message = await bot.send_photo(
        chat_id=channel,
        photo=FSImage(task.image_path),
        caption=company.name,
    )
    await bot.send_message(chat_id=channel, text=html_text, parse_mode="HTML")
    return message
