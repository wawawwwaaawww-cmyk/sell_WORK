"""merge ab testing heads

Revision ID: c8fd657a1dd4
Revises: c7f2e3d4a5b1, f92f1775be1c
Create Date: 2025-10-12 22:07:15.235847

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8fd657a1dd4'
down_revision: Union[str, None] = ('c7f2e3d4a5b1', 'f92f1775be1c')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass