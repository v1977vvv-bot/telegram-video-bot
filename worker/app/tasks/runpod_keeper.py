from __future__ import annotations

import logging

from shared.app.config import get_settings
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


@celery_app.task(name="check_starting_runpod_pods")
def check_starting_runpod_pods() -> dict[str, object]:
    logger.info("check_starting_runpod_pods started")
    settings = get_settings()
    if not settings.runpod_discovery_starting_healthcheck_enabled:
        return {"status": "disabled"}

    with get_worker_session() as session:
        result = RunPodDiscoveryService(settings).check_starting_pods_health(session)

    alert_service = TelegramAdminAlertService(settings)
    auto_retry = settings.runpod_discovery_auto_retry_waiting_on_healthy
    for pod in result.ready:
        alert_service.send_pod_ready_alert(
            pod_id=pod.pod_id,
            gpu_type=pod.gpu_type,
            base_url=pod.base_url,
            waiting_jobs=pod.waiting_jobs,
            auto_retry=auto_retry,
        )
    for pod in result.failed:
        alert_service.send_starting_pod_timeout_alert(
            pod_id=pod.pod_id,
            gpu_type=pod.gpu_type,
            base_url=pod.base_url,
            age_minutes=pod.age_minutes,
            timeout_minutes=settings.runpod_discovery_starting_healthcheck_timeout_minutes,
        )

    payload = {"status": "ok", "starting_healthcheck": result.as_dict()}
    if result.ready and auto_retry:
        try:
            retry_waiting_generation_jobs.delay()
            payload["auto_retry_enqueued"] = True
        except Exception:
            logger.warning("RunPod starting healthcheck could not enqueue waiting retry")
            payload["auto_retry_enqueued"] = False

    logger.info("check_starting_runpod_pods completed result=%s", payload)
    return payload


def _sync_runpod_pods() -> dict[str, object]:
    with get_worker_session() as session:
        result = RunPodDiscoveryService().sync_active_pods(session)
        TelegramAdminAlertService().send_queue_pressure_alert_if_needed(session)
        return {"status": "ok", "discovery": result.as_dict()}
