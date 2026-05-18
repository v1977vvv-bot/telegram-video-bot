from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.schemas.generations import (
    GenerationHistoryItemResponse,
    GenerationHistoryResponse,
)
from backend.app.schemas.statistics import (
    BusinessBalanceResponse,
    GenerationStatisticsResponse,
    SpendingStatisticsResponse,
    UserStatisticsResponse,
)
from backend.app.schemas.users import BalanceResponse
from backend.app.services.generation_history import GenerationHistoryService
from backend.app.services.statistics import UserStatisticsService
from shared.app.database import get_session

router = APIRouter(prefix="/users", tags=["users"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LimitQuery = Annotated[int, Query(ge=1, le=50)]


@router.get(
    "/by-telegram/{telegram_id}/statistics",
    response_model=UserStatisticsResponse,
)
async def get_user_statistics(
    telegram_id: int,
    session: SessionDep,
) -> UserStatisticsResponse:
    statistics = await UserStatisticsService(session).get_by_telegram_id(telegram_id)
    return UserStatisticsResponse(
        telegram_id=statistics.telegram_id,
        balance=BalanceResponse(
            available_usd=statistics.balance.available_usd,
            frozen_usd=statistics.balance.frozen_usd,
        ),
        business_account=(
            BusinessBalanceResponse(
                id=statistics.business_account.id,
                name=statistics.business_account.name,
                available_usd=statistics.business_account.available_usd,
                frozen_usd=statistics.business_account.frozen_usd,
            )
            if statistics.business_account is not None
            else None
        ),
        generations=GenerationStatisticsResponse(
            today=statistics.generations.today,
            month=statistics.generations.month,
            all_time=statistics.generations.all_time,
            completed_all_time=statistics.generations.completed_all_time,
            failed_all_time=statistics.generations.failed_all_time,
        ),
        spending=SpendingStatisticsResponse(
            today_usd=statistics.spending.today_usd,
            month_usd=statistics.spending.month_usd,
            all_time_usd=statistics.spending.all_time_usd,
        ),
    )


@router.get(
    "/by-telegram/{telegram_id}/generations",
    response_model=GenerationHistoryResponse,
)
async def get_user_generations(
    telegram_id: int,
    session: SessionDep,
    limit: LimitQuery = 10,
) -> GenerationHistoryResponse:
    items = await GenerationHistoryService(session).get_latest_by_telegram_id(
        telegram_id=telegram_id,
        limit=limit,
    )
    return GenerationHistoryResponse(
        items=[
            GenerationHistoryItemResponse(
                id=item.id,
                status=item.status,
                width=item.width,
                height=item.height,
                fps=item.fps,
                audio_duration_seconds=item.audio_duration_seconds,
                segments_count=item.segments_count,
                price_usd=item.price_usd,
                error_message=item.error_message,
                mock_result_message=item.mock_result_message,
                result_file_id=item.result_file_id,
                result_url=item.result_url,
                result_url_expires_in_seconds=item.result_url_expires_in_seconds,
                created_at=item.created_at,
            )
            for item in items
        ]
    )
