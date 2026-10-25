"""Run an optional Temporal worker against a locally running Temporal server."""
from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from workflows.activities import finalize_human_decision, process_nightly
from workflows.temporal_workflow import SlothWatchNightlyWorkflow


async def main() -> None:
    address = os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    queue = os.getenv("SLOTHWATCH_TASK_QUEUE", "slothwatch-local")
    client = await Client.connect(address)
    print(f"Temporal worker connected to {address} on task queue {queue}.")
    async with Worker(client, task_queue=queue, workflows=[SlothWatchNightlyWorkflow], activities=[process_nightly, finalize_human_decision]):
        print("Worker is running. Ctrl-C stops the worker; Temporal will redeliver uncompleted activities on restart.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
