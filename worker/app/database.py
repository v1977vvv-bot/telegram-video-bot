from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from shared.app.config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def init_worker_database() -> None:
    """Create a sync DB engine inside the Celery child process."""

    global _engine, _session_factory
    if _engine is not None and _session_factory is not None:
        return

    settings = get_settings()
    _engine = create_engine(
        settings.sync_database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        future=True,
    )
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    logger.info("Worker sync database engine initialized")


def shutdown_worker_database() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        logger.info("Worker sync database engine disposed")
    _engine = None
    _session_factory = None


@contextmanager
def get_worker_session() -> Iterator[Session]:
    if _engine is None or _session_factory is None:
        init_worker_database()
    if _session_factory is None:
        raise RuntimeError("Worker session factory is not initialized")

    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
