from __future__ import annotations

import logging

from worker.app.celery_app import celery_app
from worker.app.services.runpod_keeper import RunPodKeeper
from worker.app.tasks.generation import retry_waiting_for_gpu_jobs

logger = logging.getLogger(__name__)


@celery_app.task(name="runpod_keeper_tick")
def runpod_keeper_tick() -> dict[str, object]:
    """Maintain a warm RunPod pod and shut down expired idle pods."""

    logger.info("runpod_keeper_tick started")
    result = RunPodKeeper().tick()
    payload = result.as_dict()
    if result.should_enqueue_waiting_retry:
        try:
            retry_waiting_for_gpu_jobs.delay()
            payload["requeued_waiting_jobs"] = 0
        except Exception:
            logger.warning("RunPod keeper could not enqueue waiting GPU retry")
    logger.info("runpod_keeper_tick completed result=%s", payload)
    return payload
