"""merge divergent branches

Revision ID: 3eab965596e0
Revises: 0f3b27ac12de, 43ec2d9a8765, c8fd657a1dd4
Create Date: 2025-10-14 15:38:48.599507

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3eab965596e0'
down_revision: Union[str, None] = ('0f3b27ac12de', '43ec2d9a8765', 'c8fd657a1dd4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass