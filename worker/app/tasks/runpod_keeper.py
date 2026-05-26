from __future__ import annotations

import logging

from worker.app.celery_app import celery_app
from worker.app.database import get_worker_session
from worker.app.services.admin_alerts import TelegramAdminAlertService
from worker.app.services.runpod_discovery import RunPodDiscoveryService
from worker.app.services.runpod_keeper import RunPodKeeper
from worker.app.tasks.generation import retry_waiting_generation_jobs

logger = logging.getLogger(__name__)


@celery_app.task(name="runpod_keeper_tick")
def runpod_keeper_tick() -> dict[str, object]:
    """Maintain a warm RunPod pod and shut down expired idle pods."""

    logger.info("runpod_keeper_tick started")
    discovery_payload: dict[str, object] | None = None
    try:
        discovery_payload = _sync_runpod_pods()["discovery"]
    except Exception:
        logger.warning("RunPod keeper discovery sync failed")
    result = RunPodKeeper().tick()
    payload = result.as_dict()
    payload["discovery"] = discovery_payload
    if result.should_enqueue_waiting_retry:
        try:
            retry_waiting_generation_jobs.delay()
            payload["requeued_waiting_jobs"] = 0
        except Exception:
            logger.warning("RunPod keeper could not enqueue waiting GPU retry")
    logger.info("runpod_keeper_tick completed result=%s", payload)
    return payload


@celery_app.task(name="sync_runpod_pods")
def sync_runpod_pods() -> dict[str, object]:
    logger.info("sync_runpod_pods started")
    payload = _sync_runpod_pods()
    logger.info("sync_runpod_pods completed result=%s", payload)
    return payload


def _sync_runpod_pods() -> dict[str, object]:
    with get_worker_session() as session:
        result = RunPodDiscoveryService().sync_active_pods(session)
        TelegramAdminAlertService().send_queue_pressure_alert_if_needed(session)
        return {"status": "ok", "discovery": result.as_dict()}
