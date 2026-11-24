from datetime import date
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class CoinTransaction(Base, TimestampMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("balance_after >= 0", name="ck_transactions_balance_non_negative"),
        Index("ix_transactions_user_created", "user_id", "created_at"),
        Index("ix_transactions_type_created", "tx_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_type: Mapped[str] = mapped_column(String(48), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    reference_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class DailyReward(Base, TimestampMixin):
    __tablename__ = "daily_rewards"
    __table_args__ = (
        UniqueConstraint("user_id", "reward_date", name="uq_daily_rewards_user_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reward_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    streak_day: Mapped[int] = mapped_column(Integer, nullable=False)
    coins_awarded: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StarPayment(Base, TimestampMixin):
    __tablename__ = "star_payments"
    __table_args__ = (
        CheckConstraint("stars_amount > 0", name="ck_star_payments_stars_positive"),
        CheckConstraint("coins_amount > 0", name="ck_star_payments_coins_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    package_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stars_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    coins_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    telegram_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    provider_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
