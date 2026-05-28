"""Add generation video quality.

Revision ID: 0011_generation_quality
Revises: 0010_waiting_notify
Create Date: 2026-05-28 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_generation_quality"
down_revision: str | None = "0010_waiting_notify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE generation_jobs
        ADD COLUMN IF NOT EXISTS quality_profile TEXT NOT NULL DEFAULT '480p'
        """
    )
    op.execute(
        """
        UPDATE generation_jobs
        SET quality_profile = '480p'
        WHERE quality_profile IS NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_generation_jobs_quality_profile'
            ) THEN
                ALTER TABLE generation_jobs
                ADD CONSTRAINT ck_generation_jobs_quality_profile
                CHECK (quality_profile IN ('480p', '720p'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE generation_jobs
        DROP CONSTRAINT IF EXISTS ck_generation_jobs_quality_profile
        """
    )
    op.execute(
        """
        ALTER TABLE generation_jobs
        DROP COLUMN IF EXISTS quality_profile
        """
    )
