"""A/B testing upgrade: assignments, events, enriched results.

Revision ID: f92f1775be1c
Revises: c89b97f1d6b7
Create Date: 2025-10-10 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f92f1775be1c"
down_revision: Union[str, None] = "c89b97f1d6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply A/B testing schema upgrades."""
    op.add_column("ab_tests", sa.Column("creator_user_id", sa.BigInteger(), nullable=True))
    op.add_column("ab_tests", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ab_tests", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "ab_tests",
        sa.Column("variants_count", sa.SmallInteger(), server_default="2", nullable=False),
    )
    op.add_column("ab_tests", sa.Column("audience_size", sa.Integer(), nullable=True))
    op.add_column("ab_tests", sa.Column("test_size", sa.Integer(), nullable=True))
    op.add_column("ab_tests", sa.Column("notification_job_id", sa.String(length=120), nullable=True))
    op.execute(sa.text("UPDATE ab_tests SET variants_count = 2 WHERE variants_count IS NULL"))
    op.execute(sa.text("UPDATE ab_tests SET started_at = created_at WHERE started_at IS NULL"))

    op.add_column("ab_variants", sa.Column("content", sa.JSON(), nullable=True))
    op.add_column(
        "ab_variants",
        sa.Column("order_index", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "ab_variants",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_ab_variant_code", "ab_variants", ["ab_test_id", "variant_code"]
    )

    op.create_table(
        "ab_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("test_id", sa.BigInteger(), nullable=False),
        sa.Column("variant_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("hash_value", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["test_id"], ["ab_tests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["ab_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("test_id", "user_id", name="uq_ab_assignment_user"),
    )
    op.create_index("ix_ab_assignments_test", "ab_assignments", ["test_id"])
    op.create_index("ix_ab_assignments_variant", "ab_assignments", ["variant_id"])

    op.create_table(
        "ab_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("test_id", sa.BigInteger(), nullable=False),
        sa.Column("variant_id", sa.BigInteger(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["assignment_id"], ["ab_assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_id"], ["ab_tests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["ab_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_ab_events_test_type",
        "ab_events",
        ["test_id", "event_type"],
    )
    op.create_index(
        "ix_ab_events_assignment_type",
        "ab_events",
        ["assignment_id", "event_type"],
    )

    op.add_column("ab_results", sa.Column("variant_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "ab_results",
        sa.Column("payment_started", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "ab_results",
        sa.Column("payment_confirmed", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("ab_results", sa.Column("blocked", sa.Integer(), server_default="0", nullable=False))
    op.add_column(
        "ab_results",
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_ab_result_variant", "ab_results", ["ab_test_id", "variant_code"]
    )
    op.create_foreign_key(
        "fk_ab_results_variant",
        "ab_results",
        "ab_variants",
        ["variant_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Normalize legacy status values
    op.execute(
        sa.text(
            "UPDATE ab_tests SET status = 'completed' WHERE status IN ('finished', 'FINISHED')"
        )
    )

    # Drop server defaults that should not persist
    op.alter_column("ab_tests", "variants_count", server_default=None)
    op.alter_column("ab_variants", "order_index", server_default=None)
    op.alter_column("ab_results", "payment_started", server_default=None)
    op.alter_column("ab_results", "payment_confirmed", server_default=None)
    op.alter_column("ab_results", "blocked", server_default=None)


def downgrade() -> None:
    """Revert A/B testing schema upgrades."""
    op.drop_constraint("fk_ab_results_variant", "ab_results", type_="foreignkey")
    op.drop_constraint("uq_ab_result_variant", "ab_results", type_="unique")
    op.drop_column("ab_results", "snapshot_at")
    op.drop_column("ab_results", "blocked")
    op.drop_column("ab_results", "payment_confirmed")
    op.drop_column("ab_results", "payment_started")
    op.drop_column("ab_results", "variant_id")

    op.drop_index("ix_ab_events_assignment_type", table_name="ab_events")
    op.drop_index("ix_ab_events_test_type", table_name="ab_events")
    op.drop_table("ab_events")

    op.drop_index("ix_ab_assignments_variant", table_name="ab_assignments")
    op.drop_index("ix_ab_assignments_test", table_name="ab_assignments")
    op.drop_table("ab_assignments")

    op.drop_constraint("uq_ab_variant_code", "ab_variants", type_="unique")
    op.drop_column("ab_variants", "created_at")
    op.drop_column("ab_variants", "order_index")
    op.drop_column("ab_variants", "content")

    op.drop_column("ab_tests", "notification_job_id")
    op.drop_column("ab_tests", "test_size")
    op.drop_column("ab_tests", "audience_size")
    op.drop_column("ab_tests", "variants_count")
    op.drop_column("ab_tests", "finished_at")
    op.drop_column("ab_tests", "started_at")
    op.drop_column("ab_tests", "creator_user_id")
