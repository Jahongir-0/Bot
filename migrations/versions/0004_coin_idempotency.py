"""Add non-destructive idempotency fields for coin and game requests.

Revision ID: 0004_coin_idempotency
Revises: 0003_v2_upgrade
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_coin_idempotency"
down_revision = "0003_v2_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    transaction_columns = {column["name"] for column in inspector.get_columns("transactions")}
    if "idempotency_key" not in transaction_columns:
        op.add_column("transactions", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    transaction_indexes = {index["name"] for index in inspector.get_indexes("transactions")}
    if "uq_transactions_idempotency_key" not in transaction_indexes:
        op.create_index(
            "uq_transactions_idempotency_key",
            "transactions",
            ["idempotency_key"],
            unique=True,
        )

    game_session_columns = {column["name"] for column in inspector.get_columns("game_sessions")}
    if "request_id" not in game_session_columns:
        op.add_column("game_sessions", sa.Column("request_id", sa.String(length=64), nullable=True))
    if "status" not in game_session_columns:
        op.add_column(
            "game_sessions",
            sa.Column("status", sa.String(length=16), server_default="completed", nullable=False),
        )
    game_session_indexes = {index["name"] for index in inspector.get_indexes("game_sessions")}
    if "uq_game_sessions_request_id" not in game_session_indexes:
        op.create_index(
            "uq_game_sessions_request_id",
            "game_sessions",
            ["request_id"],
            unique=True,
        )


def downgrade() -> None:
    # Keep idempotency data intact; rollback is intentionally non-destructive.
    pass
