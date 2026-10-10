"""`leadstack-admin` -- operator commands.

    leadstack-admin sync-roles   # grant permissions added by a release to every workspace

Run after `leadstack-migrate upgrade` on every deployment that changes the
role matrix (src/leadstack/domains/workspaces/permissions.py). Idempotent:
it only creates missing roles and grants missing permissions, and never
revokes. Uses the application database role (`DATABASE_URL`).
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid

from infra.db import select, session_scope
from infra.observability import configure_logging

from leadstack.domains.workspaces.models import UserWorkspace
from leadstack.domains.workspaces.service import ensure_workspace_roles

logger = logging.getLogger(__name__)
MAX_RECOVERY_PASSES = 50


def known_workspace_ids() -> list[uuid.UUID]:
    """Every workspace LeadStack provisioned or joined (the user-workspace
    index nominates them; closed tenants are skipped by the caller)."""
    with session_scope() as session:
        return list(session.execute(select(UserWorkspace.tenant_id).distinct()).scalars())


def sync_roles() -> tuple[int, int]:
    from core.tenancy import TenantNotFoundError, TenantStatus, get_tenant, is_closed

    synced = skipped = 0
    for tenant_id in known_workspace_ids():
        try:
            if is_closed(TenantStatus(get_tenant(tenant_id).status)):
                skipped += 1
                continue
        except TenantNotFoundError:
            skipped += 1
            continue
        ensure_workspace_roles(tenant_id)
        synced += 1
    return synced, skipped


def process_all_pending_events() -> int:
    """Recovery path when event dispatch could not be enqueued (Redis down)."""
    from core.tenancy import (
        TenantNotFoundError,
        TenantStatus,
        get_tenant,
        is_accessible_to_principals,
    )

    from leadstack.domains.automation.service import process_events

    total = 0
    for tenant_id in known_workspace_ids():
        try:
            if not is_accessible_to_principals(TenantStatus(get_tenant(tenant_id).status)):
                continue
            for _ in range(MAX_RECOVERY_PASSES):  # bounded per workspace
                count = process_events(tenant_id)
                total += count
                if count == 0:
                    break
        except TenantNotFoundError:
            continue
        except Exception:  # one workspace must not stop recovery of the others
            logger.exception("event_recovery_failed", extra={"tenant_id": str(tenant_id)})
    return total


def prune_automation(older_than_days: int) -> tuple[int, int]:
    from leadstack.domains.automation.service import prune

    events = runs = 0
    for tenant_id in known_workspace_ids():
        e, r = prune(tenant_id, older_than_days=older_than_days)
        events, runs = events + e, runs + r
    return events, runs


def send_queued(older_than_minutes: int) -> int:
    """Re-enqueue messages left `queued` (enqueue failed). Delivery stays
    at-most-once: the job skips anything no longer `queued`."""
    import asyncio
    from datetime import timedelta

    from core.tenancy import (
        TenantNotFoundError,
        TenantStatus,
        get_tenant,
        is_accessible_to_principals,
    )
    from infra.jobs import TenantJobPayload, enqueue_job

    from leadstack.domains.messaging.service import stale_queued
    from leadstack.jobs import SEND_MESSAGE_JOB_NAME

    count = 0
    for tenant_id in known_workspace_ids():
        try:
            if not is_accessible_to_principals(TenantStatus(get_tenant(tenant_id).status)):
                continue
        except TenantNotFoundError:
            continue
        for message_id in stale_queued(tenant_id, older_than=timedelta(minutes=older_than_minutes)):
            payload = TenantJobPayload(
                tenant_id=str(tenant_id), data={"message_id": str(message_id)}
            )
            asyncio.run(enqueue_job(SEND_MESSAGE_JOB_NAME, payload))
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leadstack-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync-roles", help="Reconcile workspace roles with the current role matrix.")
    queued_parser = sub.add_parser(
        "send-queued", help="Re-enqueue email messages stuck in 'queued' for N minutes."
    )
    queued_parser.add_argument("--minutes", type=int, default=15)
    prune_parser = sub.add_parser(
        "prune-automation", help="Delete processed events and finished runs older than N days."
    )
    prune_parser.add_argument("--days", type=int, default=90)
    sub.add_parser(
        "process-events",
        help="Run workflows for pending outbox events of every workspace (recovery).",
    )
    args = parser.parse_args(argv)
    configure_logging()
    if args.command == "sync-roles":
        synced, skipped = sync_roles()
        logger.info("roles_synced", extra={"synced": synced, "skipped": skipped})
        print(f"synced {synced} workspace(s), skipped {skipped} closed")
    elif args.command == "send-queued":
        print(f"re-enqueued {send_queued(max(1, args.minutes))} message(s)")
    elif args.command == "prune-automation":
        events, runs = prune_automation(max(1, args.days))
        print(f"pruned {events} event(s) and {runs} run(s)")
    elif args.command == "process-events":
        total = process_all_pending_events()
        logger.info("events_processed", extra={"events": total})
        print(f"processed {total} pending event(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
