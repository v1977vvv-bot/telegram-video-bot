from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_generation_flow"
down_revision: str | None = "0002_balance_transactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("mock_result_message", sa.Text(), nullable=True),
    )

    op.create_table(
        "generation_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("audio_start_seconds", sa.Numeric(10, 3), nullable=False),
        sa.Column("audio_end_seconds", sa.Numeric(10, 3), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(10, 3), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("price_usd", sa.Numeric(12, 4), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 4), nullable=True),
        sa.Column("input_audio_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_image_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("output_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["input_audio_file_id"],
            ["uploaded_files.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["input_image_file_id"],
            ["uploaded_files.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["output_file_id"], ["uploaded_files.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_generation_segments_job_index",
        "generation_segments",
        ["job_id", "segment_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_segments_job_index", table_name="generation_segments")
    op.drop_table("generation_segments")
    op.drop_column("generation_jobs", "mock_result_message")
    op.drop_column("generation_jobs", "queued_at")
    op.drop_column("generation_jobs", "cancelled_at")
    op.drop_column("generation_jobs", "confirmed_at")
