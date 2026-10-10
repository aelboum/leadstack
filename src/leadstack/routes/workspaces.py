"""Workspace, client, member, invitation and audit-trail routes.

Every tenant route declares `api.dependencies.require_permission(...)`:
session authentication -> tenant resolution (direct, existing membership in
an accessible tenant; otherwise the same non-enumerating 404) -> per-tenant
rate limit -> `core.rbac.can()` (denials audited). The `tenant_id` in the
path is only input to that chain; handlers act on `context.tenant_id`.
The domain service re-checks authorization itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from api.context import RequestContext
from api.dependencies import get_current_actor, require_permission
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces import service
from leadstack.routes.pagination import CursorQuery, LimitQuery, paginate
from leadstack.routes.ratelimit import limit_actor
from leadstack.routes.validation import StrictModel

router = APIRouter(tags=["workspaces"])

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LocaleCode = Annotated[str, StringConstraints(min_length=2, max_length=16)]
TimeZoneName = Annotated[str, StringConstraints(min_length=1, max_length=64)]
CurrencyCode = Annotated[str, StringConstraints(min_length=3, max_length=3)]
CountryCode = Annotated[str, StringConstraints(min_length=2, max_length=2)]

WORKSPACE_CREATES_PER_HOUR = 10
INVITATION_ACCEPTS_PER_HOUR = 20


def _require(permission: tuple[str, str]):
    resource, action = permission
    return Depends(require_permission(resource, action))


# --- Schemas -----------------------------------------------------------------


class _Strict(StrictModel):
    """Unknown fields and control characters are rejected (routes/validation.py)."""


class WorkspaceCreate(_Strict):
    kind: Literal["agency", "direct"]
    display_name: DisplayName
    locale: LocaleCode
    time_zone: TimeZoneName
    currency: CurrencyCode
    country: CountryCode | None = None


class ClientCreate(_Strict):
    display_name: DisplayName
    locale: LocaleCode | None = None
    time_zone: TimeZoneName | None = None
    currency: CurrencyCode | None = None
    country: CountryCode | None = None


class WorkspaceUpdate(_Strict):
    display_name: DisplayName | None = None
    locale: LocaleCode | None = None
    time_zone: TimeZoneName | None = None
    currency: CurrencyCode | None = None
    country: CountryCode | None = None


class WorkspaceOut(BaseModel):
    tenant_id: uuid.UUID
    kind: str
    display_name: str
    locale: str
    time_zone: str
    currency: str
    country: str | None
    status: str
    parent_tenant_id: uuid.UUID | None

    @classmethod
    def of(cls, view: service.WorkspaceView) -> WorkspaceOut:
        return cls(**view.__dict__)


class WorkspaceList(BaseModel):
    items: list[WorkspaceOut]
    next_cursor: str | None


class ClientStatusChange(_Strict):
    status: Literal["active", "suspended"]


class MemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    roles: list[str]

    @classmethod
    def of(cls, view: service.MemberView) -> MemberOut:
        return cls(
            membership_id=view.membership_id,
            user_id=view.user_id,
            status=view.status,
            roles=list(view.roles),
        )


class MemberList(BaseModel):
    items: list[MemberOut]
    next_cursor: str | None


class MemberRoleChange(_Strict):
    role: Literal["owner", "admin", "member"]


class MemberStatusChange(_Strict):
    action: Literal["suspend", "reactivate", "revoke"]


class InvitationCreate(_Strict):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320)]
    role: Literal["owner", "admin", "member"]


class InvitationOut(BaseModel):
    invitation_id: uuid.UUID
    email: str
    role: str | None
    status: str
    expires_at: datetime
    created_at: datetime

    @classmethod
    def of(cls, view: service.InvitationView) -> InvitationOut:
        return cls(**view.__dict__)


class InvitationCreated(BaseModel):
    invitation: InvitationOut
    token: str = Field(description="Shown once. Share it with the invitee; it is not stored.")


class InvitationList(BaseModel):
    items: list[InvitationOut]
    next_cursor: str | None


class InvitationAccept(_Strict):
    tenant_id: uuid.UUID
    token: Annotated[str, StringConstraints(min_length=16, max_length=512)]


class InvitationAccepted(BaseModel):
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    role: str | None


class AuditEntryOut(BaseModel):
    entry_id: uuid.UUID
    created_at: datetime
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    actor_type: str
    actor_user_id: uuid.UUID | None


class AuditList(BaseModel):
    items: list[AuditEntryOut]
    next_cursor: str | None


# --- Workspaces ----------------------------------------------------------------


@router.post("/workspaces", status_code=status.HTTP_201_CREATED, response_model=WorkspaceOut)
async def create_workspace(
    body: WorkspaceCreate, actor_id: uuid.UUID = Depends(get_current_actor)
) -> WorkspaceOut:
    await limit_actor(actor_id, "workspace_create", per_hour=WORKSPACE_CREATES_PER_HOUR)
    settings = service.RegionalSettings.validated(
        locale=body.locale, time_zone=body.time_zone, currency=body.currency, country=body.country
    )
    view = await _run(
        service.create_workspace,
        actor_id,
        kind=body.kind,
        display_name=body.display_name,
        settings=settings,
    )
    return WorkspaceOut.of(view)


@router.get("/workspaces", response_model=WorkspaceList)
async def list_my_workspaces(actor_id: uuid.UUID = Depends(get_current_actor)) -> WorkspaceList:
    views = await _run(service.list_my_workspaces, actor_id)
    return WorkspaceList(items=[WorkspaceOut.of(v) for v in views], next_cursor=None)


@router.get("/workspaces/{tenant_id}", response_model=WorkspaceOut)
def get_workspace(context: RequestContext = _require(perms.WORKSPACE_READ)) -> WorkspaceOut:
    return WorkspaceOut.of(service.get_workspace(context.actor_id, context.tenant_id))


@router.patch("/workspaces/{tenant_id}", response_model=WorkspaceOut)
def update_workspace(
    body: WorkspaceUpdate, context: RequestContext = _require(perms.WORKSPACE_UPDATE)
) -> WorkspaceOut:
    fields = body.model_fields_set
    view = service.update_workspace(
        context.actor_id,
        context.tenant_id,
        display_name=body.display_name,
        locale=body.locale,
        time_zone=body.time_zone,
        currency=body.currency,
        country=body.country,
        clear_country="country" in fields and body.country is None,
    )
    return WorkspaceOut.of(view)


# --- Agency clients --------------------------------------------------------------


@router.post(
    "/workspaces/{tenant_id}/clients",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkspaceOut,
)
def create_client(
    body: ClientCreate, context: RequestContext = _require(perms.CLIENTS_CREATE)
) -> WorkspaceOut:
    view = service.create_client(
        context.actor_id,
        context.tenant_id,
        display_name=body.display_name,
        locale=body.locale,
        time_zone=body.time_zone,
        currency=body.currency,
        country=body.country,
    )
    return WorkspaceOut.of(view)


@router.get("/workspaces/{tenant_id}/clients", response_model=WorkspaceList)
def list_clients(
    limit: int = LimitQuery,
    cursor: str | None = CursorQuery,
    context: RequestContext = _require(perms.CLIENTS_READ),
) -> WorkspaceList:
    items, next_cursor = paginate(
        service.list_clients(context.actor_id, context.tenant_id), limit, cursor
    )
    return WorkspaceList(items=[WorkspaceOut.of(v) for v in items], next_cursor=next_cursor)


@router.post("/workspaces/{tenant_id}/clients/{client_id}/status", response_model=WorkspaceOut)
def change_client_status(
    client_id: uuid.UUID,
    body: ClientStatusChange,
    context: RequestContext = _require(perms.CLIENTS_MANAGE),
) -> WorkspaceOut:
    view = service.set_client_status(context.actor_id, context.tenant_id, client_id, body.status)
    return WorkspaceOut.of(view)


# --- Members -----------------------------------------------------------------------


@router.get("/workspaces/{tenant_id}/members", response_model=MemberList)
def list_members(
    limit: int = LimitQuery,
    cursor: str | None = CursorQuery,
    context: RequestContext = _require(perms.MEMBERS_READ),
) -> MemberList:
    items, next_cursor = paginate(
        service.list_members(context.actor_id, context.tenant_id), limit, cursor
    )
    return MemberList(items=[MemberOut.of(v) for v in items], next_cursor=next_cursor)


@router.put("/workspaces/{tenant_id}/members/{membership_id}/role", response_model=MemberOut)
def change_member_role(
    membership_id: uuid.UUID,
    body: MemberRoleChange,
    context: RequestContext = _require(perms.MEMBERS_MANAGE),
) -> MemberOut:
    view = service.change_member_role(context.actor_id, context.tenant_id, membership_id, body.role)
    return MemberOut.of(view)


@router.post("/workspaces/{tenant_id}/members/{membership_id}/status", response_model=MemberOut)
def change_member_status(
    membership_id: uuid.UUID,
    body: MemberStatusChange,
    context: RequestContext = _require(perms.MEMBERS_MANAGE),
) -> MemberOut:
    view = service.set_member_status(
        context.actor_id, context.tenant_id, membership_id, body.action
    )
    return MemberOut.of(view)


# --- Invitations -------------------------------------------------------------------


@router.post(
    "/workspaces/{tenant_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationCreated,
)
def invite_member(
    body: InvitationCreate,
    response: Response,
    context: RequestContext = _require(perms.MEMBERS_INVITE),
) -> InvitationCreated:
    created = service.invite_member(
        context.actor_id, context.tenant_id, email=body.email, role=body.role
    )
    response.headers["Cache-Control"] = "no-store"
    return InvitationCreated(invitation=InvitationOut.of(created.invitation), token=created.token)


@router.get("/workspaces/{tenant_id}/invitations", response_model=InvitationList)
def list_invitations(
    limit: int = LimitQuery,
    cursor: str | None = CursorQuery,
    context: RequestContext = _require(perms.MEMBERS_INVITE),
) -> InvitationList:
    items, next_cursor = paginate(
        service.list_invitations(context.actor_id, context.tenant_id), limit, cursor
    )
    return InvitationList(items=[InvitationOut.of(v) for v in items], next_cursor=next_cursor)


@router.delete(
    "/workspaces/{tenant_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def cancel_invitation(
    invitation_id: uuid.UUID, context: RequestContext = _require(perms.MEMBERS_INVITE)
) -> Response:
    service.cancel_invitation(context.actor_id, context.tenant_id, invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations/accept", response_model=InvitationAccepted)
async def accept_invitation(
    body: InvitationAccept, actor_id: uuid.UUID = Depends(get_current_actor)
) -> InvitationAccepted:
    await limit_actor(actor_id, "invitation_accept", per_hour=INVITATION_ACCEPTS_PER_HOUR)
    accepted = await _run(service.accept, actor_id, body.tenant_id, body.token)
    return InvitationAccepted(
        tenant_id=accepted.tenant_id, membership_id=accepted.membership_id, role=accepted.role
    )


# --- Audit trail ---------------------------------------------------------------------


@router.get("/workspaces/{tenant_id}/audit", response_model=AuditList)
def list_audit(
    limit: int = LimitQuery,
    cursor: str | None = CursorQuery,
    context: RequestContext = _require(perms.AUDIT_READ),
) -> AuditList:
    page = service.list_audit(context.actor_id, context.tenant_id, limit=limit, cursor=cursor)
    return AuditList(
        items=[AuditEntryOut(**entry.__dict__) for entry in page.items],
        next_cursor=page.next_cursor,
    )


async def _run(function, /, *args, **kwargs):
    """Run a blocking domain call off the event loop (async routes only;
    sync routes already run in FastAPI's thread pool)."""
    return await run_in_threadpool(function, *args, **kwargs)
