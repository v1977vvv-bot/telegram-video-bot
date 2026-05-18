"""Add business balance accounts.

Revision ID: 0008_business_accounts
Revises: 0007_segment_distribution
Create Date: 2026-05-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_business_accounts"
down_revision: str | None = "0007_segment_distribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("available_usd", sa.Numeric(12, 4), server_default="0", nullable=False),
        sa.Column("frozen_usd", sa.Numeric(12, 4), server_default="0", nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_business_accounts_status", "business_accounts", ["status"])

    op.create_table(
        "business_account_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_account_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), server_default="member", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["business_account_id"],
            ["business_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_account_id",
            "user_id",
            name="uq_business_account_members_account_user",
        ),
    )
    op.create_index(
        "ix_business_account_members_business_account_id",
        "business_account_members",
        ["business_account_id"],
    )
    op.create_index("ix_business_account_members_user_id", "business_account_members", ["user_id"])

    op.create_table(
        "business_balance_transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_account_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("generation_job_id", sa.UUID(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("balance_available_after", sa.Numeric(12, 4), nullable=False),
        sa.Column("balance_frozen_after", sa.Numeric(12, 4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_account_id"],
            ["business_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["generation_job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_business_balance_transactions_business_account_id",
        "business_balance_transactions",
        ["business_account_id"],
    )
    op.create_index(
        "ix_business_balance_transactions_user_id",
        "business_balance_transactions",
        ["user_id"],
    )
    op.create_index(
        "ix_business_balance_transactions_generation_job_id",
        "business_balance_transactions",
        ["generation_job_id"],
    )
    op.create_index(
        "ix_business_balance_transactions_created_at",
        "business_balance_transactions",
        ["created_at"],
    )

    op.add_column(
        "generation_jobs",
        sa.Column("billing_account_type", sa.Text(), server_default="personal", nullable=False),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("business_account_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("business_hold_transaction_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_generation_jobs_business_account_id",
        "generation_jobs",
        "business_accounts",
        ["business_account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_generation_jobs_business_hold_transaction_id",
        "generation_jobs",
        "business_balance_transactions",
        ["business_hold_transaction_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_generation_jobs_billing_account_type",
        "generation_jobs",
        ["billing_account_type"],
    )
    op.create_index(
        "ix_generation_jobs_business_account_id",
        "generation_jobs",
        ["business_account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_business_account_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_billing_account_type", table_name="generation_jobs")
    op.drop_constraint(
        "fk_generation_jobs_business_hold_transaction_id",
        "generation_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_generation_jobs_business_account_id",
        "generation_jobs",
        type_="foreignkey",
    )
    op.drop_column("generation_jobs", "business_hold_transaction_id")
    op.drop_column("generation_jobs", "business_account_id")
    op.drop_column("generation_jobs", "billing_account_type")

    op.drop_index(
        "ix_business_balance_transactions_created_at",
        table_name="business_balance_transactions",
    )
    op.drop_index(
        "ix_business_balance_transactions_generation_job_id",
        table_name="business_balance_transactions",
    )
    op.drop_index(
        "ix_business_balance_transactions_user_id",
        table_name="business_balance_transactions",
    )
    op.drop_index(
        "ix_business_balance_transactions_business_account_id",
        table_name="business_balance_transactions",
    )
    op.drop_table("business_balance_transactions")

    op.drop_index("ix_business_account_members_user_id", table_name="business_account_members")
    op.drop_index(
        "ix_business_account_members_business_account_id",
        table_name="business_account_members",
    )
    op.drop_table("business_account_members")

    op.drop_index("ix_business_accounts_status", table_name="business_accounts")
    op.drop_table("business_accounts")
