"""Mavjud bazani yangi versiyaga avtomatik moslashtirish (alembic'siz ham, alembic bilan ham ishlaydi).

- yangi jadvallarni yaratadi (checkfirst),
- yetishmayotgan ustunlarni ALTER TABLE bilan qo'shadi,
- yangi ustunlarni boshlang'ich qiymat bilan to'ldiradi,
- foydali indekslarni yaratadi (bir user = bir xil kartadan bitta nusxa uchun UNIQUE indeks ham).
Funksiya idempotent: qayta-qayta chaqirilsa zarar qilmaydi.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _col_defs(dialect: str):
    bool_false = "FALSE" if dialect == "postgresql" else "0"
    dt = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
    return {
        "cards": [
            ("drop_limit", "INTEGER NOT NULL DEFAULT 1"),
            ("dropped_count", "INTEGER NOT NULL DEFAULT 0"),
            ("is_event_card", f"BOOLEAN NOT NULL DEFAULT {bool_false}"),
            ("limited_copies", "INTEGER"),
            ("image_file_id", "VARCHAR(255)"),
        ],
        "groups": [
            ("member_count", "INTEGER"),
            ("member_count_updated_at", dt),
            ("auto_drop_count", "BOOLEAN NOT NULL DEFAULT " + ("TRUE" if dialect == "postgresql" else "1")),
        ],
    }


# Rank bo'yicha boshlang'ich tashlash soni (yangi ustun qo'shilganda mavjud kartalar uchun)
_DEFAULT_DROPS = {"Common": 3, "Rare": 2, "Epic": 2, "Legendary": 1, "Mythic": 1}


def upgrade_schema_sync(conn: Connection) -> None:
    import models  # noqa: F401
    from database.session import Base

    Base.metadata.create_all(conn)  # yangi jadvallar (limited_events, event_broadcasts, event_purchases ...)

    dialect = conn.dialect.name
    insp = inspect(conn)
    added_drop_limit = False
    for table, cols in _col_defs(dialect).items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols:
            if name not in existing:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}'))
                logger.info("Schema upgrade: %s.%s qo'shildi", table, name)
                if table == "cards" and name == "drop_limit":
                    added_drop_limit = True

    if added_drop_limit:
        for rarity, drops in _DEFAULT_DROPS.items():
            conn.execute(text("UPDATE cards SET drop_limit = :d WHERE rarity = :r"), {"d": drops, "r": rarity})
        conn.execute(
            text(
                "UPDATE cards SET dropped_count = "
                "(SELECT COUNT(*) FROM active_drops WHERE active_drops.card_id = cards.id)"
            )
        )

    # Indekslar
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_active_drops_group_created ON active_drops (group_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_active_drops_card_created ON active_drops (card_id, created_at)",
    ):
        try:
            conn.execute(text(stmt))
        except Exception as exc:  # pragma: no cover
            logger.warning("Index yaratilmadi (%s): %s", stmt, exc)

    # Bir user bir xil kartadan faqat bitta nusxa: dublikat bo'lmasa UNIQUE indeks qo'yamiz
    try:
        dup = conn.execute(
            text("SELECT 1 FROM user_cards GROUP BY user_id, card_id HAVING COUNT(*) > 1 LIMIT 1")
        ).first()
        if dup is None:
            conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS uq_user_cards_user_card ON user_cards (user_id, card_id)")
            )
        else:
            logger.warning(
                "user_cards da dublikat nusxalar bor: UNIQUE indeks qo'yilmadi. "
                "Dublikatlarni tozalang (bir user = bir xil kartadan bitta)."
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("UNIQUE indeks tekshiruvida xato: %s", exc)
