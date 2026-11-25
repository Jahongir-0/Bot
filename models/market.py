from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class CardListing(Base, TimestampMixin):
    __tablename__ = "card_listings"
    __table_args__ = (
        CheckConstraint("price > 0", name="ck_card_listings_price_positive"),
        Index("ix_card_listings_status_created", "status", "created_at"),
        Index("ix_card_listings_user_card_status", "user_card_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seller_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    buyer_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    sold_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Offer(Base, TimestampMixin):
    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_offers_amount_non_negative"),
        Index("ix_offers_owner_status", "owner_id", "status"),
        Index("ix_offers_expires_status", "expires_at", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    buyer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    offer_type: Mapped[str] = mapped_column(String(16), nullable=False, default="coin", server_default="coin")
    offered_user_card_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("user_cards.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PendingTransfer(Base, TimestampMixin):
    __tablename__ = "pending_transfers"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_pending_transfers_price_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    price: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
