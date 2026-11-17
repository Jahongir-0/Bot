"""V2 Professional Upgrade: yangi jadvallar, ustunlar, seriallar, RBAC, quiz, auction, season, backup.

Revision ID: 0003_v2_upgrade
Revises: 0002_limited_events
"""
from alembic import op

from database.schema_upgrade import upgrade_schema_sync

revision = "0003_v2_upgrade"
down_revision = "0002_limited_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_schema_sync(op.get_bind())


def downgrade() -> None:
    pass
