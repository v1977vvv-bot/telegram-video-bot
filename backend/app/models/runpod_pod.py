from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin


class RunpodPod(TimestampMixin, Base):
    __tablename__ = "runpod_pods"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_pod_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    runpod_pod_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cloud_type: Mapped[str | None] = mapped_column(Text)
    gpu_type: Mapped[str | None] = mapped_column(Text)
    template_id: Mapped[str | None] = mapped_column(Text)
    hourly_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    base_url: Mapped[str | None] = mapped_column(Text)
    comfyui_url: Mapped[str | None] = mapped_column(Text)
    comfyui_port: Mapped[int | None] = mapped_column(Integer)
    active_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="SET NULL"),
    )
    current_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="SET NULL"),
    )
    last_healthcheck_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_busy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
