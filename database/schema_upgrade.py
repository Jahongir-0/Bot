"""Mavjud bazani V2 versiyaga ma'lumot yo'qotmasdan avtomatik moslashtirish (idempotent).

- yangi V2 jadvallarni yaratadi (checkfirst),
- mavjud jadvallarga yetishmayotgan ustunlarni ALTER TABLE bilan qo'shadi,
- eski yozuvlarni xavfsiz backfill qiladi (serial_number, media_type, card_state),
- Hech qachon DROP TABLE / DROP COLUMN qilmaydi.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _col_defs(dialect: str):
    bool_false = "FALSE" if dialect == "postgresql" else "0"
    bool_true = "TRUE" if dialect == "postgresql" else "1"
    dt = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
    json_empty_obj = "'{}'" if dialect == "postgresql" else "'{}'"
    json_empty_arr = "'[]'" if dialect == "postgresql" else "'[]'"
    return {
        "cards": [
            ("drop_limit", "INTEGER NOT NULL DEFAULT 1"),
            ("dropped_count", "INTEGER NOT NULL DEFAULT 0"),
            ("is_event_card", f"BOOLEAN NOT NULL DEFAULT {bool_false}"),
            ("limited_copies", "INTEGER"),
            ("image_file_id", "VARCHAR(255)"),
            ("media_type", "VARCHAR(24) NOT NULL DEFAULT 'Anime'"),
            ("aliases", f"JSON NOT NULL DEFAULT {json_empty_arr}"),
            ("card_state", "VARCHAR(24) NOT NULL DEFAULT 'AVAILABLE'"),
            ("metadata_json", f"JSON NOT NULL DEFAULT {json_empty_obj}"),
            ("battle_stats", f"JSON NOT NULL DEFAULT {json_empty_obj}"),
            ("season_id", "INTEGER"),
        ],
        "groups": [
            ("member_count", "INTEGER"),
            ("member_count_updated_at", dt),
            ("auto_drop_count", f"BOOLEAN NOT NULL DEFAULT {bool_true}"),
            ("add_reward_claimed", f"BOOLEAN NOT NULL DEFAULT {bool_false}"),
            ("added_by_user_id", "INTEGER"),
            ("is_banned", f"BOOLEAN NOT NULL DEFAULT {bool_false}"),
            ("ban_until", dt),
            ("ban_reason", "VARCHAR(500)"),
        ],
        "users": [
            ("referred_by_id", "INTEGER"),
            ("referral_count", "INTEGER NOT NULL DEFAULT 0"),
            ("locked_coins", "BIGINT NOT NULL DEFAULT 0"),
            ("is_frozen", f"BOOLEAN NOT NULL DEFAULT {bool_false}"),
            ("ban_until", dt),
            ("ban_reason", "VARCHAR(500)"),
            ("vip_tier", "VARCHAR(32)"),
            ("vip_until", dt),
            ("equipped_frame", "VARCHAR(64)"),
            ("equipped_title", "VARCHAR(64)"),
            ("equipped_badge", "VARCHAR(64)"),
            ("equipped_bg", "VARCHAR(64)"),
            ("suspicion_score", "INTEGER NOT NULL DEFAULT 0"),
            ("quiz_wins", "INTEGER NOT NULL DEFAULT 0"),
            ("game_wins", "INTEGER NOT NULL DEFAULT 0"),
            ("trade_count", "INTEGER NOT NULL DEFAULT 0"),
            ("auction_wins", "INTEGER NOT NULL DEFAULT 0"),
        ],
        "user_cards": [
            ("serial_number", "INTEGER"),
            ("is_showcased", f"BOOLEAN NOT NULL DEFAULT {bool_false}"),
            ("card_state", "VARCHAR(24) NOT NULL DEFAULT 'OWNED'"),
        ],
        "admin_actions": [
            ("old_value", f"JSON NOT NULL DEFAULT {json_empty_obj}"),
            ("new_value", f"JSON NOT NULL DEFAULT {json_empty_obj}"),
        ],
        "bans": [
            ("group_id", "INTEGER"),
            ("is_permanent", f"BOOLEAN NOT NULL DEFAULT {bool_true}"),
            ("ban_until", dt),
        ],
        "limited_events": [
            ("reward_coins", "INTEGER NOT NULL DEFAULT 0"),
            ("target_scope", "VARCHAR(24) NOT NULL DEFAULT 'all'"),
        ],
    }


_DEFAULT_DROPS = {"Common": 3, "Rare": 2, "Epic": 2, "Legendary": 1, "Mythic": 1, "Divine": 1}


def upgrade_schema_sync(conn: Connection) -> None:
    import models  # noqa: F401
    from database.session import Base

    Base.metadata.create_all(conn)

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

    # PostgreSQL'da bans.user_id guruh banlari uchun NULL bo'la olishi kerak
    if dialect == "postgresql" and insp.has_table("bans"):
        try:
            for col in insp.get_columns("bans"):
                if col["name"] == "user_id" and not col.get("nullable", True):
                    conn.execute(text("ALTER TABLE bans ALTER COLUMN user_id DROP NOT NULL"))
                    logger.info("Schema upgrade: bans.user_id nullable qilindi")
                    break
        except Exception as exc:  # pragma: no cover
            logger.warning("bans.user_id alter xato: %s", exc)

    # Backfill serial_number, media_type, card_state (idempotent)
    try:
        conn.execute(text("UPDATE user_cards SET serial_number = 10000 + id WHERE serial_number IS NULL"))
        conn.execute(text("UPDATE cards SET media_type = 'Anime' WHERE media_type IS NULL OR media_type = ''"))
        conn.execute(
            text(
                "UPDATE cards SET card_state = CASE "
                "WHEN is_deleted = TRUE THEN 'DELETED' "
                "WHEN is_event_card = TRUE THEN 'EVENT' "
                "ELSE 'AVAILABLE' END "
                "WHERE card_state IS NULL OR card_state = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE user_cards SET card_state = CASE "
                "WHEN is_locked = TRUE THEN 'LOCKED' ELSE 'OWNED' END "
                "WHERE card_state IS NULL OR card_state = ''"
            )
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("Backfill xato (e'tibor berilmadi): %s", exc)

    # Indekslar
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_active_drops_group_created ON active_drops (group_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_active_drops_card_created ON active_drops (card_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_cards_media_state ON cards (media_type, card_state)",
        "CREATE INDEX IF NOT EXISTS ix_user_cards_serial ON user_cards (serial_number)",
    ):
        try:
            conn.execute(text(stmt))
        except Exception as exc:  # pragma: no cover
            logger.warning("Index yaratilmadi (%s): %s", stmt, exc)

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
                "user_cards da dublikat nusxalar bor: UNIQUE indeks qo'yilmadi."
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("UNIQUE indeks tekshiruvida xato: %s", exc)
