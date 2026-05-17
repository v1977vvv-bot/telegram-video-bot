from __future__ import annotations

from pydantic import BaseModel


class OpsDependencyStatus(BaseModel):
    status: str
    error: str | None = None


class OpsStatusResponse(BaseModel):
    status: str
    service: str
    app_env: str
    version: str
    commit: str
    database: OpsDependencyStatus
    redis: OpsDependencyStatus
    worker_queue: str
    jobs: dict[str, int]
    runpod_pods: dict[str, int]
