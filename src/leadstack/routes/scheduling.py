"""Booking types, appointments and the public booking endpoints (ADR-0008).

Public endpoints have no login. They share the forms abuse controls: the
per-client limit on every public POST (PublicBodyGuardMiddleware), a
client-bound anti-bot token, per-booking-type and per-workspace caps.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from api.context import RequestContext
from api.dependencies import require_permission
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from leadstack.domains.forms import service as forms
from leadstack.domains.scheduling import service as scheduling
from leadstack.domains.workspaces import permissions as perms
from leadstack.routes.automation import dispatch_events
from leadstack.routes.forms import client_bucket
from leadstack.routes.ratelimit import limit_key
from leadstack.routes.validation import StrictModel

router = APIRouter(tags=["scheduling"])
public_router = APIRouter(tags=["public-booking"])
W = "/workspaces/{tenant_id}"
PAGE_VIEWS_PER_CLIENT_PER_HOUR = 120
BOOKINGS_PER_TYPE_PER_HOUR = 60
BOOKINGS_PER_CLIENT_PER_HOUR = 5  # accepted bookings from one address


def _require(permission: tuple[str, str]):
    resource, action = permission
    return Depends(require_permission(resource, action))


class Rule(StrictModel):
    weekday: int = Field(ge=0, le=6)
    start: int = Field(ge=0, le=1440)
    end: int = Field(ge=0, le=1440)


class BookingTypeIn(StrictModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    host_user_id: uuid.UUID
    duration_minutes: int = Field(ge=5, le=480)
    buffer_minutes: int = Field(default=0, ge=0, le=240)
    min_notice_minutes: int = Field(default=60, ge=0, le=43200)
    horizon_days: int = Field(default=30, ge=1, le=90)
    time_zone: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    rules: list[Rule] = Field(min_length=1, max_length=scheduling.MAX_RULES)


class BookingTypeOut(BaseModel):
    booking_type_id: uuid.UUID
    name: str
    public_key: str
    host_user_id: uuid.UUID
    duration_minutes: int
    buffer_minutes: int
    min_notice_minutes: int
    horizon_days: int
    time_zone: str
    rules: list[dict[str, int]]
    status: str


class StatusIn(StrictModel):
    status: Literal["active", "disabled"]


class AppointmentIn(StrictModel):
    host_user_id: uuid.UUID
    contact_id: uuid.UUID
    starts_at: AwareDatetime
    duration_minutes: int = Field(ge=5, le=480)
    time_zone: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class AppointmentOut(BaseModel):
    appointment_id: uuid.UUID
    booking_type_id: uuid.UUID | None
    host_user_id: uuid.UUID
    contact_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    time_zone: str
    status: str
    source: str


class PublicBookingOut(BaseModel):
    name: str
    duration_minutes: int
    time_zone: str
    slots: list[datetime]
    booking_token: str


class BookingIn(StrictModel):
    starts_at: AwareDatetime
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    email: Annotated[str, StringConstraints(min_length=3, max_length=254)]
    phone: Annotated[str, StringConstraints(max_length=32)] | None = None
    consent: bool
    booking_token: Annotated[str, StringConstraints(min_length=10, max_length=200)]
    website: Annotated[str, StringConstraints(max_length=200)] | None = None  # honeypot


class BookingAccepted(BaseModel):
    appointment_id: uuid.UUID
    starts_at: datetime


@router.post(
    f"{W}/booking-types", status_code=status.HTTP_201_CREATED, response_model=BookingTypeOut
)
def create_booking_type(
    body: BookingTypeIn, context: RequestContext = _require(perms.BOOKING_TYPES_MANAGE)
):
    view = scheduling.create_booking_type(
        context.actor_id,
        context.tenant_id,
        name=body.name,
        host_user_id=body.host_user_id,
        duration_minutes=body.duration_minutes,
        buffer_minutes=body.buffer_minutes,
        min_notice_minutes=body.min_notice_minutes,
        horizon_days=body.horizon_days,
        time_zone=body.time_zone,
        rules=[r.model_dump() for r in body.rules],
    )
    return BookingTypeOut(**view.__dict__)


@router.get(f"{W}/booking-types", response_model=list[BookingTypeOut])
def list_booking_types(context: RequestContext = _require(perms.APPOINTMENTS_READ)):
    return [
        BookingTypeOut(**v.__dict__)
        for v in scheduling.list_booking_types(context.actor_id, context.tenant_id)
    ]


@router.post(f"{W}/booking-types/{{booking_type_id}}/status", response_model=BookingTypeOut)
def set_booking_type_status(
    booking_type_id: uuid.UUID,
    body: StatusIn,
    context: RequestContext = _require(perms.BOOKING_TYPES_MANAGE),
):
    view = scheduling.set_booking_type_status(
        context.actor_id, context.tenant_id, booking_type_id, body.status
    )
    return BookingTypeOut(**view.__dict__)


@router.delete(f"{W}/booking-types/{{booking_type_id}}", status_code=status.HTTP_204_NO_CONTENT)
def delete_booking_type(
    booking_type_id: uuid.UUID, context: RequestContext = _require(perms.BOOKING_TYPES_MANAGE)
) -> None:
    scheduling.delete_booking_type(context.actor_id, context.tenant_id, booking_type_id)


@router.get(f"{W}/appointments", response_model=list[AppointmentOut])
def list_appointments(
    start: AwareDatetime = Query(alias="from"),
    end: AwareDatetime = Query(alias="to"),
    context: RequestContext = _require(perms.APPOINTMENTS_READ),
):
    items = scheduling.list_appointments(context.actor_id, context.tenant_id, start, end)
    return [AppointmentOut(**a.__dict__) for a in items]


@router.post(
    f"{W}/appointments", status_code=status.HTTP_201_CREATED, response_model=AppointmentOut
)
def create_appointment(
    body: AppointmentIn, context: RequestContext = _require(perms.APPOINTMENTS_MANAGE)
):
    view = scheduling.create_appointment(
        context.actor_id,
        context.tenant_id,
        host_user_id=body.host_user_id,
        contact_id=body.contact_id,
        starts_at=body.starts_at,
        duration_minutes=body.duration_minutes,
        time_zone=body.time_zone,
    )
    return AppointmentOut(**view.__dict__)


@router.post(f"{W}/appointments/{{appointment_id}}/cancel", response_model=AppointmentOut)
def cancel_appointment(
    appointment_id: uuid.UUID, context: RequestContext = _require(perms.APPOINTMENTS_MANAGE)
):
    view = scheduling.cancel_appointment(context.actor_id, context.tenant_id, appointment_id)
    return AppointmentOut(**view.__dict__)


# --- Public ------------------------------------------------------------------------------


def _known_shape(public_key: str) -> None:
    if not forms.valid_public_key(public_key):
        raise scheduling.BookingTypeNotFoundError()


def _client(request: Request) -> str:
    return client_bucket(request.client.host if request.client else None)


def _daily_cap() -> int:
    raw = os.environ.get("LEADSTACK_BOOKING_DAILY_PUBLIC", "")
    return max(1, int(raw)) if raw.isdigit() else scheduling.DEFAULT_DAILY_PUBLIC_BOOKINGS


@public_router.get("/public/booking/{public_key}", response_model=PublicBookingOut)
async def get_public_booking(
    public_key: str,
    request: Request,
    response: Response,
    first_day: date = Query(alias="from"),
    last_day: date = Query(alias="to"),
):
    _known_shape(public_key)
    client = _client(request)
    await limit_key(f"booking_view:{client}", per_hour=PAGE_VIEWS_PER_CLIENT_PER_HOUR)
    page = await run_in_threadpool(scheduling.public_page, public_key, client, first_day, last_day)
    response.headers["Cache-Control"] = "no-store"  # the token is per client and request
    return PublicBookingOut(**page.__dict__)


@public_router.post("/public/booking/{public_key}", response_model=BookingAccepted)
async def book(public_key: str, body: BookingIn, request: Request, background: BackgroundTasks):
    # The per-client limit on all public POSTs already ran in
    # PublicBodyGuardMiddleware. Booking-specific quotas are consumed only
    # after the token and contact details passed (as for forms), so junk
    # requests cannot lock real bookers out of a page.
    _known_shape(public_key)
    client = _client(request)
    request_data = scheduling.BookingRequest(
        starts_at=body.starts_at,
        name=body.name,
        email=body.email,
        phone=body.phone,
        consent=body.consent,
        token=body.booking_token,
        honeypot=body.website,
    )
    prepared = await run_in_threadpool(scheduling.prepare_booking, public_key, client, request_data)
    await limit_key(f"booking_client:{client}", per_hour=BOOKINGS_PER_CLIENT_PER_HOUR)
    await limit_key(f"booking_accept:{public_key}", per_hour=BOOKINGS_PER_TYPE_PER_HOUR)
    appointment_id = await run_in_threadpool(
        scheduling.store_booking, prepared, daily_cap=_daily_cap()
    )
    background.add_task(dispatch_events, prepared.tenant_id)  # contact.created outbox
    return BookingAccepted(appointment_id=appointment_id, starts_at=body.starts_at)
