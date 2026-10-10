"""LeadStack's own background-job handlers.

Handlers are plain `async def handler(payload) -> result` functions wrapped
by SaaS-OS's `infra.jobs.register_job()` (retry with backoff, dead-letter,
correlation context, no persisted results). Registration is deferred to
`product_job_functions()` rather than done at import time, because
`register_job()` resolves the Redis configuration when called -- importing
this module must not require `REDIS_URL`.

Delivery is at-least-once: every handler must be idempotent.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import TYPE_CHECKING

from infra.jobs import TenantJobPayload, register_job
from infra.ratelimit import RateLimitExceededError, enforce_rate_limit, get_ratelimit_config

if TYPE_CHECKING:
    from arq.worker import Function

NOOP_JOB_NAME = "leadstack_noop"
PROCESS_EVENTS_JOB_NAME = "leadstack_process_events"
SEND_MESSAGE_JOB_NAME = "leadstack_send_message"
CONFIRMATIONS_PER_ADDRESS_PER_DAY = 3
LOCK_SECONDS = 300
_RELEASE_LOCK = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) "
    "else return 0 end"
)
MAX_PASSES = 20  # bounded work per job (each pass is at most 100 events)


async def leadstack_noop(payload: TenantJobPayload | None = None) -> dict[str, bool]:
    """Phase 1 foundation job: proves the LeadStack worker executes a
    product-registered job. Reads nothing, writes nothing; idempotent."""
    return {"ok": True}


async def leadstack_process_events(payload: TenantJobPayload | None = None) -> dict[str, int]:
    """Run workflows for one workspace's pending outbox events. Idempotent
    (one run per workflow and event); the tenant comes only from the payload
    the API enqueued after an authorized change."""
    import uuid

    import redis.asyncio as aioredis
    from infra.jobs import get_jobs_config
    from starlette.concurrency import run_in_threadpool

    from leadstack.domains.automation.service import process_events

    if payload is None:
        return {"processed": 0}
    tenant_id = uuid.UUID(payload.tenant_id)
    lock_key = f"leadstack:events-lock:{tenant_id}"
    client = aioredis.Redis.from_url(get_jobs_config().redis_url)
    total = 0
    try:
        # One processor per workspace at a time; concurrent dispatches for the
        # same workspace return immediately (the holder drains the outbox).
        token = uuid.uuid4().hex
        if not await client.set(lock_key, token, nx=True, ex=LOCK_SECONDS):
            return {"processed": 0, "coalesced": 1}
        try:
            for _ in range(MAX_PASSES):
                processed = await run_in_threadpool(process_events, tenant_id)
                total += processed
                if processed == 0:
                    break
        finally:
            # Compare-and-delete: after the lock expired (a pass longer than
            # LOCK_SECONDS) another worker may hold it; never release theirs.
            released = client.eval(_RELEASE_LOCK, 1, lock_key, token)
            if inspect.isawaitable(released):  # the stubs type eval() for both clients
                await released
        # Events committed while the lock was being released are picked up here.
        total += await run_in_threadpool(process_events, tenant_id)
    finally:
        await client.aclose()
    return {"processed": total}


async def leadstack_send_message(payload: TenantJobPayload | None = None) -> dict[str, str]:
    """Deliver one queued message (at most once; see messaging.service)."""
    import uuid

    from starlette.concurrency import run_in_threadpool

    from leadstack.domains.messaging import service as messaging
    from leadstack.domains.messaging.service import deliver_message

    if payload is None or "message_id" not in payload.data:
        return {"status": "ignored"}
    tenant_id = uuid.UUID(payload.tenant_id)
    message_id = uuid.UUID(str(payload.data["message_id"]))
    info = await run_in_threadpool(messaging.outbound_info, tenant_id, message_id)
    if info is not None and info.kind == "confirmation" and info.first_attempt:
        # One address cannot be flooded with confirmations through many
        # workspaces' public forms. Backend errors raise (job retry).
        config = dataclasses.replace(
            get_ratelimit_config(),
            requests_per_window=CONFIRMATIONS_PER_ADDRESS_PER_DAY,
            window_seconds=86_400,
            key_prefix="leadstack:confirm-address",
        )
        try:
            await enforce_rate_limit(info.address_hash, config=config)
        except RateLimitExceededError:
            await run_in_threadpool(
                messaging.suppress, tenant_id, message_id, "address_rate_limited"
            )
            return {"status": "suppressed"}
    status = await run_in_threadpool(deliver_message, tenant_id, message_id)
    return {"status": status}


def product_job_functions() -> list[Function]:
    """Every LeadStack job, wrapped for the worker. Call at worker build time."""
    return [
        register_job(leadstack_noop),
        register_job(leadstack_process_events),
        register_job(leadstack_send_message),
    ]
