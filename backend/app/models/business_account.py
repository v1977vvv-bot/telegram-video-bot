from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.business_account_member import BusinessAccountMember
    from backend.app.models.generation_job import GenerationJob


class BusinessAccount(TimestampMixin, Base):
    __tablename__ = "business_accounts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
        server_default="active",
    )
    available_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False,
        default=Decimal("0.0000"),
        server_default="0",
    )
    frozen_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False,
        default=Decimal("0.0000"),
        server_default="0",
    )

    members: Mapped[list[BusinessAccountMember]] = relationship(
        "BusinessAccountMember",
        back_populates="business_account",
        cascade="all, delete-orphan",
    )
    generation_jobs: Mapped[list[GenerationJob]] = relationship(
        "GenerationJob",
        back_populates="business_account",
    )
