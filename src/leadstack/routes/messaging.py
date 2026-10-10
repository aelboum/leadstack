"""Consent and email routes, plus the public confirm/unsubscribe endpoints.

Confirm and unsubscribe are POST endpoints called by the app's own pages
(`/c/{token}`, `/u/{token}`) after a click: link scanners that prefetch
URLs in emails must not be able to confirm or unsubscribe anyone.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from api.context import RequestContext
from api.dependencies import require_permission
from fastapi import APIRouter, BackgroundTasks, Depends, status
from infra.jobs import TenantJobPayload, enqueue_job
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from leadstack.domains.messaging import service as messaging
from leadstack.domains.workspaces import permissions as perms
from leadstack.jobs import SEND_MESSAGE_JOB_NAME
from leadstack.routes.pagination import LimitQuery
from leadstack.routes.ratelimit import limit_actor, limit_key
from leadstack.routes.validation import StrictModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["messaging"])
public_router = APIRouter(tags=["public-consent"])
C = "/workspaces/{tenant_id}/contacts/{contact_id}"
SENDS_PER_MEMBER_PER_HOUR = 60
SENDS_PER_WORKSPACE_PER_HOUR = 200


def _require(permission: tuple[str, str]):
    resource, action = permission
    return Depends(require_permission(resource, action))


async def enqueue_send(tenant_id: uuid.UUID, message_id: uuid.UUID) -> None:
    try:
        await enqueue_job(
            SEND_MESSAGE_JOB_NAME,
            TenantJobPayload(tenant_id=str(tenant_id), data={"message_id": str(message_id)}),
        )
    except Exception as exc:  # noqa: BLE001 -- the message stays queued and visible
        logger.warning("message_enqueue_failed", extra={"error_type": type(exc).__name__})


async def after_form_submission(
    tenant_id: uuid.UUID, contact_id: uuid.UUID, submission_id: uuid.UUID
) -> None:
    """Double opt-in for a new form lead. Never fails the submission: a
    missing configuration is logged and the consent stays absent."""
    try:
        message_id = await run_in_threadpool(
            messaging.request_double_opt_in, tenant_id, contact_id, submission_id
        )
    except Exception as exc:  # noqa: BLE001 -- best effort, logged
        logger.warning("double_opt_in_failed", extra={"error_type": type(exc).__name__})
        return
    if message_id is not None:
        await enqueue_send(tenant_id, message_id)


class ConsentIn(StrictModel):
    status: Literal["granted", "withdrawn"]


class ConsentOut(BaseModel):
    channel: str
    status: str
    source: str
    requested_at: datetime
    granted_at: datetime | None
    withdrawn_at: datetime | None
    withdrawn_via: str | None


class EmailIn(StrictModel):
    subject: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    body: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]


class MessageOut(BaseModel):
    message_id: uuid.UUID
    contact_id: uuid.UUID
    kind: str
    to_address: str
    subject: str
    body: str
    status: str
    error_code: str | None
    created_at: datetime
    sent_at: datetime | None


class TokenIn(StrictModel):
    token: Annotated[str, StringConstraints(min_length=20, max_length=1024)]


@router.get(f"{C}/consent", response_model=ConsentOut | None)
def get_consent(contact_id: uuid.UUID, context: RequestContext = _require(perms.CONTACTS_READ)):
    view = messaging.get_consent(context.actor_id, context.tenant_id, contact_id)
    return ConsentOut(**view.__dict__) if view else None


@router.put(f"{C}/consent", response_model=ConsentOut)
def record_consent(
    contact_id: uuid.UUID,
    body: ConsentIn,
    context: RequestContext = _require(perms.CONTACTS_UPDATE),
):
    view = messaging.record_consent(context.actor_id, context.tenant_id, contact_id, body.status)
    return ConsentOut(**view.__dict__)


@router.post(f"{C}/messages", status_code=status.HTTP_202_ACCEPTED, response_model=MessageOut)
async def send_email(
    contact_id: uuid.UUID,
    body: EmailIn,
    background: BackgroundTasks,
    context: RequestContext = _require(perms.MESSAGES_SEND),
):
    # Shared platform sender: bounded per member and per workspace.
    await limit_actor(context.actor_id, "email_send", per_hour=SENDS_PER_MEMBER_PER_HOUR)
    await limit_key(f"email_send_ws:{context.tenant_id}", per_hour=SENDS_PER_WORKSPACE_PER_HOUR)
    view = await run_in_threadpool(
        messaging.queue_email,
        context.actor_id,
        context.tenant_id,
        contact_id,
        subject=body.subject,
        body=body.body,
    )
    background.add_task(enqueue_send, context.tenant_id, view.message_id)
    return MessageOut(**view.__dict__)


@router.get(f"{C}/messages", response_model=list[MessageOut])
def list_messages(
    contact_id: uuid.UUID,
    limit: int = LimitQuery,
    context: RequestContext = _require(perms.CONTACTS_READ),
):
    items = messaging.list_messages(context.actor_id, context.tenant_id, contact_id, limit=limit)
    return [MessageOut(**m.__dict__) for m in items]


@public_router.post("/public/consent/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm(body: TokenIn) -> None:
    await run_in_threadpool(messaging.confirm, body.token)


@public_router.post("/public/consent/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(body: TokenIn) -> None:
    await run_in_threadpool(messaging.unsubscribe, body.token)
