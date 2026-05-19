"""Add generation waiting notification timestamp.

Revision ID: 0010_waiting_notify
Revises: 0009_admin_audit_logs
Create Date: 2026-05-19 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_waiting_notify"
down_revision: str | None = "0009_admin_audit_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE generation_jobs
        ADD COLUMN IF NOT EXISTS waiting_notification_sent_at TIMESTAMP WITH TIME ZONE
        """
    )
    op.execute(
        """
        UPDATE generation_jobs
        SET waiting_notification_sent_at = NOW()
        WHERE status IN ('waiting_for_gpu', 'waiting_for_pod')
          AND waiting_notification_sent_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE generation_jobs
        DROP COLUMN IF EXISTS waiting_notification_sent_at
        """
    )
