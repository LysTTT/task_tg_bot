"""Create users and terms versions

Revision ID: d1ffe554b897
Revises: 
Create Date: 2026-11-11 20:51:53.687342

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1ffe554b897'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "terms_versions",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("url_terms", sa.Text(), nullable=False),
        sa.Column("url_privacy", sa.Text(), nullable=False),
        sa.Column(
            "published_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "is_current", sa.Boolean(),
            server_default=sa.text("false"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    op.create_index(
        "uq_terms_versions_current", "terms_versions", ["is_current"],
        unique=True, postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), server_default="Europe/Moscow", nullable=False),
        sa.Column("morning_report_time", sa.Time(), nullable=True),
        sa.Column("evening_report_time", sa.Time(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("terms_version", sa.Text(), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_blocked", sa.Boolean(),
            server_default=sa.text("false"), nullable=False,
        ),
        sa.ForeignKeyConstraint(["terms_version"], ["terms_versions.version"]),
        sa.PrimaryKeyConstraint("telegram_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("users")
    op.drop_index("uq_terms_versions_current", table_name="terms_versions")
    op.drop_table("terms_versions")
