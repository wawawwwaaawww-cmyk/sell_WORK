"""add lead profile table

Revision ID: 22c07c66a3d4
Revises: 13068cd26c23
Create Date: 2025-10-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '22c07c66a3d4'
down_revision: Union[str, None] = '13068cd26c23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lead_profiles',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('scenario', sa.String(length=32), nullable=True),
        sa.Column('current_stage', sa.String(length=64), server_default=sa.text("'opening'"), nullable=False),
        sa.Column('summary_text', sa.Text(), nullable=True),
        sa.Column('profile_data', sa.JSON(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('readiness_score', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('client_label', sa.String(length=120), nullable=True),
        sa.Column('handoff_ready', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('handoff_trigger', sa.String(length=120), nullable=True),
        sa.Column('last_agent_notes', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('lead_profiles')
