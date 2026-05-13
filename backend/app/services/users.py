from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.user import User
from backend.app.repositories.balances import BalanceRepository
from backend.app.repositories.users import TelegramUserData, UserRepository
from shared.app.exceptions import AppError


@dataclass(frozen=True, slots=True)
class UserWithBalance:
    id: UUID
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    is_banned: bool
    available_usd: Decimal
    frozen_usd: Decimal


class UserService:
    def __init__(
        self,
        session: AsyncSession,
        user_repository: UserRepository | None = None,
        balance_repository: BalanceRepository | None = None,
    ) -> None:
        self._session = session
        self._user_repository = user_repository or UserRepository()
        self._balance_repository = balance_repository or BalanceRepository()

    async def upsert_telegram_user(self, data: TelegramUserData) -> UserWithBalance:
        async with self._session.begin():
            user_id = await self._user_repository.upsert_telegram_user(self._session, data)
            user = await self._get_user_or_raise(user_id)
            account = await self._balance_repository.get_account(self._session, user.id)
            if account is None:
                await self._balance_repository.create_account_if_missing(self._session, user.id)
                account = await self._balance_repository.get_account(self._session, user.id)
            if account is None:
                raise AppError("Balance account was not created", code="balance_account_missing")
            return UserWithBalance(
                id=user.id,
                telegram_id=user.telegram_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
                is_banned=user.is_banned,
                available_usd=account.available_usd,
                frozen_usd=account.frozen_usd,
            )

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self._user_repository.get_by_telegram_id(self._session, telegram_id)

    async def _get_user_or_raise(self, user_id: UUID) -> User:
        user = await self._user_repository.get_by_id(self._session, user_id)
        if user is None:
            raise AppError("User not found after upsert", code="user_not_found", status_code=404)
        return user
