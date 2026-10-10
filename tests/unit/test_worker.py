"""The LeadStack worker carries every SaaS-OS platform job plus LeadStack's
own, and fails closed on bad invocation or an unreachable Redis."""

from __future__ import annotations

import asyncio

from api.worker import registered_job_functions

from leadstack.jobs import (
    NOOP_JOB_NAME,
    PROCESS_EVENTS_JOB_NAME,
    SEND_MESSAGE_JOB_NAME,
    leadstack_noop,
)
from leadstack.worker import build_leadstack_worker, main


def test_worker_registers_platform_jobs_and_product_job() -> None:
    worker = build_leadstack_worker(burst=True)

    platform_names = {function.name for function in registered_job_functions()}
    assert platform_names, "SaaS-OS registered no platform jobs -- interface changed?"
    assert set(worker.functions) == platform_names | {
        NOOP_JOB_NAME,
        PROCESS_EVENTS_JOB_NAME,
        SEND_MESSAGE_JOB_NAME,
    }


def test_noop_job_is_side_effect_free() -> None:
    assert asyncio.run(leadstack_noop()) == {"ok": True}


def test_unknown_arguments_exit_non_zero() -> None:
    assert main(["--bogus"]) == 1


def test_health_check_fails_when_redis_is_unreachable() -> None:
    assert main(["--check"]) == 1
