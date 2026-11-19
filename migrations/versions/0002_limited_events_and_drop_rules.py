"""Limitli eventlar, karta nusxa limitlari, guruh a'zolar soni, indekslar.

Revision ID: 0002_limited_events
Revises: 0001_initial
"""
from alembic import op

from database.schema_upgrade import upgrade_schema_sync

revision = "0002_limited_events"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Funksiya idempotent: init_db() allaqachon bajargan bo'lsa ham xavfsiz.
    upgrade_schema_sync(op.get_bind())


def downgrade() -> None:
    pass
