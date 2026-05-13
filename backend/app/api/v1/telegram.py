from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.repositories.users import TelegramUserData
from backend.app.schemas.users import (
    BalanceResponse,
    TelegramUserResponse,
    TelegramUserUpsertRequest,
)
from backend.app.services.users import UserService
from shared.app.database import get_session

router = APIRouter(prefix="/telegram", tags=["telegram"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/users/upsert", response_model=TelegramUserResponse)
async def upsert_telegram_user(
    payload: TelegramUserUpsertRequest,
    session: SessionDep,
) -> TelegramUserResponse:
    user = await UserService(session).upsert_telegram_user(
        TelegramUserData(
            telegram_id=payload.telegram_id,
            username=payload.username,
            first_name=payload.first_name,
            last_name=payload.last_name,
            language_code=payload.language_code,
        )
    )
    return TelegramUserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        is_banned=user.is_banned,
        balance=BalanceResponse(
            available_usd=user.available_usd,
            frozen_usd=user.frozen_usd,
        ),
    )
