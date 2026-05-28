from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.business_account import BusinessAccount
    from backend.app.models.business_balance_transaction import BusinessBalanceTransaction
    from backend.app.models.generation_batch import GenerationBatch
    from backend.app.models.generation_segment import GenerationSegment
    from backend.app.models.user import User


class GenerationJob(TimestampMixin, Base):
    __tablename__ = "generation_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_image_file_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
    )
    source_audio_file_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
    )
    output_file_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
    )
    fps: Mapped[int] = mapped_column(Integer, nullable=False, server_default="25")
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_profile: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="480p",
        server_default="480p",
    )
    audio_duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    segments_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    billing_account_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="personal",
        server_default="personal",
    )
    business_account_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("business_accounts.id", ondelete="SET NULL"),
    )
    business_hold_transaction_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("business_balance_transactions.id", ondelete="SET NULL"),
    )
    batch_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="SET NULL"),
        index=True,
    )
    batch_index: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    mock_result_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_for_gpu_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_for_pod_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_notification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship("User", back_populates="generation_jobs")
    business_account: Mapped[BusinessAccount | None] = relationship(
        "BusinessAccount",
        back_populates="generation_jobs",
        foreign_keys=[business_account_id],
    )
    business_hold_transaction: Mapped[BusinessBalanceTransaction | None] = relationship(
        "BusinessBalanceTransaction",
        foreign_keys=[business_hold_transaction_id],
    )
    batch: Mapped[GenerationBatch | None] = relationship(
        "GenerationBatch",
        back_populates="jobs",
    )
    segments: Mapped[list[GenerationSegment]] = relationship(
        "GenerationSegment",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="GenerationSegment.segment_index",
    )
