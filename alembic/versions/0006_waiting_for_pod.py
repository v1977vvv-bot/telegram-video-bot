"""Add waiting-for-pod retry field.

Revision ID: 0006_waiting_for_pod
Revises: 0005_waiting_for_gpu
Create Date: 2026-05-16 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_waiting_for_pod"
down_revision: str | None = "0005_waiting_for_gpu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("waiting_for_pod_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generation_jobs", "waiting_for_pod_since")
