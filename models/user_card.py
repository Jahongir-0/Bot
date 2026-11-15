from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class UserCard(Base, TimestampMixin):
    __tablename__ = "user_cards"
    __table_args__ = (
        Index("ix_user_cards_user_card", "user_id", "card_id"),
        Index("ix_user_cards_card_locked", "card_id", "is_locked"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id", ondelete="RESTRICT"), nullable=False, index=True)
    acquired_via: Mapped[str] = mapped_column(String(32), nullable=False, default="drop")
    acquired_group_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("groups.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
