from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class DropSchedule(Base, TimestampMixin):
    __tablename__ = "drop_schedules"
    __table_args__ = (
        Index("ix_drop_schedules_group_date", "group_id", "schedule_date"),
        Index("ix_drop_schedules_due", "status", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    schedule_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    card_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cards.id", ondelete="SET NULL"), nullable=True)
    is_priority_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ActiveDrop(Base, TimestampMixin):
    __tablename__ = "active_drops"
    __table_args__ = (
        Index("ix_active_drops_group_claimed", "group_id", "is_claimed"),
        Index("ix_active_drops_group_created", "group_id", "created_at"),
        Index("ix_active_drops_card_created", "card_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id", ondelete="RESTRICT"), nullable=False)
    schedule_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("drop_schedules.id", ondelete="SET NULL"), nullable=True)
    is_claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    claimed_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reward_coins: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
