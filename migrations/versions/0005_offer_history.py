"""Preserve cancelled offers when their user-card row is removed.

Revision ID: 0005_offer_history
Revises: 0004_coin_idempotency
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_offer_history"
down_revision = "0004_coin_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("offers") as batch_op:
        batch_op.alter_column(
            "user_card_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    # Keep historical offers intact; do not make a nullable column non-nullable.
    pass
