from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class LimitedEvent(Base, TimestampMixin):
    """Cheklangan nusxali karta eventi: guruhlarga tashlanmaydi, faqat event orqali coin/Stars evaziga olinadi."""

    __tablename__ = "limited_events"
    __table_args__ = (
        CheckConstraint("total_copies > 0", name="ck_limited_events_total_positive"),
        CheckConstraint("sold_count >= 0", name="ck_limited_events_sold_non_negative"),
        Index("ix_limited_events_status_ends", "status", "ends_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id", ondelete="RESTRICT"), nullable=False, index=True)
    total_copies: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_coins: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_stars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|sold_out|ended|cancelled
    created_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    finalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    @property
    def remaining(self) -> int:
        return max(0, int(self.total_copies) - int(self.sold_count))


class EventBroadcast(Base, TimestampMixin):
    """Event e'loni yuborilgan xabarlar (tugaganda tugmani olib tashlash uchun)."""

    __tablename__ = "event_broadcasts"
    __table_args__ = (Index("ix_event_broadcasts_event", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("limited_events.id", ondelete="CASCADE"), nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_photo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EventPurchase(Base, TimestampMixin):
    __tablename__ = "event_purchases"
    __table_args__ = (
        Index("ix_event_purchases_event_user", "event_id", "user_id"),
        Index("ix_event_purchases_charge", "telegram_payment_charge_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_uid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("limited_events.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)  # coins|stars
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|paid|refunded|failed
    telegram_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
