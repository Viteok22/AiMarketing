from __future__ import annotations


def is_message_not_modified(exc: BaseException) -> bool:
    """Telegram отвечает так, если edit_text получил тот же текст и клавиатуру."""

    return "message is not modified" in str(exc).lower()
