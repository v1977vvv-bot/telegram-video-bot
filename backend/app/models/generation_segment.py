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
    from backend.app.models.generation_job import GenerationJob


class GenerationSegment(TimestampMixin, Base):
    __tablename__ = "generation_segments"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    audio_start_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    audio_end_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    duration_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False)
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    input_audio_file_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
    )
    input_image_file_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
    )
    output_file_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
    )
    runpod_pod_id: Mapped[str | None] = mapped_column(Text)
    prompt_id: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[GenerationJob] = relationship("GenerationJob", back_populates="segments")
