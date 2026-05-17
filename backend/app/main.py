from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.v1.router import api_router
from backend.app.core.config_sanity import validate_startup_config
from backend.app.core.cors import parse_cors_origins
from backend.app.core.error_handlers import register_exception_handlers
from backend.app.core.redis import close_redis, ping_redis
from backend.app.schemas.health import HealthResponse
from shared.app.config import get_settings
from shared.app.database import dispose_engine, ping_database
from shared.app.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting backend service env=%s", settings.app_env)
    validate_startup_config(settings)
    await ping_database()
    await ping_redis()
    logger.info("Backend dependencies are reachable")
    try:
        yield
    finally:
        await close_redis()
        await dispose_engine()
        logger.info("Backend service stopped")


app = FastAPI(
    title="Telegram Video Avatar Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(settings),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def root_health() -> HealthResponse:
    return HealthResponse(status="ok", service="backend")


app.include_router(api_router, prefix="/api/v1")
