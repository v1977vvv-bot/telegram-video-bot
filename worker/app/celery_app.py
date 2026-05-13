from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from shared.app.config import get_settings
from shared.app.logging import configure_logging
from worker.app.database import init_worker_database, shutdown_worker_database

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery(
    "telegram_video_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "worker.app.tasks.debug",
        "worker.app.tasks.generation",
    ],
)

celery_app.conf.update(
    accept_content=["json"],
    result_serializer="json",
    task_serializer="json",
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
)


@worker_process_init.connect
def on_worker_process_init(**_: object) -> None:
    init_worker_database()


@worker_process_shutdown.connect
def on_worker_process_shutdown(**_: object) -> None:
    shutdown_worker_database()
