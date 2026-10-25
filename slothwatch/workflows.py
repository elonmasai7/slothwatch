"""Durable orchestration with Temporal. Optional: without `temporalio` the CLI/demo run in-process.

Design: the ladder is a pure function and Engine.run is IDEMPOTENT per window, so a worker crash mid-run is
safe: Temporal retries the activity, the engine sees the finished window and returns the stored result, and
exactly one alert row exists. The workflow then durably WAITS (up to 7 days) for your approve/ignore/snooze
signal. It never sends anything; approval only writes a draft to the outbox folder.

Granularity note: three activities (pipeline, notify, apply_decision) rather than one per rung, because the rungs
run atomically inside the engine; per-rung timing lives in the Sentry/JSONL spans instead."""
from __future__ import annotations
import asyncio
from datetime import timedelta

try:
    from temporalio import activity, workflow
    from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleIntervalSpec, ScheduleSpec
    from temporalio.common import RetryPolicy
    from temporalio.worker import Worker
    HAVE_TEMPORAL = True
except ImportError:  # pragma: no cover
    HAVE_TEMPORAL = False

TASK_QUEUE = "slothwatch"

if HAVE_TEMPORAL:
    RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0,
                        maximum_interval=timedelta(minutes=2), maximum_attempts=5)

    @activity.defn
    async def pipeline_activity(params: dict) -> dict:
        def work():
            from .config import load_config
            from .ingest import load_path
            from .runner import Engine
            cfg = load_config(None, data_dir=params["data_dir"])
            return Engine(cfg).run_real(load_path(params["path"], cfg))
        return await asyncio.to_thread(work)

    @activity.defn
    async def notify_activity(params: dict, result: dict) -> None:
        from .config import load_config
        from .notify import deliver
        deliver(result["message"], load_config(None, data_dir=params["data_dir"]), result.get("pings", 0))

    @activity.defn
    async def decision_activity(params: dict, alert_id: int, decision: str) -> dict:
        from .config import load_config
        from .runner import Engine
        return await asyncio.to_thread(Engine(load_config(None, data_dir=params["data_dir"])).decide, alert_id, decision)

    @workflow.defn
    class SlothNightly:
        def __init__(self) -> None:
            self.decisions: dict[int, str] = {}

        @workflow.signal
        def decide(self, alert_id: int, decision: str) -> None:
            self.decisions[alert_id] = decision

        @workflow.run
        async def run(self, params: dict) -> dict:
            res = await workflow.execute_activity(pipeline_activity, params,
                                                  start_to_close_timeout=timedelta(minutes=10), retry_policy=RETRY)
            await workflow.execute_activity(notify_activity, args=[params, res],
                                            start_to_close_timeout=timedelta(minutes=1), retry_policy=RETRY)
            pending = list(res.get("act_alert_ids", []))
            if pending:
                try:   # a durable sleep: survives worker restarts
                    await workflow.wait_condition(lambda: all(i in self.decisions for i in pending),
                                                  timeout=timedelta(days=7))
                except asyncio.TimeoutError:
                    pass
                for i, d in self.decisions.items():
                    await workflow.execute_activity(decision_activity, args=[params, i, d],
                                                    start_to_close_timeout=timedelta(minutes=1), retry_policy=RETRY)
            return {"alerts": len(pending), "decided": dict(self.decisions)}


async def run_worker(cfg, address: str = "localhost:7233"):
    if not HAVE_TEMPORAL:
        raise SystemExit("temporalio not installed: pip install temporalio  (and run `temporal server start-dev`)")
    client = await Client.connect(address)
    print(f"Worker up on task queue '{TASK_QUEUE}'. Kill it mid-run and restart it; the workflow resumes.")
    await Worker(client, task_queue=TASK_QUEUE, workflows=[SlothNightly],
                 activities=[pipeline_activity, notify_activity, decision_activity]).run()


async def create_schedule(cfg, path: str, address: str = "localhost:7233", hour: int = 2):
    if not HAVE_TEMPORAL:
        raise SystemExit("temporalio not installed: pip install temporalio")
    client = await Client.connect(address)
    params = {"path": path, "data_dir": cfg.data_dir}
    await client.create_schedule(
        "slothwatch-nightly",
        Schedule(action=ScheduleActionStartWorkflow(SlothNightly.run, params, id="slothwatch-nightly-run",
                                                    task_queue=TASK_QUEUE),
                 spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(days=1), offset=timedelta(hours=hour))])))
    print(f"Schedule 'slothwatch-nightly' created: every night at {hour:02d}:00 UTC.")
