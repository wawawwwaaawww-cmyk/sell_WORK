"""Add product criteria tables and matching log."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0f3b27ac12de"
down_revision: Union[str, None] = "f92f1775be1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply product matching schema updates."""
    op.add_column("products", sa.Column("slug", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("short_desc", sa.String(length=500), nullable=True))
    op.add_column(
        "products",
        sa.Column("currency", sa.String(length=10), server_default="RUB", nullable=False),
    )
    op.add_column("products", sa.Column("value_props", sa.JSON(), nullable=True))
    op.add_column("products", sa.Column("landing_url", sa.String(length=500), nullable=True))
    op.add_column(
        "products",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_products_slug", "products", ["slug"])

    # Initialize new columns for existing rows
    op.execute(sa.text("UPDATE products SET short_desc = LEFT(description, 240) WHERE short_desc IS NULL"))
    op.execute(sa.text("UPDATE products SET landing_url = payment_landing_url WHERE landing_url IS NULL"))
    op.execute(sa.text("UPDATE products SET value_props = '[]'::jsonb WHERE value_props IS NULL"))
    op.execute(sa.text("UPDATE products SET currency = 'RUB' WHERE currency IS NULL"))
    op.execute(sa.text("UPDATE products SET updated_at = now() WHERE updated_at IS NULL"))

    op.create_table(
        "product_criteria",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("answer_id", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Integer(), server_default="1", nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("question_code", sa.String(length=50), nullable=True),
        sa.Column("answer_code", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("product_id", "question_id", "answer_id", name="uq_product_criteria_unique_answer"),
    )
    op.create_index(
        "ix_product_criteria_product_question",
        "product_criteria",
        ["product_id", "question_id"],
    )

    op.create_table(
        "product_match_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("top3", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "matched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("threshold_used", sa.Float(), nullable=True),
        sa.Column("trigger", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_product_match_log_user", "product_match_log", ["user_id"])
    op.create_index("ix_product_match_log_created", "product_match_log", ["matched_at"])


def downgrade() -> None:
    """Revert product matching schema updates."""
    op.drop_index("ix_product_match_log_created", table_name="product_match_log")
    op.drop_index("ix_product_match_log_user", table_name="product_match_log")
    op.drop_table("product_match_log")

    op.drop_index("ix_product_criteria_product_question", table_name="product_criteria")
    op.drop_table("product_criteria")

    op.drop_constraint("uq_products_slug", "products", type_="unique")
    op.drop_column("products", "updated_at")
    op.drop_column("products", "created_at")
    op.drop_column("products", "landing_url")
    op.drop_column("products", "value_props")
    op.drop_column("products", "currency")
    op.drop_column("products", "short_desc")
    op.drop_column("products", "slug")
