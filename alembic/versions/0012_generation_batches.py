"""Add generation batches.

Revision ID: 0012_generation_batches
Revises: 0011_generation_quality
Create Date: 2026-05-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_generation_batches"
down_revision: str | None = "0011_generation_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("quality_profile", sa.Text(), server_default="480p", nullable=False),
        sa.Column("total_jobs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_jobs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_jobs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_duration_seconds", sa.Numeric(12, 3), nullable=True),
        sa.Column("total_price_usd", sa.Numeric(12, 4), nullable=True),
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
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_generation_batches_user_id",
        "generation_batches",
        ["user_id"],
    )
    op.create_index(
        "ix_generation_batches_status",
        "generation_batches",
        ["status"],
    )
    op.add_column(
        "generation_jobs",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("batch_index", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_generation_jobs_batch_id_generation_batches",
        "generation_jobs",
        "generation_batches",
        ["batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_generation_jobs_batch_id",
        "generation_jobs",
        ["batch_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_batch_id", table_name="generation_jobs")
    op.drop_constraint(
        "fk_generation_jobs_batch_id_generation_batches",
        "generation_jobs",
        type_="foreignkey",
    )
    op.drop_column("generation_jobs", "batch_index")
    op.drop_column("generation_jobs", "batch_id")
    op.drop_index("ix_generation_batches_status", table_name="generation_batches")
    op.drop_index("ix_generation_batches_user_id", table_name="generation_batches")
    op.drop_table("generation_batches")
