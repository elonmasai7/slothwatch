"""Create the daily Temporal Schedule; run after the local Temporal service is ready."""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleIntervalSpec, ScheduleSpec

from workflows.temporal_workflow import SlothWatchNightlyWorkflow


async def main() -> None:
    address = os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    queue = os.getenv("SLOTHWATCH_TASK_QUEUE", "slothwatch-local")
    client = await Client.connect(address)
    try:
        await client.create_schedule(
            "slothwatch-nightly",
            Schedule(
                action=ScheduleActionStartWorkflow(SlothWatchNightlyWorkflow.run, {"config": "slothwatch.yaml"}, id="slothwatch-nightly", task_queue=queue),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]),
            ),
        )
        print("Created Temporal Schedule 'slothwatch-nightly' (daily).")
    except Exception as exc:
        if "already exists" in str(exc).lower():
            print("Temporal Schedule 'slothwatch-nightly' already exists.")
        else:
            raise


if __name__ == "__main__":
    asyncio.run(main())
