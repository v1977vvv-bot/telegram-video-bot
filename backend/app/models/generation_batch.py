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
    from backend.app.models.generation_batch_item import GenerationBatchItem
    from backend.app.models.generation_job import GenerationJob
    from backend.app.models.user import User


class GenerationBatch(TimestampMixin, Base):
    __tablename__ = "generation_batches"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    quality_profile: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="480p",
        server_default="480p",
    )
    total_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completed_jobs: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    failed_jobs: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    total_duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    total_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    error_message: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship("User", back_populates="generation_batches")
    jobs: Mapped[list[GenerationJob]] = relationship(
        "GenerationJob",
        back_populates="batch",
    )
    items: Mapped[list[GenerationBatchItem]] = relationship(
        "GenerationBatchItem",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="GenerationBatchItem.batch_index",
    )
