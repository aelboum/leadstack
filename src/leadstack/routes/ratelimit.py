"""Per-user rate limits for routes that have no tenant yet (workspace
creation, invitation acceptance), built on SaaS-OS's `infra.ratelimit`.
Tenant routes are already limited by SaaS-OS's ingress chain.

Fails closed like the platform: an unavailable limiter rejects the request
with 503, never lets it through."""

from __future__ import annotations

import dataclasses
import uuid

from api.errors import rate_limited, service_unavailable
from infra.ratelimit import (
    RateLimitBackendError,
    RateLimitExceededError,
    enforce_rate_limit,
    get_ratelimit_config,
)

_BACKEND_RETRY_AFTER_SECONDS = 5


async def limit_actor(actor_id: uuid.UUID, bucket: str, *, per_hour: int) -> None:
    await limit_key(f"{bucket}:{actor_id}", per_hour=per_hour)


async def limit_key(key: str, *, per_hour: int) -> None:
    """Fixed-window limit on an arbitrary key (e.g. a hashed client address)."""
    config = dataclasses.replace(
        get_ratelimit_config(),
        requests_per_window=per_hour,
        window_seconds=3600,
        key_prefix="leadstack:ratelimit",
    )
    try:
        await enforce_rate_limit(key, config=config)
    except RateLimitExceededError as exc:
        raise rate_limited(exc.retry_after_seconds) from None
    except RateLimitBackendError:
        raise service_unavailable(_BACKEND_RETRY_AFTER_SECONDS) from None
