"""Track scheduled deliveries."""

from alembic import op
import sqlalchemy as sa

revision = "81e5ac9b17d2"
down_revision = "95b2afe04e3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )
    op.create_index("ix_deliveries_expires_at", "deliveries", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_deliveries_expires_at", table_name="deliveries")
    op.drop_table("deliveries")
