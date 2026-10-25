"""Durable nightly Temporal workflow with signal-based approval and retry backoff."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities import finalize_human_decision, process_nightly


@workflow.defn
class SlothWatchNightlyWorkflow:
    """Process a local CSV folder and wait durably for explicit human approval when needed."""

    def __init__(self) -> None:
        self._decision: dict | None = None

    @workflow.signal
    def decide(self, payload: dict) -> None:
        """Record the user's explicit approve / ignore / snooze signal."""
        self._decision = payload

    @workflow.run
    async def run(self, payload: dict) -> dict:
        result = await workflow.execute_activity(
            process_nightly,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), maximum_interval=timedelta(minutes=1), backoff_coefficient=2.0, maximum_attempts=5),
        )
        pending = [a for a in result.get("alerts", []) if a.get("status") == "pending"]
        decisions = []
        for alert in pending:
            self._decision = None
            await workflow.wait_condition(lambda: self._decision is not None)
            decision = dict(self._decision or {})
            decision.setdefault("alert_id", alert["id"])
            decision.setdefault("config", payload.get("config", "slothwatch.yaml"))
            decisions.append(await workflow.execute_activity(
                finalize_human_decision,
                decision,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3),
            ))
        return {**result, "human_decisions": decisions, "sent": False}
