from __future__ import annotations

import re
from datetime import datetime, timedelta


class PublishTimeError(ValueError):
    """Пользователь ввёл дату или время в неподдерживаемом формате."""


_RELATIVE_UNIT_RE = re.compile(
    r"(\d+)\s*(мин(?:ут[а-я]*)?|ч(?:ас[а-я]*)?|д(?:ень|ня|ней))"
)
_DATE_TIME_RE = re.compile(
    r"^(?:(?P<day>\d{1,2})\.(?P<month>\d{1,2})(?:\.(?P<year>\d{2,4}))?"
    r"|(?P<iso_year>\d{4})-(?P<iso_month>\d{1,2})-(?P<iso_day>\d{1,2}))"
    r"\s+(?P<hour>\d{1,2})[:.](?P<minute>\d{2})$"
)
_CLOCK_RE = re.compile(r"^(?P<hour>\d{1,2})[:.](?P<minute>\d{2})$")
_DAY_WORDS = (("сегодня", 0), ("завтра", 1), ("послезавтра", 2))

_EXAMPLES = (
    "Например: «15.11 18:30», «15.11.2026 18:30», "
    "«2026-11-15 18:30», «завтра 10:00» или «через 2 часа»."
)


def parse_custom_publish_time(raw: str, now: datetime | None = None) -> datetime:
    """Разбирает пользовательское время публикации и возвращает aware datetime."""

    text = " ".join((raw or "").strip().lower().replace("ё", "е").split())
    text = text.replace(" в ", " ").strip()
    if not text:
        raise PublishTimeError(_EXAMPLES)

    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.astimezone()

    scheduled = _parse_relative(text, now)
    if scheduled is None:
        scheduled = _parse_named_day(text, now)
    if scheduled is None:
        scheduled = _parse_absolute(text, now)
    if scheduled is None:
        raise PublishTimeError(_EXAMPLES)

    if scheduled <= now:
        raise PublishTimeError(
            "Это время уже прошло. Укажите будущее время или добавьте год."
        )
    return scheduled


def _parse_relative(text: str, now: datetime) -> datetime | None:
    if not text.startswith("через "):
        return None
    rest = text[len("через ") :].strip()
    matches = list(_RELATIVE_UNIT_RE.finditer(rest))
    if not matches:
        raise PublishTimeError(_EXAMPLES)

    delta = timedelta()
    for match in matches:
        value = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("мин"):
            delta += timedelta(minutes=value)
        elif unit.startswith("ч"):
            delta += timedelta(hours=value)
        else:
            delta += timedelta(days=value)

    leftover = re.sub(r"[\s,и]+", "", _RELATIVE_UNIT_RE.sub("", rest))
    if leftover:
        raise PublishTimeError(_EXAMPLES)
    if delta <= timedelta():
        raise PublishTimeError("Время должно быть в будущем.")
    return now + delta


def _parse_named_day(text: str, now: datetime) -> datetime | None:
    for word, offset in _DAY_WORDS:
        prefix = word + " "
        if not text.startswith(prefix):
            continue
        hour, minute = _parse_clock(text[len(prefix) :])
        base = now + timedelta(days=offset)
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return None


def _parse_absolute(text: str, now: datetime) -> datetime | None:
    match = _DATE_TIME_RE.fullmatch(text)
    if match is None:
        return None

    if match.group("day"):
        day = int(match.group("day"))
        month = int(match.group("month"))
        year_raw = match.group("year")
        year = _parse_year(year_raw, now.year) if year_raw else now.year
    else:
        day = int(match.group("iso_day"))
        month = int(match.group("iso_month"))
        year = int(match.group("iso_year"))

    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise PublishTimeError("Некорректное время. Часы — 00–23, минуты — 00–59.")

    try:
        return datetime(year, month, day, hour, minute, tzinfo=now.tzinfo)
    except ValueError as exc:
        raise PublishTimeError("Некорректная дата или время.") from exc


def _parse_year(value: str, current_year: int) -> int:
    year = int(value)
    if len(value) == 2:
        return current_year - current_year % 100 + year
    return year


def _parse_clock(value: str) -> tuple[int, int]:
    match = _CLOCK_RE.fullmatch(value.strip())
    if match is None:
        raise PublishTimeError(_EXAMPLES)
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise PublishTimeError("Некорректное время. Часы — 00–23, минуты — 00–59.")
    return hour, minute
