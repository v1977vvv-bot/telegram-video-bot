from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.admin_audit_log import AdminAuditLog
from shared.app.logging import get_logger

logger = get_logger(__name__)


class AdminAuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        *,
        admin_identifier: str,
        action: str,
        request: Request | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        entry = AdminAuditLog(
            admin_identifier=admin_identifier,
            action=action,
            target_type=target_type,
            target_id=target_id,
            request_path=request.url.path if request is not None else None,
            request_method=request.method if request is not None else None,
            ip_address=request.client.host if request is not None and request.client else None,
            user_agent=request.headers.get("user-agent") if request is not None else None,
            audit_metadata=metadata,
        )
        self._session.add(entry)

    async def log_best_effort(
        self,
        *,
        admin_identifier: str,
        action: str,
        request: Request | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self.log(
                admin_identifier=admin_identifier,
                action=action,
                request=request,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata,
            )
        except Exception:
            logger.warning("Admin audit log write failed action=%s", action, exc_info=True)
