from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from shared.app.config import Settings, get_settings
from shared.app.exceptions import AppError

security = HTTPBasic(auto_error=False)


class AdminPrincipal(str):
    """Authenticated admin identifier."""


def require_admin_enabled(settings: Settings | None = None) -> Settings:
    resolved_settings = settings or get_settings()
    if not resolved_settings.admin_panel_enabled:
        raise AppError("Admin panel is disabled", code="not_found", status_code=404)
    return resolved_settings


def require_admin_auth(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> AdminPrincipal:
    settings = require_admin_enabled()
    if not settings.admin_basic_auth_enabled:
        raise AppError("Admin auth is disabled", code="admin_auth_disabled", status_code=403)

    expected_username = settings.admin_basic_auth_username.strip()
    expected_password = settings.admin_basic_auth_password
    if not expected_username or not expected_password:
        raise AppError(
            "Admin credentials are not configured",
            code="admin_auth_unconfigured",
            status_code=403,
        )

    if credentials is None:
        raise _basic_challenge()

    username_ok = secrets.compare_digest(credentials.username, expected_username)
    password_ok = secrets.compare_digest(credentials.password, expected_password)
    if not username_ok or not password_ok:
        raise _basic_challenge()

    request.state.admin_identifier = credentials.username
    return AdminPrincipal(credentials.username)


def _basic_challenge() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )
