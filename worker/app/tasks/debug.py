from __future__ import annotations

import logging

from worker.app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="debug_ping")
def debug_ping(message: str = "pong") -> dict[str, str]:
    logger.info("debug_ping received message=%s", message)
    return {"status": "ok", "message": message}
