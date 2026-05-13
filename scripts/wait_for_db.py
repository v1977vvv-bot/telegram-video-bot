from __future__ import annotations

import asyncio
import logging
import time

import asyncpg

from shared.app.config import get_settings
from shared.app.logging import configure_logging

logger = logging.getLogger(__name__)


async def wait_for_db(timeout_seconds: int = 60) -> None:
    settings = get_settings()
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            connection = await asyncpg.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password,
            )
            await connection.execute("SELECT 1")
            await connection.close()
            logger.info("PostgreSQL is reachable")
            return
        except Exception as exc:
            last_error = exc
            logger.info("Waiting for PostgreSQL: %s", exc)
            await asyncio.sleep(2)

    raise RuntimeError("PostgreSQL was not reachable before timeout") from last_error


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(wait_for_db())


if __name__ == "__main__":
    main()
