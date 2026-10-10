"""LeadStack's background-worker entrypoint.

    leadstack-worker            # long-running worker (or: python -m leadstack.worker)
    leadstack-worker --check    # health probe: exit 0 iff a live worker's sentinel is in Redis

SaaS-OS's own `python -m api.worker` runs a fixed list of platform jobs and
has no extension point for a consumer's jobs, so LeadStack runs its own
process built only from SaaS-OS interfaces:

- `api.worker.registered_job_functions()` -- every platform job a production
  worker must execute (webhook delivery, usage ingestion, notification
  dispatch, session cleanup). Reused rather than re-listed so a platform
  upgrade that adds a job is picked up automatically;
- `infra.jobs.build_worker()` -- the one arq `Worker` constructor;
- `infra.db.validate_application_role()` -- the same fail-closed guard the
  HTTP app runs: the worker refuses a superuser or BYPASSRLS database role;
- `api.worker.check_health()` -- the Redis health-sentinel probe.

No scheduler is introduced (scheduling is an open platform gap, see
docs/architecture/saas-os-integration.md).
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from arq.constants import default_queue_name

if TYPE_CHECKING:
    from arq.worker import Worker
    from infra.jobs import JobsConfig

logger = logging.getLogger(__name__)

# Matches the SaaS-OS worker's sentinel interval, which its --check probe
# (`api.worker.check_health`, reused below) is designed around.
HEALTH_CHECK_INTERVAL_SECONDS = 15.0

_EXIT_OK = 0
_EXIT_FAILURE = 1


def build_leadstack_worker(
    config: JobsConfig | None = None,
    *,
    burst: bool = False,
    queue_name: str = default_queue_name,
) -> Worker:
    """Platform jobs plus LeadStack jobs on one arq worker. Constructs the
    worker without starting it. Platform job modules register their
    handlers at import time, which resolves `REDIS_URL` -- so the imports
    happen here, after configuration has been validated, never at module
    import."""
    from api.worker import registered_job_functions
    from infra.jobs import build_worker, get_jobs_config

    from leadstack.domains.workspaces.purge import register_purge_participant
    from leadstack.jobs import product_job_functions

    register_purge_participant()
    return build_worker(
        [*registered_job_functions(), *product_job_functions()],
        config=config or get_jobs_config(),
        burst=burst,
        queue_name=queue_name,
        health_check_interval_seconds=HEALTH_CHECK_INTERVAL_SECONDS,
    )


def main(argv: list[str] | None = None) -> int:
    """Process entrypoint; returns the exit code. Every startup failure is
    logged by exception type only (never a message that could carry a
    connection string) and exits non-zero -- there is no fallback queue,
    database role, or in-memory mode."""
    from infra.observability import configure_logging, configure_tracing

    args = sys.argv[1:] if argv is None else argv
    configure_logging()

    if args == ["--check"]:
        import asyncio

        from api.worker import check_health

        return asyncio.run(check_health())
    if args:
        logger.error("worker_invalid_arguments", extra={"argument_count": len(args)})
        return _EXIT_FAILURE

    configure_tracing()
    try:
        from infra.db import get_engine, validate_application_role
        from infra.jobs import get_jobs_config

        config = get_jobs_config()
        role = validate_application_role(get_engine())
        worker = build_leadstack_worker(config)
    except Exception as exc:  # noqa: BLE001 -- every startup failure must exit non-zero
        logger.error("worker_startup_failed", extra={"error_type": type(exc).__name__})
        return _EXIT_FAILURE

    logger.info(
        "worker_starting",
        extra={
            "queue_name": worker.queue_name,
            "job_functions": sorted(worker.functions),
            "database_role": role.role_name,
        },
    )
    try:
        worker.run()
    except Exception as exc:  # noqa: BLE001 -- e.g. Redis unreachable after arq's retries
        logger.error("worker_failed", extra={"error_type": type(exc).__name__})
        return _EXIT_FAILURE
    logger.info("worker_stopped", extra={"queue_name": worker.queue_name})
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
