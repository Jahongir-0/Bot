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
    stars_payment_secret: str = Field(default="", alias="STARS_PAYMENT_SECRET")
    auto_schema_upgrade: bool = Field(default=False, alias="AUTO_SCHEMA_UPGRADE")
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE", ge=1, le=50)
    db_max_overflow: int = Field(default=10, alias="DB_MAX_OVERFLOW", ge=0, le=100)
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT", ge=5, le=120)

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

    def validate_runtime(self) -> None:
        """Fail fast on unsafe/missing production configuration."""
        if not self.bot_token:
            raise RuntimeError("BOT_TOKEN is not set. Configure it in .env or the deployment environment.")
        if not self.admin_ids and not self.admin_usernames:
            raise RuntimeError("ADMIN_IDS or ADMIN_USERNAMES must contain at least one administrator.")
        if self.enable_telegram_stars and len(self.stars_payment_secret) < 32:
            raise RuntimeError(
                "STARS_PAYMENT_SECRET must be at least 32 characters when Telegram Stars payments are enabled."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
