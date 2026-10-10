"""Contact and company routes: `/v1/workspaces/{tenant_id}/contacts|companies`.

Same ingress chain as every tenant route (see routes/workspaces.py). Request
bodies forbid unknown fields; for updates, an omitted field is left
unchanged and an explicit `null` clears it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from api.context import RequestContext
from api.dependencies import require_permission
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, StringConstraints

from leadstack.domains.crm import service as crm
from leadstack.domains.workspaces import permissions as perms
from leadstack.routes.automation import dispatch_events
from leadstack.routes.pagination import CursorQuery, LimitQuery
from leadstack.routes.validation import StrictModel, has_control_characters

router = APIRouter(tags=["crm"])


def _require(permission: tuple[str, str]):
    resource, action = permission
    return Depends(require_permission(resource, action))


Text2 = Annotated[str, StringConstraints(max_length=2)]
Text32 = Annotated[str, StringConstraints(max_length=32)]
Text64 = Annotated[str, StringConstraints(max_length=64)]
Text100 = Annotated[str, StringConstraints(max_length=100)]
Text150 = Annotated[str, StringConstraints(max_length=150)]
Text254 = Annotated[str, StringConstraints(max_length=254)]
Text2048 = Annotated[str, StringConstraints(max_length=2048)]
Text10k = Annotated[str, StringConstraints(max_length=10_000)]
CompanyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class _Strict(StrictModel):
    """Unknown fields and control characters are rejected (routes/validation.py)."""


class CompanyIn(_Strict):
    name: CompanyName
    website: Text2048 | None = None
    phone: Text32 | None = None
    country: Text2 | None = None
    notes: Text10k | None = None


class CompanyPatch(_Strict):
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
        | None
    ) = None
    website: Text2048 | None = None
    phone: Text32 | None = None
    country: Text2 | None = None
    notes: Text10k | None = None


class CompanyOut(BaseModel):
    company_id: uuid.UUID
    name: str
    website: str | None
    phone_e164: str | None
    country: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class CompanyPage(BaseModel):
    items: list[CompanyOut]
    next_cursor: str | None


class ContactIn(_Strict):
    first_name: Text100 | None = None
    last_name: Text100 | None = None
    email: Text254 | None = None
    phone: Text32 | None = None
    company_id: uuid.UUID | None = None
    job_title: Text150 | None = None
    source: Text64 | None = None
    owner_user_id: uuid.UUID | None = None


class ContactOut(BaseModel):
    contact_id: uuid.UUID
    first_name: str | None
    last_name: str | None
    email: str | None
    phone_e164: str | None
    company_id: uuid.UUID | None
    job_title: str | None
    source: str | None
    owner_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ContactPage(BaseModel):
    items: list[ContactOut]
    next_cursor: str | None


def _query(q: str | None) -> str | None:
    if q is not None and has_control_characters(q):
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "q"),
                    "msg": "control characters",
                    "input": q,
                }
            ]
        )
    return q


def _provided(body: BaseModel, cls):
    """Only fields the client actually sent; explicit nulls are kept."""
    return cls(**{name: getattr(body, name) for name in body.model_fields_set})


# --- Companies ----------------------------------------------------------------


@router.post(
    "/workspaces/{tenant_id}/companies",
    status_code=status.HTTP_201_CREATED,
    response_model=CompanyOut,
)
def create_company(body: CompanyIn, context: RequestContext = _require(perms.COMPANIES_CREATE)):
    view = crm.create_company(
        context.actor_id, context.tenant_id, _provided(body, crm.CompanyInput)
    )
    return CompanyOut(**view.__dict__)


@router.get("/workspaces/{tenant_id}/companies", response_model=CompanyPage)
def list_companies(
    q: str | None = Query(None, max_length=crm.MAX_QUERY),
    limit: int = LimitQuery,
    cursor: str | None = CursorQuery,
    context: RequestContext = _require(perms.COMPANIES_READ),
):
    page = crm.list_companies(
        context.actor_id, context.tenant_id, q=_query(q), limit=limit, cursor=cursor
    )
    return CompanyPage(
        items=[CompanyOut(**v.__dict__) for v in page.items], next_cursor=page.next_cursor
    )


@router.get("/workspaces/{tenant_id}/companies/{company_id}", response_model=CompanyOut)
def get_company(company_id: uuid.UUID, context: RequestContext = _require(perms.COMPANIES_READ)):
    return CompanyOut(**crm.get_company(context.actor_id, context.tenant_id, company_id).__dict__)


@router.patch("/workspaces/{tenant_id}/companies/{company_id}", response_model=CompanyOut)
def update_company(
    company_id: uuid.UUID,
    body: CompanyPatch,
    context: RequestContext = _require(perms.COMPANIES_UPDATE),
):
    data = _provided(body, crm.CompanyInput)
    view = crm.update_company(context.actor_id, context.tenant_id, company_id, data)
    return CompanyOut(**view.__dict__)


@router.delete(
    "/workspaces/{tenant_id}/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_company(
    company_id: uuid.UUID, context: RequestContext = _require(perms.COMPANIES_DELETE)
):
    crm.delete_company(context.actor_id, context.tenant_id, company_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Contacts -------------------------------------------------------------------


@router.post(
    "/workspaces/{tenant_id}/contacts",
    status_code=status.HTTP_201_CREATED,
    response_model=ContactOut,
)
def create_contact(
    body: ContactIn,
    background: BackgroundTasks,
    context: RequestContext = _require(perms.CONTACTS_CREATE),
):
    view = crm.create_contact(
        context.actor_id, context.tenant_id, _provided(body, crm.ContactInput)
    )
    background.add_task(dispatch_events, context.tenant_id)
    return ContactOut(**view.__dict__)


@router.get("/workspaces/{tenant_id}/contacts", response_model=ContactPage)
def list_contacts(
    q: str | None = Query(None, max_length=crm.MAX_QUERY),
    company_id: uuid.UUID | None = None,
    tag_id: uuid.UUID | None = None,
    owner: Literal["me"] | uuid.UUID | None = None,
    limit: int = LimitQuery,
    cursor: str | None = CursorQuery,
    context: RequestContext = _require(perms.CONTACTS_READ),
):
    page = crm.list_contacts(
        context.actor_id,
        context.tenant_id,
        q=_query(q),
        company_id=company_id,
        limit=limit,
        cursor=cursor,
        tag_id=tag_id,
        owner_user_id=context.actor_id if owner == "me" else owner,
    )
    return ContactPage(
        items=[ContactOut(**v.__dict__) for v in page.items], next_cursor=page.next_cursor
    )


@router.get("/workspaces/{tenant_id}/contacts/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: uuid.UUID, context: RequestContext = _require(perms.CONTACTS_READ)):
    return ContactOut(**crm.get_contact(context.actor_id, context.tenant_id, contact_id).__dict__)


@router.patch("/workspaces/{tenant_id}/contacts/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: uuid.UUID,
    body: ContactIn,
    context: RequestContext = _require(perms.CONTACTS_UPDATE),
):
    data = _provided(body, crm.ContactInput)
    view = crm.update_contact(context.actor_id, context.tenant_id, contact_id, data)
    return ContactOut(**view.__dict__)


@router.delete(
    "/workspaces/{tenant_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_contact(
    contact_id: uuid.UUID, context: RequestContext = _require(perms.CONTACTS_DELETE)
):
    crm.delete_contact(context.actor_id, context.tenant_id, contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
