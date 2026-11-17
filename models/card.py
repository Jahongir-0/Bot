from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class Card(Base, TimestampMixin):
    __tablename__ = "cards"
    __table_args__ = (
        CheckConstraint("base_value >= 0", name="ck_cards_base_value_non_negative"),
        CheckConstraint("drop_weight >= 0", name="ck_cards_drop_weight_non_negative"),
        Index("ix_cards_character_name", "character_name"),
        Index("ix_cards_anime_name", "anime_name"),
        Index("ix_cards_rarity_avail", "rarity", "is_available", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    character_name: Mapped[str] = mapped_column(String(128), nullable=False)
    anime_name: Mapped[str] = mapped_column(String(128), nullable=False)
    anime_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    character_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    image: Mapped[str] = mapped_column(String(512), nullable=False)
    rarity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    base_value: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    drop_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    archived_note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Nusxa limiti: karta jami necha marta guruhlarga tashlanishi mumkin va necha marta tashlangan
    drop_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dropped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Event kartasi: guruhlarga tashlanmaydi, faqat event orqali olinadi (limited_copies = jami nusxa)
    is_event_card: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    limited_copies: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Telegram file_id keshi: URL bilan har safar qayta yuklamaslik uchun (tezroq yuboriladi)
    image_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
