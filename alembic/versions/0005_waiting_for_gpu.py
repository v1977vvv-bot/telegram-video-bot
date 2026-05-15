"""Add waiting-for-GPU retry fields.

Revision ID: 0005_waiting_for_gpu
Revises: 0004_runpod_manager
Create Date: 2026-05-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_waiting_for_gpu"
down_revision: str | None = "0004_runpod_manager"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("waiting_for_gpu_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_generation_jobs_waiting_gpu_retry",
        "generation_jobs",
        ["status", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_waiting_gpu_retry", table_name="generation_jobs")
    op.drop_column("generation_jobs", "next_retry_at")
    op.drop_column("generation_jobs", "waiting_for_gpu_since")
