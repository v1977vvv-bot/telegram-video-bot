from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_balance_transactions"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "balance_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generation_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("balance_available_after", sa.Numeric(12, 4), nullable=False),
        sa.Column("balance_frozen_after", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["generation_job_id"],
            ["generation_jobs.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_balance_transactions_user_created_at",
        "balance_transactions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_balance_transactions_type_created_at",
        "balance_transactions",
        ["type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_balance_transactions_type_created_at", table_name="balance_transactions")
    op.drop_index("ix_balance_transactions_user_created_at", table_name="balance_transactions")
    op.drop_table("balance_transactions")
