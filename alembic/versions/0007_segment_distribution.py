"""Add distributed segment metadata.

Revision ID: 0007_segment_distribution
Revises: 0006_waiting_for_pod
Create Date: 2026-05-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_segment_distribution"
down_revision: str | None = "0006_waiting_for_pod"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generation_segments", sa.Column("runpod_pod_id", sa.Text(), nullable=True))
    op.add_column("generation_segments", sa.Column("prompt_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("generation_segments", "prompt_id")
    op.drop_column("generation_segments", "runpod_pod_id")
