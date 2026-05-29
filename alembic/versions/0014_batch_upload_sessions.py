"""Add batch upload sessions.

Revision ID: 0014_batch_upload_sessions
Revises: 0013_generation_batch_items
Create Date: 2026-05-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_batch_upload_sessions"
down_revision: str | None = "0013_generation_batch_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "batch_upload_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("quality_profile", sa.Text(), nullable=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_batch_upload_sessions_token_hash"),
    )
    op.create_index("ix_batch_upload_sessions_user_id", "batch_upload_sessions", ["user_id"])
    op.create_index("ix_batch_upload_sessions_status", "batch_upload_sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_batch_upload_sessions_status", table_name="batch_upload_sessions")
    op.drop_index("ix_batch_upload_sessions_user_id", table_name="batch_upload_sessions")
    op.drop_table("batch_upload_sessions")
