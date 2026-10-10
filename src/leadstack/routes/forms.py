"""Form management (tenant routes) and the public submission endpoints.

Public endpoints (`/v1/public/forms/{public_key}`) take no session and are
meant to be called from other origins (embedded forms), so they:
- answer CORS without credentials (`PublicCorsMiddleware`);
- are exempt from the same-origin guard (nothing session-bound to forge);
- are rate limited per client address and per form, failing closed;
- never reveal whether a key exists, is disabled or belongs to a suspended
  workspace (all are the same 404).

Client address: `request.client.host`. Behind a proxy, run uvicorn with
`--proxy-headers --forwarded-allow-ips=<edge>` so this is the real client.
The address is only hashed into a rate-limit key; it is never stored.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from api.context import RequestContext
from api.dependencies import require_permission
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from leadstack.domains.forms import service as forms
from leadstack.domains.workspaces import permissions as perms
from leadstack.routes.automation import dispatch_events
from leadstack.routes.messaging import after_form_submission
from leadstack.routes.pagination import LimitQuery
from leadstack.routes.ratelimit import limit_key
from leadstack.routes.validation import StrictModel

router = APIRouter(tags=["forms"])
public_router = APIRouter(tags=["public-forms"])
W = "/workspaces/{tenant_id}"

SUBMISSIONS_PER_CLIENT_PER_HOUR = 20  # all POSTs, counted before parsing
CONSENT_ACTIONS_PER_CLIENT_PER_HOUR = 120  # shared NATs; unsubscribing must work
SUBMISSIONS_PER_FORM_PER_HOUR = 500
VIEWS_PER_CLIENT_PER_HOUR = 120


def _require(permission: tuple[str, str]):
    resource, action = permission
    return Depends(require_permission(resource, action))


Label = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class FieldIn(StrictModel):
    key: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    label: Label
    type: Literal["text", "email", "phone", "textarea"]
    required: bool = False
    maps_to: Literal["first_name", "last_name", "email", "phone", "company", "message"] | None = (
        None
    )


class FormIn(StrictModel):
    name: Label
    fields: list[FieldIn] = Field(min_length=1, max_length=20)
    consent_text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    success_message: Annotated[str, StringConstraints(max_length=500)] | None = None
    redirect_url: Annotated[str, StringConstraints(max_length=2048)] | None = None
    source_label: Annotated[str, StringConstraints(max_length=64)] | None = None


class FormStatusIn(StrictModel):
    status: Literal["active", "disabled"]


class FormOut(BaseModel):
    form_id: uuid.UUID
    name: str
    public_key: str
    status: str
    fields: list[dict[str, Any]]
    consent_text: str
    success_message: str | None
    redirect_url: str | None
    source_label: str
    created_at: datetime


class SubmissionOut(BaseModel):
    submission_id: uuid.UUID
    contact_id: uuid.UUID | None
    data: dict[str, Any]
    consented_at: datetime
    submitted_at: datetime


@router.post(f"{W}/forms", status_code=status.HTTP_201_CREATED, response_model=FormOut)
def create_form(body: FormIn, context: RequestContext = _require(perms.FORMS_MANAGE)):
    view = forms.create_form(
        context.actor_id,
        context.tenant_id,
        name=body.name,
        fields=[f.model_dump() for f in body.fields],
        consent_text=body.consent_text,
        success_message=body.success_message,
        redirect_url=body.redirect_url,
        source_label=body.source_label,
    )
    return FormOut(**view.__dict__)


@router.get(f"{W}/forms", response_model=list[FormOut])
def list_forms(context: RequestContext = _require(perms.FORMS_READ)):
    return [FormOut(**f.__dict__) for f in forms.list_forms(context.actor_id, context.tenant_id)]


@router.post(f"{W}/forms/{{form_id}}/status", response_model=FormOut)
def set_status(
    form_id: uuid.UUID, body: FormStatusIn, context: RequestContext = _require(perms.FORMS_MANAGE)
):
    view = forms.set_form_status(context.actor_id, context.tenant_id, form_id, body.status)
    return FormOut(**view.__dict__)


@router.delete(f"{W}/forms/{{form_id}}", status_code=status.HTTP_204_NO_CONTENT)
def delete_form(form_id: uuid.UUID, context: RequestContext = _require(perms.FORMS_MANAGE)):
    forms.delete_form(context.actor_id, context.tenant_id, form_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(f"{W}/forms/{{form_id}}/submissions", response_model=list[SubmissionOut])
def list_submissions(
    form_id: uuid.UUID,
    limit: int = LimitQuery,
    context: RequestContext = _require(perms.CONTACTS_READ),
):
    items = forms.list_submissions(context.actor_id, context.tenant_id, form_id, limit=limit)
    return [SubmissionOut(**s.__dict__) for s in items]


# --- Public ----------------------------------------------------------------------

MAX_PUBLIC_BODY_BYTES = 64 * 1024


class PublicFormOut(BaseModel):
    name: str
    fields: list[dict[str, Any]]
    consent_text: str
    success_message: str | None
    redirect_url: str | None
    form_token: str


class SubmissionIn(StrictModel):
    values: dict[
        Annotated[str, StringConstraints(max_length=32)],
        Annotated[str, StringConstraints(max_length=5000)] | None,
    ] = Field(max_length=20)
    consent: bool
    form_token: Annotated[str, StringConstraints(min_length=10, max_length=128)]
    website: Annotated[str, StringConstraints(max_length=200)] | None = None  # honeypot


class SubmissionAccepted(BaseModel):
    success_message: str | None
    redirect_url: str | None


def client_bucket(host: str | None) -> str:
    """Rate-limit identity of a client address, hashed (never stored raw).
    IPv6 clients are bucketed by /64, since one host typically controls a
    whole /64 and could otherwise rotate addresses freely."""
    host = host or "unknown"
    try:
        address = ipaddress.ip_address(host)
        if address.version == 6:
            host = str(ipaddress.ip_network(f"{address}/64", strict=False))
    except ValueError:
        pass
    return hashlib.sha256(host.encode()).hexdigest()[:32]


def _client(request: Request) -> str:
    return client_bucket(request.client.host if request.client else None)


def _known_shape(public_key: str) -> None:
    # Validate before the key reaches any rate-limit key or lookup.
    if not forms.valid_public_key(public_key):
        raise forms.FormNotFoundError()


@public_router.get("/public/forms/{public_key}", response_model=PublicFormOut)
async def get_public_form(public_key: str, request: Request, response: Response):
    _known_shape(public_key)
    client = _client(request)
    await limit_key(f"form_view:{client}", per_hour=VIEWS_PER_CLIENT_PER_HOUR)
    form = await run_in_threadpool(forms.public_form, public_key, client)
    response.headers["Cache-Control"] = "no-store"  # the token is per client and request
    return PublicFormOut(**form.__dict__)


@public_router.post("/public/forms/{public_key}/submissions", response_model=SubmissionAccepted)
async def submit_public_form(
    public_key: str, body: SubmissionIn, request: Request, background: BackgroundTasks
):
    # The per-client limit already ran in PublicBodyGuardMiddleware, before
    # the body was parsed. The per-form quota counts only submissions that
    # passed every check, so junk traffic cannot exhaust a form's quota.
    _known_shape(public_key)
    client = _client(request)
    prepared = await run_in_threadpool(
        forms.prepare_submission,
        public_key,
        client,
        values=dict(body.values),
        consent=body.consent,
        form_token=body.form_token,
        honeypot=body.website,
    )
    if isinstance(prepared, forms.SubmissionResult):  # honeypot: discarded
        return SubmissionAccepted(
            success_message=prepared.success_message, redirect_url=prepared.redirect_url
        )
    await limit_key(f"form_accept:{public_key}", per_hour=SUBMISSIONS_PER_FORM_PER_HOUR)
    result = await run_in_threadpool(forms.store_submission, prepared, daily_cap=_daily_cap())
    background.add_task(dispatch_events, prepared.tenant_id)
    if result.contact_id is not None and result.submission_id is not None:
        background.add_task(
            after_form_submission, prepared.tenant_id, result.contact_id, result.submission_id
        )
    return SubmissionAccepted(
        success_message=result.success_message, redirect_url=result.redirect_url
    )


def _daily_cap() -> int:
    return max(
        1, int(os.environ.get("LEADSTACK_FORM_DAILY_SUBMISSIONS", forms.DEFAULT_DAILY_SUBMISSIONS))
    )


class PublicBodyGuardMiddleware:
    """For `/v1/public/*` POSTs, before any parsing:
    - rejects bodies over MAX_PUBLIC_BODY_BYTES (declared or streamed): 413;
    - applies the per-client submission limit, so oversized or malformed
      requests are rate limited too.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/v1/public/")
        ):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        declared = headers.get("content-length")
        if declared is not None and (
            not declared.isdigit() or int(declared) > MAX_PUBLIC_BODY_BYTES
        ):
            await _error(413, "payload_too_large", "Request too large.")(scope, receive, send)
            return
        client = client_bucket((scope.get("client") or (None,))[0])
        if scope["path"].startswith("/v1/public/consent/"):
            bucket, per_hour = "public_consent", CONSENT_ACTIONS_PER_CLIENT_PER_HOUR
        else:
            bucket, per_hour = "form_submit", SUBMISSIONS_PER_CLIENT_PER_HOUR
        try:
            await limit_key(f"{bucket}:{client}", per_hour=per_hour)
        except HTTPException as exc:
            code = "rate_limited" if exc.status_code == 429 else "unavailable"
            response = _error(
                exc.status_code,
                code,
                "Too many requests." if code == "rate_limited" else "Temporarily unavailable.",
            )
            for name, value in (exc.headers or {}).items():
                response.headers[name] = value
            await response(scope, receive, send)
            return

        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_PUBLIC_BODY_BYTES:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except _BodyTooLarge:
            await _error(413, "payload_too_large", "Request too large.")(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"detail": {"code": code, "message": message}}
    )


class PublicCorsMiddleware:
    """CORS for `/v1/public/*` only: any origin, no credentials, JSON POST.
    Every other path keeps the browser's default same-origin policy."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/v1/public/"):
            await self.app(scope, receive, send)
            return
        if scope["method"] == "OPTIONS":
            response = Response(status_code=204, headers=_CORS_HEADERS)
            await response(scope, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(k.lower().encode(), v.encode()) for k, v in _CORS_HEADERS.items()]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)


_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
}
