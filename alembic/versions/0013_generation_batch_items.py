"""Add generation batch items.

Revision ID: 0013_generation_batch_items
Revises: 0012_generation_batches
Create Date: 2026-05-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_generation_batch_items"
down_revision: str | None = "0012_generation_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_batch_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("basename", sa.Text(), nullable=False),
        sa.Column("image_filename", sa.Text(), nullable=False),
        sa.Column("audio_filename", sa.Text(), nullable=False),
        sa.Column("source_image_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_audio_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(12, 3), nullable=False),
        sa.Column("price_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("generation_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generation_job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_audio_file_id"], ["uploaded_files.id"]),
        sa.ForeignKeyConstraint(["source_image_file_id"], ["uploaded_files.id"]),
        sa.UniqueConstraint("batch_id", "batch_index", name="uq_generation_batch_items_index"),
    )
    op.create_index(
        "ix_generation_batch_items_batch_id",
        "generation_batch_items",
        ["batch_id"],
    )
    op.create_index(
        "ix_generation_batch_items_generation_job_id",
        "generation_batch_items",
        ["generation_job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_batch_items_generation_job_id", table_name="generation_batch_items")
    op.drop_index("ix_generation_batch_items_batch_id", table_name="generation_batch_items")
    op.drop_table("generation_batch_items")
