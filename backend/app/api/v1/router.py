from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.v1.debug import router as debug_router
from backend.app.api.v1.files import router as files_router
from backend.app.api.v1.generation import router as generation_router
from backend.app.api.v1.health import router as health_router
from backend.app.api.v1.ops import router as ops_router
from backend.app.api.v1.payments import router as payments_router
from backend.app.api.v1.settings import router as settings_router
from backend.app.api.v1.telegram import router as telegram_router
from backend.app.api.v1.users import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(ops_router)
api_router.include_router(settings_router)
api_router.include_router(payments_router)
api_router.include_router(debug_router)
api_router.include_router(telegram_router)
api_router.include_router(users_router)
api_router.include_router(generation_router)
api_router.include_router(files_router)
