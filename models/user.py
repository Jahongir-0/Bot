from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, utcnow


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("coins >= 0", name="ck_users_coins_non_negative"),
        CheckConstraint("total_cards >= 0", name="ck_users_total_cards_non_negative"),
        CheckConstraint("unique_cards >= 0", name="ck_users_unique_cards_non_negative"),
        Index("ix_users_username_lower", "username"),
        Index("ix_users_ranking_total", "total_cards", "unique_cards"),
        Index("ix_users_ranking_unique", "unique_cards", "total_cards"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="Collector")
    coins: Mapped[int] = mapped_column(BigInteger, nullable=False, default=100)
    locked_coins: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_cards: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_cards: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    daily_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_daily_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    referred_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    referral_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ban_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ban_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    vip_tier: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    vip_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    equipped_frame: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    equipped_title: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    equipped_badge: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    equipped_bg: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    suspicion_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quiz_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    game_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auction_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True,
    )

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.first_name or str(self.telegram_id)
