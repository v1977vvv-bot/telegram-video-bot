"""Add RunPod manager fields.

Revision ID: 0004_runpod_manager
Revises: 0003_generation_flow
Create Date: 2026-05-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_runpod_manager"
down_revision: str | None = "0003_generation_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runpod_pods", sa.Column("runpod_pod_id", sa.Text(), nullable=True))
    op.add_column("runpod_pods", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("runpod_pods", sa.Column("cloud_type", sa.Text(), nullable=True))
    op.add_column("runpod_pods", sa.Column("template_id", sa.Text(), nullable=True))
    op.add_column("runpod_pods", sa.Column("base_url", sa.Text(), nullable=True))
    op.add_column("runpod_pods", sa.Column("comfyui_port", sa.Integer(), nullable=True))
    op.add_column("runpod_pods", sa.Column("active_job_id", sa.UUID(), nullable=True))
    op.add_column(
        "runpod_pods", sa.Column("last_healthcheck_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "runpod_pods", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("runpod_pods", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column(
        "runpod_pods", sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.execute("UPDATE runpod_pods SET runpod_pod_id = provider_pod_id WHERE runpod_pod_id IS NULL")
    op.execute("UPDATE runpod_pods SET base_url = comfyui_url WHERE base_url IS NULL")
    op.execute("UPDATE runpod_pods SET comfyui_port = 8188 WHERE comfyui_port IS NULL")
    op.execute("UPDATE runpod_pods SET active_job_id = current_job_id WHERE active_job_id IS NULL")
    op.execute(
        "UPDATE runpod_pods SET last_healthcheck_at = last_heartbeat_at "
        "WHERE last_healthcheck_at IS NULL"
    )
    op.execute("UPDATE runpod_pods SET last_used_at = last_busy_at WHERE last_used_at IS NULL")

    op.alter_column("runpod_pods", "runpod_pod_id", existing_type=sa.Text(), nullable=False)
    op.create_unique_constraint("uq_runpod_pods_runpod_pod_id", "runpod_pods", ["runpod_pod_id"])
    op.create_foreign_key(
        "fk_runpod_pods_active_job_id_generation_jobs",
        "runpod_pods",
        "generation_jobs",
        ["active_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_runpod_pods_active_job_id_generation_jobs", "runpod_pods", type_="foreignkey"
    )
    op.drop_constraint("uq_runpod_pods_runpod_pod_id", "runpod_pods", type_="unique")
    op.drop_column("runpod_pods", "terminated_at")
    op.drop_column("runpod_pods", "error_message")
    op.drop_column("runpod_pods", "last_used_at")
    op.drop_column("runpod_pods", "last_healthcheck_at")
    op.drop_column("runpod_pods", "active_job_id")
    op.drop_column("runpod_pods", "comfyui_port")
    op.drop_column("runpod_pods", "base_url")
    op.drop_column("runpod_pods", "template_id")
    op.drop_column("runpod_pods", "cloud_type")
    op.drop_column("runpod_pods", "name")
    op.drop_column("runpod_pods", "runpod_pod_id")
