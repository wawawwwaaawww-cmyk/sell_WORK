"""add_sales_script_support

Revision ID: 6ad3cb841d2a
Revises: 13068cd26c23
Create Date: 2025-10-15 12:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6ad3cb841d2a"
down_revision: Union[str, None] = "13068cd26c23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("sales_script_md", sa.Text(), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "sales_script_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "leads",
        sa.Column(
            "sales_script_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "leads",
        sa.Column("sales_script_model", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("sales_script_inputs_hash", sa.String(length=128), nullable=True),
    )

    op.create_table(
        "lead_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "lead_id",
            sa.BigInteger(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column(
            "payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_lead_events_lead_id_created_at",
        "lead_events",
        ["lead_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_lead_events_event_type",
        "lead_events",
        ["event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_lead_events_event_type", table_name="lead_events")
    op.drop_index("ix_lead_events_lead_id_created_at", table_name="lead_events")
    op.drop_table("lead_events")

    op.drop_column("leads", "sales_script_inputs_hash")
    op.drop_column("leads", "sales_script_model")
    op.drop_column("leads", "sales_script_generated_at")
    op.drop_column("leads", "sales_script_version")
    op.drop_column("leads", "sales_script_md")
