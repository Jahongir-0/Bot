from functools import lru_cache
from pathlib import Path
from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BOT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_BOT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")
    admin_usernames_raw: str = Field(default="", alias="ADMIN_USERNAMES")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./anime_cards.db",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="", alias="REDIS_URL")
    timezone: str = Field(default="Asia/Tashkent", alias="TIMEZONE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    default_offer_expiry_hours: int = Field(default=24, alias="DEFAULT_OFFER_EXPIRY_HOURS")
    enable_telegram_stars: bool = Field(default=True, alias="ENABLE_TELEGRAM_STARS")
    stars_payment_secret: str = Field(
        default="dev_secret_change_in_prod",
        alias="STARS_PAYMENT_SECRET",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if not value:
            return "sqlite+aiosqlite:///./anime_cards.db"
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://") and "+asyncpg" not in value:
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("sqlite:///") and "+aiosqlite" not in value:
            return value.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return value

    @property
    def admin_ids(self) -> List[int]:
        ids: List[int] = []
        for item in self.admin_ids_raw.split(","):
            item = item.strip()
            if item.isdigit():
                ids.append(int(item))
        return ids

    @property
    def admin_usernames(self) -> List[str]:
        names: List[str] = []
        for item in self.admin_usernames_raw.split(","):
            cleaned = item.strip().lstrip("@").lower()
            if cleaned:
                names.append(cleaned)
        return names

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
