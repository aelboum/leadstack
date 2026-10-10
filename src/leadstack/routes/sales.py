"""Pipeline and deal routes (`/v1/workspaces/{tenant_id}/pipelines|deals`).

Amounts travel as decimal *strings* with an explicit ISO 4217 currency
(`{"amount": "1250.50", "currency": "MAD"}`); JSON floats are rejected.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from api.context import RequestContext
from api.dependencies import require_permission
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from pydantic import BaseModel, Field, StrictInt, StringConstraints

from leadstack.domains.sales import service as sales
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces import service as workspaces
from leadstack.routes.automation import dispatch_events
from leadstack.routes.pagination import CursorQuery, LimitQuery
from leadstack.routes.validation import StrictModel

router = APIRouter(tags=["sales"])
W = "/workspaces/{tenant_id}"


def _require(permission: tuple[str, str]):
    resource, action = permission
    return Depends(require_permission(resource, action))


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Amount = Annotated[str, StringConstraints(min_length=1, max_length=32)] | StrictInt
Currency = Annotated[str, StringConstraints(min_length=3, max_length=3)]


class StageIn(StrictModel):
    name: Name
    probability: StrictInt = Field(ge=0, le=100)
    kind: Literal["open", "won", "lost"] = "open"


class PipelineIn(StrictModel):
    name: Name
    stages: list[StageIn] | None = Field(default=None, min_length=2, max_length=20)


class StageOut(BaseModel):
    stage_id: uuid.UUID
    name: str
    position: int
    probability: int
    kind: str


class PipelineOut(BaseModel):
    pipeline_id: uuid.UUID
    name: str
    stages: list[StageOut]

    @classmethod
    def of(cls, view: sales.PipelineView) -> PipelineOut:
        return cls(
            pipeline_id=view.pipeline_id,
            name=view.name,
            stages=[StageOut(**s.__dict__) for s in view.stages],
        )


class DealIn(StrictModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    stage_id: uuid.UUID | None = None
    amount: Amount | None = None
    currency: Currency | None = None
    contact_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    expected_close_date: date | None = None


class DealPatch(StrictModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    amount: Amount | None = None
    currency: Currency | None = None
    contact_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    expected_close_date: date | None = None


class DealMove(StrictModel):
    stage_id: uuid.UUID


class DealOut(BaseModel):
    deal_id: uuid.UUID
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID
    title: str
    amount: str | None
    currency: str | None
    amount_minor: int | None
    contact_id: uuid.UUID | None
    company_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    expected_close_date: date | None
    status: str
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DealList(BaseModel):
    items: list[DealOut]
    next_cursor: str | None


class HistoryOut(BaseModel):
    from_stage_id: uuid.UUID | None
    to_stage_id: uuid.UUID
    changed_by_user_id: uuid.UUID
    changed_at: datetime


class StageSummaryOut(BaseModel):
    stage_id: uuid.UUID
    name: str
    kind: str
    deal_count: int
    totals: dict[str, str]
    weighted: dict[str, str]


def _sent(body: BaseModel) -> dict[str, object]:
    return {name: getattr(body, name) for name in body.model_fields_set}


# --- Pipelines ----------------------------------------------------------------------


@router.post(f"{W}/pipelines", status_code=status.HTTP_201_CREATED, response_model=PipelineOut)
def create_pipeline(body: PipelineIn, context: RequestContext = _require(perms.PIPELINES_MANAGE)):
    locale = workspaces.get_workspace(context.actor_id, context.tenant_id).locale
    view = sales.create_pipeline(
        context.actor_id,
        context.tenant_id,
        name=body.name,
        stages=[s.model_dump() for s in body.stages] if body.stages is not None else None,
        locale=locale,
    )
    return PipelineOut.of(view)


@router.get(f"{W}/pipelines", response_model=list[PipelineOut])
def list_pipelines(context: RequestContext = _require(perms.DEALS_READ)):
    return [PipelineOut.of(p) for p in sales.list_pipelines(context.actor_id, context.tenant_id)]


@router.get(f"{W}/pipelines/{{pipeline_id}}", response_model=PipelineOut)
def get_pipeline(pipeline_id: uuid.UUID, context: RequestContext = _require(perms.DEALS_READ)):
    return PipelineOut.of(sales.get_pipeline(context.actor_id, context.tenant_id, pipeline_id))


@router.delete(f"{W}/pipelines/{{pipeline_id}}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(
    pipeline_id: uuid.UUID, context: RequestContext = _require(perms.PIPELINES_MANAGE)
):
    sales.delete_pipeline(context.actor_id, context.tenant_id, pipeline_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(f"{W}/pipelines/{{pipeline_id}}/summary", response_model=list[StageSummaryOut])
def pipeline_summary(pipeline_id: uuid.UUID, context: RequestContext = _require(perms.DEALS_READ)):
    items = sales.pipeline_summary(context.actor_id, context.tenant_id, pipeline_id)
    return [StageSummaryOut(**s.__dict__) for s in items]


# --- Deals --------------------------------------------------------------------------


@router.post(
    f"{W}/pipelines/{{pipeline_id}}/deals",
    status_code=status.HTTP_201_CREATED,
    response_model=DealOut,
)
def create_deal(
    pipeline_id: uuid.UUID, body: DealIn, context: RequestContext = _require(perms.DEALS_CREATE)
):
    view = sales.create_deal(
        context.actor_id, context.tenant_id, pipeline_id, _sent(body) | {"title": body.title}
    )
    return DealOut(**view.__dict__)


@router.get(f"{W}/deals", response_model=DealList)
def list_deals(
    pipeline_id: uuid.UUID | None = None,
    stage_id: uuid.UUID | None = None,
    status_filter: Literal["open", "won", "lost"] | None = Query(None, alias="status"),
    owner: Literal["me"] | uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    limit: int = LimitQuery,
    cursor: str | None = CursorQuery,
    context: RequestContext = _require(perms.DEALS_READ),
):
    page = sales.list_deals(
        context.actor_id,
        context.tenant_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        status=status_filter,
        owner_user_id=context.actor_id if owner == "me" else owner,
        contact_id=contact_id,
        limit=limit,
        cursor=cursor,
    )
    return DealList(items=[DealOut(**d.__dict__) for d in page.items], next_cursor=page.next_cursor)


@router.get(f"{W}/deals/{{deal_id}}", response_model=DealOut)
def get_deal(deal_id: uuid.UUID, context: RequestContext = _require(perms.DEALS_READ)):
    return DealOut(**sales.get_deal(context.actor_id, context.tenant_id, deal_id).__dict__)


@router.patch(f"{W}/deals/{{deal_id}}", response_model=DealOut)
def update_deal(
    deal_id: uuid.UUID, body: DealPatch, context: RequestContext = _require(perms.DEALS_UPDATE)
):
    fields = _sent(body)
    if "title" in fields and fields["title"] is None:
        fields.pop("title")
    view = sales.update_deal(context.actor_id, context.tenant_id, deal_id, fields)
    return DealOut(**view.__dict__)


@router.post(f"{W}/deals/{{deal_id}}/stage", response_model=DealOut)
def move_deal(
    deal_id: uuid.UUID,
    body: DealMove,
    background: BackgroundTasks,
    context: RequestContext = _require(perms.DEALS_UPDATE),
):
    view = sales.move_deal(context.actor_id, context.tenant_id, deal_id, body.stage_id)
    background.add_task(dispatch_events, context.tenant_id)
    return DealOut(**view.__dict__)


@router.delete(f"{W}/deals/{{deal_id}}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deal(deal_id: uuid.UUID, context: RequestContext = _require(perms.DEALS_DELETE)):
    sales.delete_deal(context.actor_id, context.tenant_id, deal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(f"{W}/deals/{{deal_id}}/history", response_model=list[HistoryOut])
def deal_history(deal_id: uuid.UUID, context: RequestContext = _require(perms.DEALS_READ)):
    items = sales.deal_history(context.actor_id, context.tenant_id, deal_id)
    return [HistoryOut(**h.__dict__) for h in items]
