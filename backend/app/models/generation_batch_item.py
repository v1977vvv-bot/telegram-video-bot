from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.generation_batch import GenerationBatch
    from backend.app.models.generation_job import GenerationJob
    from backend.app.models.uploaded_file import UploadedFile


class GenerationBatchItem(TimestampMixin, Base):
    __tablename__ = "generation_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "batch_index", name="uq_generation_batch_items_index"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    basename: Mapped[str] = mapped_column(Text, nullable=False)
    image_filename: Mapped[str] = mapped_column(Text, nullable=False)
    audio_filename: Mapped[str] = mapped_column(Text, nullable=False)
    source_image_file_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploaded_files.id"),
        nullable=False,
    )
    source_audio_file_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploaded_files.id"),
        nullable=False,
    )
    duration_seconds: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    generation_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="SET NULL"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="draft",
        server_default="draft",
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    batch: Mapped[GenerationBatch] = relationship("GenerationBatch", back_populates="items")
    generation_job: Mapped[GenerationJob | None] = relationship("GenerationJob")
    source_image_file: Mapped[UploadedFile] = relationship(
        "UploadedFile",
        foreign_keys=[source_image_file_id],
    )
    source_audio_file: Mapped[UploadedFile] = relationship(
        "UploadedFile",
        foreign_keys=[source_audio_file_id],
    )
