from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from backend.app.models.runpod_pod import RunpodPod
from shared.app.enums import PodStatus
from worker.app.services.runpod import RunPodCapacityError, RunPodClient, RunPodPodInfo
from worker.app.services.runpod_discovery import RunPodDiscoveryService
from worker.app.services.runpod_manager import ASSIGNABLE_POD_STATUSES


class ManualRunPodModeTests(unittest.TestCase):
    def test_auto_create_disabled_blocks_create_pod_api(self) -> None:
        http_client = _FakeHttpClient()
        client = RunPodClient(
            _settings(runpod_auto_create_enabled=False),
            http_client=http_client,
        )

        with self.assertRaises(RunPodCapacityError):
            client.create_pod("NVIDIA L40S")

        self.assertEqual(http_client.post_calls, 0)

    def test_discovery_registers_starting_pod_when_healthcheck_fails(self) -> None:
        pod_info = _pod_info()
        session = _FakeSession()
        service = _DiscoveryService(
            _settings(runpod_discovery_register_starting=True),
            runpod_client=_FakeRunPodClient([pod_info]),
            healthcheck_result=False,
        )

        result = service.sync_active_pods(session)

        self.assertEqual(result.registered, 1)
        self.assertEqual(result.starting, 1)
        self.assertEqual(len(result.skipped), 0)
        self.assertEqual(session.added[0].status, PodStatus.STARTING.value)

    def test_starting_pod_is_not_assignable(self) -> None:
        self.assertNotIn(PodStatus.STARTING.value, ASSIGNABLE_POD_STATUSES)
        self.assertIn(PodStatus.IDLE.value, ASSIGNABLE_POD_STATUSES)
        self.assertIn(PodStatus.READY.value, ASSIGNABLE_POD_STATUSES)

    def test_starting_pod_becomes_idle_after_successful_healthcheck(self) -> None:
        now = datetime.now(UTC)
        pod = RunpodPod(
            provider_pod_id="pod-1",
            runpod_pod_id="pod-1",
            name="manual-pod",
            status=PodStatus.STARTING.value,
            cloud_type="SECURE",
            gpu_type="NVIDIA L40S",
            template_id="template-1",
            hourly_price_usd="0.80",
            base_url="https://pod-1-8188.proxy.runpod.net",
            comfyui_url="https://pod-1-8188.proxy.runpod.net",
            comfyui_port=8188,
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
        )
        session = _FakeSession(pods=[pod])
        service = _DiscoveryService(_settings(), healthcheck_result=True)

        result = service.check_starting_pods_health(session)

        self.assertEqual(result.checked, 1)
        self.assertEqual(result.healthy, 1)
        self.assertEqual(result.ready[0].pod_id, "pod-1")
        self.assertEqual(pod.status, PodStatus.IDLE.value)
        self.assertIsNotNone(pod.last_healthcheck_at)
        self.assertIsNone(pod.error_message)


class _DiscoveryService(RunPodDiscoveryService):
    def __init__(
        self,
        settings: SimpleNamespace,
        *,
        runpod_client: object | None = None,
        healthcheck_result: bool,
    ) -> None:
        super().__init__(settings, runpod_client=runpod_client)  # type: ignore[arg-type]
        self._healthcheck_result = healthcheck_result

    def _healthcheck(self, base_url: str) -> bool:
        return self._healthcheck_result


class _FakeRunPodClient:
    def __init__(self, pods: list[RunPodPodInfo]) -> None:
        self._pods = pods

    def list_pods(self) -> list[RunPodPodInfo]:
        return self._pods


class _FakeHttpClient:
    def __init__(self) -> None:
        self.post_calls = 0

    def post(self, *_args: object, **_kwargs: object) -> None:
        self.post_calls += 1


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, pods: list[RunpodPod] | None = None) -> None:
        self._pods = pods or []
        self.added: list[RunpodPod] = []
        self.commits = 0

    def scalar(self, _statement: object) -> object | None:
        return None

    def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self._pods)

    def add(self, pod: RunpodPod) -> None:
        self.added.append(pod)

    def commit(self) -> None:
        self.commits += 1


def _pod_info() -> RunPodPodInfo:
    return RunPodPodInfo(
        pod_id="pod-1",
        name="manual-pod",
        status="running",
        cloud_type="SECURE",
        gpu_type="NVIDIA L40S",
        template_id="template-1",
        ports=["8188/http"],
        hourly_price_usd="0.80",
        base_url="https://pod-1-8188.proxy.runpod.net",
        raw={},
    )


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        "runpod_auto_create_enabled": True,
        "runpod_discovery_enabled": True,
        "runpod_auto_manager_enabled": True,
        "runpod_discovery_auto_register": True,
        "runpod_discovery_require_healthy": True,
        "runpod_discovery_register_starting": True,
        "runpod_discovery_starting_healthcheck_enabled": True,
        "runpod_discovery_starting_healthcheck_timeout_minutes": 120,
        "runpod_allowed_gpu_type_list": ["NVIDIA L40S"],
        "runpod_fallback_allowed_gpu_type_list": [],
        "runpod_template_id": "template-1",
        "runpod_comfyui_port": 8188,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


if __name__ == "__main__":
    unittest.main()
