from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (BASE_DIR / path).resolve()


def relative_path(value: str | Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(BASE_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_channel_id: str = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    telegram_proxy: str = os.getenv("TELEGRAM_PROXY", "").strip()
    admin_telegram_id: int = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or 0)
    admin_password: str = os.getenv("ADMIN_PASSWORD", "admin")
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "dev-secret-change-me")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    chatgpt_profile_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("CHATGPT_PROFILE_DIR", str(BASE_DIR / "data" / "chrome-profile"))
        ).resolve()
    )
    chatgpt_headless: bool = _env_bool("CHATGPT_HEADLESS", False)
    chatgpt_url: str = os.getenv("CHATGPT_URL", "https://chatgpt.com/")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip()
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
    anymodel_base_url: str = os.getenv("ANYMODEL_BASE_URL", "https://anymodel.org/v1").strip()
    anymodel_api_key: str = os.getenv("ANYMODEL_API_KEY", "").strip()
    anymodel_image_model: str = os.getenv("ANYMODEL_IMAGE_MODEL", "gpt-image-2.5").strip()
    anymodel_image_quality: str = os.getenv("ANYMODEL_IMAGE_QUALITY", "low").strip()
    database_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "app.db"))
        ).resolve()
    )
    images_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "images")

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN не задан — Telegram-бот не запустится")
        if not self.telegram_channel_id:
            problems.append("TELEGRAM_CHANNEL_ID не задан — публикация в канал недоступна")
        if not self.deepseek_api_key:
            problems.append("DEEPSEEK_API_KEY не задан — генерация контента недоступна")
        if not self.anymodel_api_key:
            problems.append("ANYMODEL_API_KEY не задан — генерация изображений недоступна")
        if self.admin_password in {"admin", "change-me", ""}:
            problems.append("ADMIN_PASSWORD оставлен по умолчанию")
        return problems


settings = Settings()
settings.images_dir.mkdir(parents=True, exist_ok=True)
settings.database_path.parent.mkdir(parents=True, exist_ok=True)
