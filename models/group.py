from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, utcnow


class Group(Base, TimestampMixin):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Anime Group")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    daily_drop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    drop_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="uz")
    coin_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    allowed_features: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {
            "drops": True,
            "market": True,
            "search": True,
            "daily": True,
            "ranking": True,
            "transfer": True,
        },
    )
    admin_permissions: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {"group_admins_can_view_schedule": False},
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class GroupMember(Base, TimestampMixin):
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
        Index("ix_group_members_group_last_seen", "group_id", "last_seen"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
