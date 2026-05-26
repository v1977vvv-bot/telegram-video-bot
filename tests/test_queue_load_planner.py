from __future__ import annotations

import unittest
from decimal import Decimal

from worker.app.services.queue_load_planner import calculate_queue_load_plan


class QueueLoadPlannerTests(unittest.TestCase):
    def test_four_waiting_minutes_on_one_healthy_pod_needs_no_extra_pod(self) -> None:
        plan = _plan(waiting_minutes=Decimal("4"), healthy_pods=1, active_pods=1)

        self.assertEqual(plan.recommended_additional_pods, 0)
        self.assertFalse(plan.should_alert)

    def test_seven_waiting_minutes_on_one_healthy_pod_recommends_one_extra_pod(self) -> None:
        plan = _plan(waiting_minutes=Decimal("7"), healthy_pods=1, active_pods=1)

        self.assertEqual(plan.recommended_total_pods, 2)
        self.assertEqual(plan.recommended_additional_pods, 1)
        self.assertTrue(plan.should_alert)

    def test_eighteen_waiting_minutes_on_two_healthy_pods_recommends_one_extra_pod(
        self,
    ) -> None:
        plan = _plan(waiting_minutes=Decimal("18"), healthy_pods=2, active_pods=2)

        self.assertEqual(plan.recommended_total_pods, 3)
        self.assertEqual(plan.recommended_additional_pods, 1)

    def test_recommendation_is_capped_by_max_active_pods(self) -> None:
        plan = _plan(
            waiting_minutes=Decimal("30"),
            healthy_pods=1,
            active_pods=1,
            max_active_pods=3,
        )

        self.assertEqual(plan.recommended_total_pods, 5)
        self.assertEqual(plan.recommended_additional_pods, 2)

    def test_small_waiting_queue_with_no_healthy_pods_recommends_but_does_not_alert(self) -> None:
        plan = _plan(
            waiting_minutes=Decimal("3"),
            healthy_pods=0,
            active_pods=0,
            waiting_jobs=1,
        )

        self.assertEqual(plan.recommended_total_pods, 1)
        self.assertEqual(plan.recommended_additional_pods, 1)
        self.assertFalse(plan.should_alert)

    def test_old_small_waiting_queue_alerts_even_when_load_is_below_minimum(self) -> None:
        plan = _plan(
            waiting_minutes=Decimal("3"),
            healthy_pods=1,
            active_pods=1,
            waiting_jobs=1,
            oldest_wait_minutes=12,
        )

        self.assertEqual(plan.recommended_additional_pods, 0)
        self.assertTrue(plan.should_alert)
        self.assertEqual(plan.alert_reason, "oldest_wait_exceeded")


def _plan(
    *,
    waiting_minutes: Decimal,
    healthy_pods: int,
    active_pods: int,
    waiting_jobs: int = 1,
    oldest_wait_minutes: int = 0,
    max_active_pods: int = 10,
):
    return calculate_queue_load_plan(
        waiting_for_pod_jobs_count=waiting_jobs,
        queued_jobs_count=0,
        generating_jobs_count=0,
        total_waiting_audio_seconds=waiting_minutes * Decimal("60"),
        healthy_pods_count=healthy_pods,
        idle_healthy_pods_count=healthy_pods,
        busy_pods_count=0,
        active_pods_count=active_pods,
        oldest_wait_minutes=oldest_wait_minutes,
        target_minutes_per_pod_min=Decimal("5"),
        target_minutes_per_pod_max=Decimal("6"),
        alert_min_total_minutes=Decimal("5"),
        max_recommended_pods=5,
        max_active_pods=max_active_pods,
        min_waiting_jobs_for_count_alert=2,
        target_wait_minutes_for_oldest_alert=10,
        include_generating=True,
        planning_enabled=True,
    )


if __name__ == "__main__":
    unittest.main()
