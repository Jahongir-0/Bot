"""Bot 2.5 Upgrade: Bot Do'koni, Card Suggestions, Offer Card Swap, Acquired Prices, Group bot_status, Auction Anti-Snipe.

Revision ID: 0004_v25_upgrade
Revises: 0003_v2_upgrade
"""
from alembic import op

from database.schema_upgrade import upgrade_schema_sync

revision = "0004_v25_upgrade"
down_revision = "0003_v2_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_schema_sync(op.get_bind())


def downgrade() -> None:
    pass
