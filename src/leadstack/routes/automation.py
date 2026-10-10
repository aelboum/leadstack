"""Workflow routes and the post-request event dispatch hook."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from api.context import RequestContext
from api.dependencies import require_permission
from fastapi import APIRouter, Depends, Response, status
from infra.jobs import TenantJobPayload, enqueue_job
from pydantic import BaseModel, Field, StringConstraints

from leadstack.domains.automation import service as automation
from leadstack.domains.workspaces import permissions as perms
from leadstack.jobs import PROCESS_EVENTS_JOB_NAME
from leadstack.routes.pagination import LimitQuery
from leadstack.routes.validation import StrictModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["automation"])
W = "/workspaces/{tenant_id}"


def _require(permission: tuple[str, str]):
    resource, action = permission
    return Depends(require_permission(resource, action))


async def dispatch_events(tenant_id: uuid.UUID) -> None:
    """Ask the worker to process this workspace's pending events. Never fails
    the request: if the queue is unavailable the events stay in the outbox
    and `leadstack-admin process-events` (or the next dispatch) handles them."""
    try:
        await enqueue_job(PROCESS_EVENTS_JOB_NAME, TenantJobPayload(tenant_id=str(tenant_id)))
    except Exception as exc:  # noqa: BLE001 -- best effort by design (outbox)
        logger.warning("event_dispatch_failed", extra={"error_type": type(exc).__name__})


class WorkflowIn(StrictModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    trigger: Literal["contact.created", "form.submitted", "deal.stage_changed"]
    conditions: dict[Annotated[str, StringConstraints(max_length=32)], str | bool] = Field(
        default_factory=dict, max_length=5
    )
    actions: list[dict[str, Any]] = Field(min_length=1, max_length=automation.MAX_ACTIONS)


class WorkflowStatusIn(StrictModel):
    status: Literal["active", "paused"]


class WorkflowOut(BaseModel):
    workflow_id: uuid.UUID
    name: str
    trigger: str
    conditions: dict[str, Any]
    actions: list[dict[str, Any]]
    status: str
    paused_reason: str | None
    created_by_user_id: uuid.UUID
    created_at: datetime


class RunOut(BaseModel):
    run_id: uuid.UUID
    event_id: uuid.UUID
    status: str
    error_code: str | None
    steps: list[dict[str, Any]]
    started_at: datetime
    finished_at: datetime | None


@router.post(f"{W}/workflows", status_code=status.HTTP_201_CREATED, response_model=WorkflowOut)
def create_workflow(body: WorkflowIn, context: RequestContext = _require(perms.WORKFLOWS_MANAGE)):
    view = automation.create_workflow(
        context.actor_id,
        context.tenant_id,
        name=body.name,
        trigger=body.trigger,
        conditions=dict(body.conditions),
        actions=body.actions,
    )
    return WorkflowOut(**view.__dict__)


@router.get(f"{W}/workflows", response_model=list[WorkflowOut])
def list_workflows(context: RequestContext = _require(perms.WORKFLOWS_READ)):
    items = automation.list_workflows(context.actor_id, context.tenant_id)
    return [WorkflowOut(**w.__dict__) for w in items]


@router.post(f"{W}/workflows/{{workflow_id}}/status", response_model=WorkflowOut)
def set_status(
    workflow_id: uuid.UUID,
    body: WorkflowStatusIn,
    context: RequestContext = _require(perms.WORKFLOWS_MANAGE),
):
    view = automation.set_workflow_status(
        context.actor_id, context.tenant_id, workflow_id, body.status
    )
    return WorkflowOut(**view.__dict__)


@router.delete(f"{W}/workflows/{{workflow_id}}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(
    workflow_id: uuid.UUID, context: RequestContext = _require(perms.WORKFLOWS_MANAGE)
):
    automation.delete_workflow(context.actor_id, context.tenant_id, workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(f"{W}/workflows/{{workflow_id}}/runs", response_model=list[RunOut])
def list_runs(
    workflow_id: uuid.UUID,
    limit: int = LimitQuery,
    context: RequestContext = _require(perms.WORKFLOWS_READ),
):
    items = automation.list_runs(context.actor_id, context.tenant_id, workflow_id, limit=limit)
    return [RunOut(**r.__dict__) for r in items]
