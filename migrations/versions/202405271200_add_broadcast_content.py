"""Add content column to broadcasts for rich media."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7f2e3d4a5b1"
down_revision: Union[str, None] = "c89b97f1d6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add JSON column for storing broadcast content items."""
    op.add_column("broadcasts", sa.Column("content", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove broadcast content column."""
    op.drop_column("broadcasts", "content")
