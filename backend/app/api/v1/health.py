from __future__ import annotations

from fastapi import APIRouter, Response

from backend.app.core.redis import ping_redis
from backend.app.schemas.health import HealthResponse
from shared.app.database import ping_database

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    database_status = "ok"
    redis_status = "ok"

    try:
        await ping_database()
    except Exception:
        database_status = "error"

    try:
        await ping_redis()
    except Exception:
        redis_status = "error"

    status = "ok" if database_status == "ok" and redis_status == "ok" else "degraded"
    if status == "degraded":
        response.status_code = 503

    return HealthResponse(
        status=status,
        service="backend",
        database=database_status,
        redis=redis_status,
    )
