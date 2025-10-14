"""Sentiment counters, audit table, and settings store.

Revision ID: 43ec2d9a8765
Revises: f92f1775be1c
Create Date: 2025-02-14 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "43ec2d9a8765"
down_revision: Union[str, None] = "f92f1775be1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply sentiment tracking schema changes."""
    op.add_column("users", sa.Column("counter", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("pos_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("neu_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("neg_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("scored_total", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("lead_level_percent", sa.Integer(), nullable=True))
    op.add_column(
        "users",
        sa.Column("lead_level_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_message_scores",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("hash", sa.String(length=128), nullable=False),
        sa.UniqueConstraint("hash", name="uq_user_message_scores_hash"),
        sa.UniqueConstraint("user_id", "message_id", name="uq_user_message_scores_message"),
    )
    op.create_index(
        "ix_user_message_scores_user_id",
        "user_message_scores",
        ["user_id"],
    )
    op.create_index(
        "ix_user_message_scores_evaluated_at",
        "user_message_scores",
        ["evaluated_at"],
    )

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.alter_column("users", "counter", server_default=None)
    op.alter_column("users", "pos_count", server_default=None)
    op.alter_column("users", "neu_count", server_default=None)
    op.alter_column("users", "neg_count", server_default=None)
    op.alter_column("users", "scored_total", server_default=None)
    op.alter_column("user_message_scores", "model", server_default=None)
    op.alter_column("user_message_scores", "confidence", server_default=None)


def downgrade() -> None:
    """Revert sentiment tracking schema changes."""
    op.drop_table("system_settings")

    op.drop_index("ix_user_message_scores_evaluated_at", table_name="user_message_scores")
    op.drop_index("ix_user_message_scores_user_id", table_name="user_message_scores")
    op.drop_table("user_message_scores")

    op.drop_column("users", "lead_level_updated_at")
    op.drop_column("users", "lead_level_percent")
    op.drop_column("users", "scored_total")
    op.drop_column("users", "neg_count")
    op.drop_column("users", "neu_count")
    op.drop_column("users", "pos_count")
    op.drop_column("users", "counter")
