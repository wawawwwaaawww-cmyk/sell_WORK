"""resolve multiple heads

Revision ID: b2325450231f
Revises: 44a251473885, 6ad3cb841d2a
Create Date: 2025-10-19 21:33:15.874477

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2325450231f'
down_revision: Union[str, None] = ('44a251473885', '6ad3cb841d2a')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass