from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.balance_account import BalanceAccount
from backend.app.models.user import User


@dataclass(frozen=True, slots=True)
class TelegramUserData:
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None


class UserRepository:
    async def upsert_telegram_user(
        self,
        session: AsyncSession,
        data: TelegramUserData,
    ) -> UUID:
        user_stmt = (
            insert(User)
            .values(
                telegram_id=data.telegram_id,
                username=data.username,
                first_name=data.first_name,
                last_name=data.last_name,
                language_code=data.language_code,
            )
            .on_conflict_do_update(
                index_elements=[User.telegram_id],
                set_={
                    "username": data.username,
                    "first_name": data.first_name,
                    "last_name": data.last_name,
                    "language_code": data.language_code,
                    "updated_at": func.now(),
                },
            )
            .returning(User.id)
        )
        user_id = (await session.execute(user_stmt)).scalar_one()

        account_stmt = (
            insert(BalanceAccount)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=[BalanceAccount.user_id])
        )
        await session.execute(account_stmt)
        return user_id

    async def get_by_id(self, session: AsyncSession, user_id: UUID) -> User | None:
        return await session.get(User, user_id)

    async def get_by_telegram_id(
        self,
        session: AsyncSession,
        telegram_id: int,
    ) -> User | None:
        result = await session.execute(self._by_telegram_stmt(telegram_id))
        return result.scalar_one_or_none()

    def _by_telegram_stmt(self, telegram_id: int) -> Select[tuple[User]]:
        return select(User).where(User.telegram_id == telegram_id)
