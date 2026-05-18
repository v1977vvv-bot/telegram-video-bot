from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.balance_account import BalanceAccount
    from backend.app.models.balance_transaction import BalanceTransaction
    from backend.app.models.business_account_member import BusinessAccountMember
    from backend.app.models.generation_job import GenerationJob
    from backend.app.models.payment import Payment
    from backend.app.models.uploaded_file import UploadedFile


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    language_code: Mapped[str | None] = mapped_column(Text)
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    balance_account: Mapped[BalanceAccount | None] = relationship(
        "BalanceAccount",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    generation_jobs: Mapped[list[GenerationJob]] = relationship(
        "GenerationJob",
        back_populates="user",
    )
    uploaded_files: Mapped[list[UploadedFile]] = relationship(
        "UploadedFile",
        back_populates="user",
    )
    payments: Mapped[list[Payment]] = relationship("Payment", back_populates="user")
    balance_transactions: Mapped[list[BalanceTransaction]] = relationship(
        "BalanceTransaction",
        back_populates="user",
    )
    business_memberships: Mapped[list[BusinessAccountMember]] = relationship(
        "BusinessAccountMember",
        back_populates="user",
        cascade="all, delete-orphan",
    )
