from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, utcnow


class AdminRole(Base, TimestampMixin):
    __tablename__ = "admin_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    role_name: Mapped[str] = mapped_column(String(48), nullable=False, default="Moderator")
    is_super_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    added_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CardHistory(Base, TimestampMixin):
    __tablename__ = "card_history"
    __table_args__ = (
        Index("ix_card_history_card_created", "card_id", "created_at"),
        Index("ix_card_history_to_user", "to_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True)
    user_card_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    from_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    to_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    group_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    price: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ForcedChannel(Base, TimestampMixin):
    __tablename__ = "forced_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Telegram Channel")
    invite_link: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    channel_type: Mapped[str] = mapped_column(String(24), nullable=False, default="telegram")  # telegram|group|external
    sub_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="vip")  # daily|subscribers|vip
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    target_subscribers: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_subscribers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class Quiz(Base, TimestampMixin):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # scheduled|active|ended
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reward_coins: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    reward_xp: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    reward_card_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cards.id", ondelete="SET NULL"), nullable=True)
    max_winners: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    target_scope: Mapped[str] = mapped_column(String(24), nullable=False, default="all")  # all|group
    target_group_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_dispatched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class QuizQuestion(Base, TimestampMixin):
    __tablename__ = "quiz_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quiz_id: Mapped[int] = mapped_column(Integer, ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True)
    order_num: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    accepted_answers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    correct_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    image_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    time_limit_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)


class QuizAttempt(Base, TimestampMixin):
    __tablename__ = "quiz_attempts"
    __table_args__ = (
        UniqueConstraint("question_id", "user_id", name="uq_quiz_attempt_question_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quiz_id: Mapped[int] = mapped_column(Integer, ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("quiz_questions.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    selected_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GameSession(Base, TimestampMixin):
    __tablename__ = "game_sessions"
    __table_args__ = (
        Index("ix_game_sessions_user_date", "user_id", "created_date"),
        Index("uq_game_sessions_request_id", "request_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    game_type: Mapped[str] = mapped_column(String(24), nullable=False, default="dice")
    roll_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reward_coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reward_xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_date: Mapped[date] = mapped_column(Date, nullable=False)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed", server_default="completed")


class Auction(Base, TimestampMixin):
    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint("start_price > 0", name="ck_auctions_start_positive"),
        CheckConstraint("current_price >= start_price", name="ck_auctions_curr_ge_start"),
        Index("ix_auctions_status_ends", "status", "ends_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_card_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("user_cards.id", ondelete="SET NULL"), nullable=True)
    seller_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    start_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    min_step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=50)
    highest_bidder_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    bid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|ended|cancelled|no_bids
    finalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    channel_msg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    card_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)



class AuctionBid(Base, TimestampMixin):
    __tablename__ = "auction_bids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    auction_id: Mapped[int] = mapped_column(Integer, ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_refunded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Season(Base, TimestampMixin):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    rewards_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    finalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SeasonProgress(Base, TimestampMixin):
    __tablename__ = "season_progress"
    __table_args__ = (UniqueConstraint("season_id", "user_id", name="uq_season_progress_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(Integer, ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cards_claimed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trades_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quiz_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reward_claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Achievement(Base, TimestampMixin):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    icon: Mapped[str] = mapped_column(String(16), nullable=False, default="🏅")
    metric: Mapped[str] = mapped_column(String(48), nullable=False, default="total_cards")
    target_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reward_coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reward_xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reward_badge: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reward_title: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserAchievement(Base, TimestampMixin):
    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    achievement_id: Mapped[int] = mapped_column(Integer, ForeignKey("achievements.id", ondelete="CASCADE"), nullable=False)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AdCampaign(Base, TimestampMixin):
    __tablename__ = "ad_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    button_text: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    button_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    placement: Mapped[str] = mapped_column(String(24), nullable=False, default="all")
    frequency_mins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    views_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_shown_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class WishlistItem(Base, TimestampMixin):
    __tablename__ = "wishlist_items"
    __table_args__ = (UniqueConstraint("user_id", "card_id", name="uq_wishlist_user_card"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    card_id: Mapped[int] = mapped_column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True)


class UserNotification(Base, TimestampMixin):
    __tablename__ = "user_notifications"
    __table_args__ = (Index("ix_user_notifications_user_read", "user_id", "is_read"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    notif_type: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SupportTicket(Base, TimestampMixin):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False, default="support")  # support|report|appeal
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)  # open|answered|closed
    assigned_admin_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SupportMessage(Base, TimestampMixin):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # user|admin
    sender_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class SecurityIncident(Base, TimestampMixin):
    __tablename__ = "security_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    group_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)
    incident_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    score_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    action_taken: Mapped[str] = mapped_column(String(24), nullable=False, default="logged")
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class BackupRecord(Base, TimestampMixin):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    backup_type: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    tables_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)


class ScheduledMessage(Base, TimestampMixin):
    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    from_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    forward_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_scope: Mapped[str] = mapped_column(String(24), nullable=False, default="all")
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class CardTrade(Base, TimestampMixin):
    __tablename__ = "card_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiator_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    partner_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    initiator_uc_id: Mapped[int] = mapped_column(Integer, ForeignKey("user_cards.id", ondelete="CASCADE"), nullable=False)
    partner_uc_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("user_cards.id", ondelete="CASCADE"), nullable=True)
    initiator_coins: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    partner_coins: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_pick", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
