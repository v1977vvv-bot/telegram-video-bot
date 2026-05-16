from __future__ import annotations

from celery.result import AsyncResult

from worker.app.tasks.debug import debug_ping
from worker.app.tasks.generation import process_generation_job, retry_waiting_for_gpu_jobs
from worker.app.tasks.runpod_keeper import runpod_keeper_tick


def enqueue_debug_ping(message: str = "pong") -> AsyncResult:
    return debug_ping.delay(message)


def enqueue_generation_job(job_id: str) -> AsyncResult:
    return process_generation_job.delay(job_id)


def enqueue_retry_waiting_for_gpu_jobs() -> AsyncResult:
    return retry_waiting_for_gpu_jobs.delay()


def enqueue_runpod_keeper_tick() -> AsyncResult:
    return runpod_keeper_tick.delay()
