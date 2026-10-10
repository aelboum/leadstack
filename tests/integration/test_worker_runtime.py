"""The LeadStack worker executes LeadStack's own registered job against real
Redis -- both in-process (burst) and as the real `leadstack.worker` process."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid

import pytest
from infra.jobs import TenantJobPayload, enqueue_job, get_jobs_config

from leadstack.jobs import NOOP_JOB_NAME
from leadstack.worker import build_leadstack_worker
from tests.integration.conftest import DisposableServices

pytestmark = pytest.mark.integration


def test_burst_worker_executes_product_job(migrated, platform_env: DisposableServices) -> None:
    queue = f"leadstack-test:{uuid.uuid4().hex}"

    async def scenario() -> tuple[int, int]:
        await enqueue_job(
            NOOP_JOB_NAME, TenantJobPayload(tenant_id=str(uuid.uuid4())), queue_name=queue
        )
        worker = build_leadstack_worker(get_jobs_config(), burst=True, queue_name=queue)
        try:
            await worker.main()
        finally:
            await worker.close()
        return worker.jobs_complete, worker.jobs_failed

    complete, failed = asyncio.run(scenario())

    assert (complete, failed) == (1, 0)


def test_worker_process_starts_and_executes_product_job(
    migrated, platform_env: DisposableServices
) -> None:
    """The real entrypoint, as deployment runs it, on the default queue."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "leadstack.worker"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output: list[str] = []
    try:
        deadline = time.monotonic() + 30
        started = False
        while time.monotonic() < deadline and process.poll() is None:
            line = process.stdout.readline() if process.stdout else ""
            output.append(line)
            if "worker_starting" in line:
                started = True
                break
        assert started, "".join(output)

        asyncio.run(enqueue_job(NOOP_JOB_NAME, TenantJobPayload(tenant_id=str(uuid.uuid4()))))

        succeeded = False
        while time.monotonic() < deadline and process.poll() is None:
            line = process.stdout.readline() if process.stdout else ""
            output.append(line)
            if "job_succeeded" in line and NOOP_JOB_NAME in line:
                succeeded = True
                break
        assert succeeded, "".join(output)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
