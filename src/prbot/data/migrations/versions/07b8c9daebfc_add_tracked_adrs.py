"""add tracked_adrs

Revision ID: 07b8c9daebfc
Revises: f6a7b8c9daeb
Create Date: 2026-11-12 00:00:00.000000

ADR review posts and the reactions the bot has put on them. Kept apart from
``tracked_prs``: they are keyed by Notion page, not by PR.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "07b8c9daebfc"
down_revision: str | Sequence[str] | None = "f6a7b8c9daeb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracked_adrs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("page_id", sa.String(), nullable=False),
        sa.Column("integration_id", sa.String(), nullable=False),
        sa.Column("message_ref", sa.String(), nullable=False),
        sa.Column("applied_emojis", sa.Text(), server_default="", nullable=False),
        sa.Column("scope_keys", sa.Text(), server_default="", nullable=False),
        sa.Column("last_edited_time", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.String(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.String(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("page_id", "integration_id", "message_ref"),
    )


def downgrade() -> None:
    op.drop_table("tracked_adrs")
