from __future__ import annotations

from ipaddress import ip_address

from fastapi import Request

from shared.app.config import Settings, get_settings
from shared.app.exceptions import AppError

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def require_debug_access(request: Request) -> None:
    """Protect all debug endpoints from accidental public exposure."""

    settings = get_settings()
    if not settings.debug_endpoints_enabled:
        raise AppError("Debug endpoints are disabled", code="not_found", status_code=404)

    if settings.app_env == "production" and not settings.debug_endpoints_local_only:
        raise AppError("Debug endpoints are disabled", code="not_found", status_code=404)

    if settings.debug_endpoints_local_only and not is_allowed_debug_client(request, settings):
        raise AppError("Debug endpoint is local-only", code="debug_forbidden", status_code=403)


def is_allowed_debug_client(request: Request, settings: Settings) -> bool:
    if not settings.debug_endpoints_local_only:
        return True

    client_host = request.client.host if request.client else ""
    if client_host in LOCAL_HOSTS:
        return True

    try:
        address = ip_address(client_host)
    except ValueError:
        return False

    return bool(address.is_loopback or address.is_private)
