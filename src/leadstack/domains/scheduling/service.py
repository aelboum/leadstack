"""Booking types, availability, public booking and appointments (ADR-0008).

Times: availability rules are local wall-clock minutes in the booking
type's IANA time zone; appointments are stored as UTC instants. Slot
computation is `slots.slots()` (pure, DST-safe).

No double booking: every write that creates a `booked` appointment takes a
per-host advisory lock, re-reads the host's overlapping appointments (with
the buffer) in the same transaction and refuses on overlap. A partial
unique index on (tenant, host, starts_at) is the backstop.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core import audit_log
from core.audit_log import ActorType, AuditOutcome
from core.tenancy import TenantNotFoundError, TenantStatus, get_tenant, is_accessible_to_principals
from infra.db import (
    IntegrityError,
    acquire_tenant_advisory_lock,
    select,
    session_scope,
    tenant_session_scope,
)

from leadstack.domains import regional
from leadstack.domains.crm.contact_points import (
    InvalidEmailError,
    InvalidPhoneError,
    normalize_email,
    normalize_phone,
)
from leadstack.domains.crm.models import Contact
from leadstack.domains.crm.service import ContactNotFoundError
from leadstack.domains.forms import service as forms
from leadstack.domains.scheduling import slots as engine
from leadstack.domains.scheduling.models import Appointment, BookingKey, BookingType
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.service import (
    PermissionDeniedError,
    authorize,
    workspace_profile,
)

logger = logging.getLogger(__name__)

MAX_BOOKING_TYPES = 50
MAX_RULES = 28
MAX_LIST_DAYS = 62
DEFAULT_DAILY_PUBLIC_BOOKINGS = 500  # per workspace, rolling 24 h
DEFAULT_DAILY_BOOKINGS_PER_TYPE = 100  # one page cannot take the whole workspace cap
QUOTA_LOCK = "leadstack.booking_quota"
MAX_LISTED = 1000
_TOKEN_SCOPE = "booking:"  # noqa: S105 -- a key prefix; booking tokens cannot be used on forms


class SchedulingError(Exception):
    code = "scheduling_error"


class InvalidBookingTypeError(SchedulingError):
    code = "invalid_booking_type"


class BookingTypeNotFoundError(SchedulingError):
    code = "booking_type_not_found"


class BookingTypeLimitError(SchedulingError):
    code = "booking_type_limit"


class InvalidHostError(SchedulingError):
    code = "invalid_host"


class SlotUnavailableError(SchedulingError):
    code = "slot_unavailable"


class InvalidRangeError(SchedulingError):
    code = "invalid_range"


class RangeTooLargeError(SchedulingError):
    code = "range_too_large"


class AppointmentNotFoundError(SchedulingError):
    code = "appointment_not_found"


class BookingRejectedError(SchedulingError):
    code = "booking_rejected"


class BookingQuotaExceededError(SchedulingError):
    code = "booking_quota_exceeded"


@dataclass(frozen=True)
class BookingTypeView:
    booking_type_id: uuid.UUID
    name: str
    public_key: str
    host_user_id: uuid.UUID
    duration_minutes: int
    buffer_minutes: int
    min_notice_minutes: int
    horizon_days: int
    time_zone: str
    rules: list[dict[str, Any]]
    status: str


@dataclass(frozen=True)
class AppointmentView:
    appointment_id: uuid.UUID
    booking_type_id: uuid.UUID | None
    host_user_id: uuid.UUID
    contact_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    time_zone: str
    status: str
    source: str


@dataclass(frozen=True)
class PublicBookingPage:
    name: str
    duration_minutes: int
    time_zone: str
    slots: list[datetime]
    booking_token: str


def _type_view(row: BookingType) -> BookingTypeView:
    return BookingTypeView(
        row.id, row.name, row.public_key, row.host_user_id, row.duration_minutes,
        row.buffer_minutes, row.min_notice_minutes, row.horizon_days, row.time_zone,
        list(row.rules), row.status,
    )  # fmt: skip


def _appointment_view(row: Appointment) -> AppointmentView:
    return AppointmentView(row.id, row.booking_type_id, row.host_user_id, row.contact_id,
                           row.starts_at, row.ends_at, row.time_zone, row.status,
                           row.source)  # fmt: skip


def _audit(tenant_id, actor_type, actor_id, action: str, contact_id, metadata=None) -> None:
    audit_log.record(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_user_id=actor_id,
        action=action,
        resource_type="leadstack.contact",
        resource_id=str(contact_id),
        outcome=AuditOutcome.SUCCESS,
        metadata=metadata,
    )


def _audit_type(tenant_id, actor_id, action: str, booking_type_id) -> None:
    audit_log.record(
        tenant_id=tenant_id,
        actor_type=ActorType.USER,
        actor_user_id=actor_id,
        action=action,
        resource_type="leadstack.booking_type",
        resource_id=str(booking_type_id),
        outcome=AuditOutcome.SUCCESS,
    )


# --- Validation ------------------------------------------------------------------------


def validate_rules(rules: list[dict[str, Any]]) -> list[dict[str, int]]:
    """Weekly windows: {weekday 0-6 (Monday=0), start, end} in minutes after
    local midnight, 5-minute granularity, start < end <= 1440."""
    if not isinstance(rules, list) or not 1 <= len(rules) <= MAX_RULES:
        raise InvalidBookingTypeError()
    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"weekday", "start", "end"}:
            raise InvalidBookingTypeError()
        weekday, start, end = rule["weekday"], rule["start"], rule["end"]
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (weekday, start, end)):
            raise InvalidBookingTypeError()
        if not (0 <= weekday <= 6 and 0 <= start < end <= 1440):
            raise InvalidBookingTypeError()
        if start % 5 or end % 5:
            raise InvalidBookingTypeError()
        cleaned.append({"weekday": weekday, "start": start, "end": end})
    return sorted(cleaned, key=lambda r: (r["weekday"], r["start"]))


def _engine_rules(rules: list[dict[str, Any]]) -> list[engine.WeeklyRule]:
    def clock(minutes: int) -> time:
        # 1440 (end of day) is represented as 23:59:59.999999 for the engine.
        if minutes >= 1440:
            return time.max  # the engine reads this as the next midnight
        return time(minutes // 60, minutes % 60)

    return [engine.WeeklyRule(r["weekday"], clock(r["start"]), clock(r["end"])) for r in rules]


def _require_host(host_user_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    """The host must currently hold appointment access in the workspace
    (checked before any session is opened: authorization opens its own)."""
    try:
        authorize(host_user_id, tenant_id, perms.APPOINTMENTS_READ)
    except PermissionDeniedError:
        raise InvalidHostError() from None


# --- Booking types (members) -------------------------------------------------------------


def create_booking_type(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str,
    host_user_id: uuid.UUID,
    duration_minutes: int,
    buffer_minutes: int,
    min_notice_minutes: int,
    horizon_days: int,
    time_zone: str,
    rules: list[dict[str, Any]],
) -> BookingTypeView:
    authorize(actor_id, tenant_id, perms.BOOKING_TYPES_MANAGE)
    name = name.strip()
    if not name or len(name) > 100:
        raise InvalidBookingTypeError()
    if not (5 <= duration_minutes <= 480 and 0 <= buffer_minutes <= 240):
        raise InvalidBookingTypeError()
    if not (0 <= min_notice_minutes <= 43200 and 1 <= horizon_days <= 90):
        raise InvalidBookingTypeError()
    try:
        regional.validate_time_zone(time_zone)
    except regional.RegionalValueError:
        raise InvalidBookingTypeError() from None
    cleaned = validate_rules(rules)
    _require_host(host_user_id, tenant_id)
    public_key = secrets.token_urlsafe(24)
    with tenant_session_scope(tenant_id) as session:
        acquire_tenant_advisory_lock(session, tenant_id, "leadstack.booking_types")
        count = len(
            session.execute(select(BookingType.id).where(BookingType.tenant_id == tenant_id)).all()
        )
        if count >= MAX_BOOKING_TYPES:
            raise BookingTypeLimitError()
        row = BookingType(tenant_id=tenant_id, name=name, public_key=public_key,
                          host_user_id=host_user_id, duration_minutes=duration_minutes,
                          buffer_minutes=buffer_minutes, min_notice_minutes=min_notice_minutes,
                          horizon_days=horizon_days, time_zone=time_zone, rules=cleaned,
                          status="active", created_by_user_id=actor_id)  # fmt: skip
        session.add(row)
        session.flush()
        session.add(BookingKey(public_key=public_key, tenant_id=tenant_id, booking_type_id=row.id))
        session.flush()
        session.refresh(row)
        view = _type_view(row)
    _audit_type(tenant_id, actor_id, "leadstack.booking_type.created", view.booking_type_id)
    return view


def list_booking_types(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[BookingTypeView]:
    authorize(actor_id, tenant_id, perms.APPOINTMENTS_READ)
    with tenant_session_scope(tenant_id) as session:
        rows = session.execute(
            select(BookingType)
            .where(BookingType.tenant_id == tenant_id)
            .order_by(BookingType.created_at)
        ).scalars()
        return [_type_view(r) for r in rows]


def set_booking_type_status(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, booking_type_id: uuid.UUID, status: str
) -> BookingTypeView:
    authorize(actor_id, tenant_id, perms.BOOKING_TYPES_MANAGE)
    if status not in ("active", "disabled"):
        raise InvalidBookingTypeError()
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(BookingType)
            .where(BookingType.tenant_id == tenant_id, BookingType.id == booking_type_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise BookingTypeNotFoundError()
        row.status, row.updated_at = status, datetime.now(UTC)
        session.flush()
        view = _type_view(row)
    _audit_type(tenant_id, actor_id, f"leadstack.booking_type.{status}", booking_type_id)
    return view


def delete_booking_type(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, booking_type_id: uuid.UUID
) -> None:
    """Existing appointments are kept (their booking type becomes NULL)."""
    authorize(actor_id, tenant_id, perms.BOOKING_TYPES_MANAGE)
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(BookingType).where(
                BookingType.tenant_id == tenant_id, BookingType.id == booking_type_id
            )
        ).scalar_one_or_none()
        if row is None:
            raise BookingTypeNotFoundError()
        key = session.get(BookingKey, row.public_key)
        if key is not None and key.tenant_id == tenant_id:
            session.delete(key)
        session.delete(row)
    _audit_type(tenant_id, actor_id, "leadstack.booking_type.deleted", booking_type_id)


# --- Availability ------------------------------------------------------------------------


def _busy(
    session, tenant_id: uuid.UUID, host_user_id: uuid.UUID, start: datetime, end: datetime
) -> list[engine.Interval]:
    rows = session.execute(
        select(Appointment.starts_at, Appointment.ends_at).where(
            Appointment.tenant_id == tenant_id,
            Appointment.host_user_id == host_user_id,
            Appointment.status == "booked",
            Appointment.starts_at < end,
            Appointment.ends_at > start,
        )
    ).all()
    return [engine.Interval(s, e) for s, e in rows]


def _free_slots(
    session, row: BookingType, first_day: date, last_day: date, now: datetime
) -> list[datetime]:
    zone = ZoneInfo(row.time_zone)
    today = now.astimezone(zone).date()
    last_allowed = today + timedelta(days=row.horizon_days)
    first_day, last_day = max(first_day, today), min(last_day, last_allowed)
    if last_day < first_day:
        return []
    window_start = datetime.combine(first_day, time(0), tzinfo=zone).astimezone(UTC)
    window_end = datetime.combine(last_day + timedelta(days=1), time(0), tzinfo=zone)
    margin = timedelta(minutes=row.buffer_minutes + row.duration_minutes)
    busy = _busy(session, row.tenant_id, row.host_user_id, window_start - margin,
                 window_end.astimezone(UTC) + margin)  # fmt: skip
    return engine.slots(
        rules=_engine_rules(row.rules),
        zone_name=row.time_zone,
        duration_minutes=row.duration_minutes,
        buffer_minutes=row.buffer_minutes,
        first_day=first_day,
        last_day=last_day,
        busy=busy,
        not_before=now + timedelta(minutes=row.min_notice_minutes),
    )


# --- Public booking ----------------------------------------------------------------------


def _resolve(public_key: str) -> tuple[uuid.UUID, uuid.UUID]:
    if not forms.valid_public_key(public_key):
        raise BookingTypeNotFoundError()
    with session_scope() as session:
        key = session.get(BookingKey, public_key)
        if key is None:
            raise BookingTypeNotFoundError()
        tenant_id, booking_type_id = key.tenant_id, key.booking_type_id
    try:
        tenant = get_tenant(tenant_id)
    except TenantNotFoundError:
        raise BookingTypeNotFoundError() from None
    if not is_accessible_to_principals(TenantStatus(tenant.status)):
        raise BookingTypeNotFoundError()
    return tenant_id, booking_type_id


def _active_type(session, tenant_id: uuid.UUID, booking_type_id: uuid.UUID) -> BookingType:
    row = session.execute(
        select(BookingType).where(
            BookingType.tenant_id == tenant_id, BookingType.id == booking_type_id
        )
    ).scalar_one_or_none()
    if row is None or row.status != "active":
        raise BookingTypeNotFoundError()
    return row


def public_page(
    public_key: str, client: str, first_day: date, last_day: date, *, now: datetime | None = None
) -> PublicBookingPage:
    if last_day < first_day or (last_day - first_day).days > engine.MAX_RANGE_DAYS:
        raise InvalidRangeError()
    tenant_id, booking_type_id = _resolve(public_key)
    now = now or datetime.now(UTC)
    with tenant_session_scope(tenant_id) as session:
        host = _active_type(session, tenant_id, booking_type_id).host_user_id
    try:
        _require_host(host, tenant_id)  # outside the session (authorization opens its own)
    except InvalidHostError:
        raise BookingTypeNotFoundError() from None
    with tenant_session_scope(tenant_id) as session:
        row = _active_type(session, tenant_id, booking_type_id)
        free = _free_slots(session, row, first_day, last_day, now)
        return PublicBookingPage(
            name=row.name,
            duration_minutes=row.duration_minutes,
            time_zone=row.time_zone,
            slots=free,
            booking_token=forms.issue_token(_TOKEN_SCOPE + public_key, client),
        )


@dataclass(frozen=True)
class BookingRequest:
    starts_at: datetime
    name: str
    email: str
    phone: str | None
    consent: bool
    token: str
    honeypot: str | None = None  # hidden field; bots fill it


@dataclass(frozen=True)
class PreparedBooking:
    """A booking request that passed every check that needs no quota."""

    public_key: str
    tenant_id: uuid.UUID
    booking_type_id: uuid.UUID
    starts_at: datetime
    first_name: str
    last_name: str
    email: str
    phone: str | None


def prepare_booking(public_key: str, client: str, request: BookingRequest) -> PreparedBooking:
    """Token, consent, honeypot and contact-detail checks, plus host and
    type resolution. Runs before any shared quota is consumed, so junk
    requests cannot exhaust a booking page's limits."""
    if not request.consent or request.honeypot:
        raise BookingRejectedError()
    try:
        forms.check_token(_TOKEN_SCOPE + public_key, client, request.token)
    except forms.SubmissionRejectedError:
        raise BookingRejectedError() from None
    name = " ".join(request.name.split())
    if not name or len(name) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in request.name):
        raise BookingRejectedError()
    tenant_id, booking_type_id = _resolve(public_key)
    profile = workspace_profile(tenant_id)
    try:
        email = normalize_email(request.email)
        phone = normalize_phone(request.phone, profile.country) if request.phone else None
    except (InvalidEmailError, InvalidPhoneError):
        raise BookingRejectedError() from None
    # Host authority is checked before the session (authorization opens its own).
    with tenant_session_scope(tenant_id) as session:
        host = _active_type(session, tenant_id, booking_type_id).host_user_id
    try:
        _require_host(host, tenant_id)
    except InvalidHostError:
        raise BookingTypeNotFoundError() from None  # do not reveal membership changes
    first_name, _, last_name = name.partition(" ")
    return PreparedBooking(public_key, tenant_id, booking_type_id,
                           request.starts_at.astimezone(UTC), first_name, last_name,
                           email, phone)  # fmt: skip


def _is_slot_conflict(exc: IntegrityError) -> bool:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) == "uq_appointments_host_start_booked"


def store_booking(
    prepared: PreparedBooking,
    *,
    daily_cap: int = DEFAULT_DAILY_PUBLIC_BOOKINGS,
    type_daily_cap: int | None = None,
    now: datetime | None = None,
) -> uuid.UUID:
    """Book a free slot. The slot must be one the engine offers right now;
    the overlap check runs under the host lock. Daily caps (per workspace
    and per booking type) are counted under a workspace-wide lock. An
    existing contact (same email) is linked, never modified.

    Lock order: booking quota lock, then the host lock."""
    tenant_id, starts_at = prepared.tenant_id, prepared.starts_at
    if type_daily_cap is None:
        type_daily_cap = DEFAULT_DAILY_BOOKINGS_PER_TYPE
    now = now or datetime.now(UTC)
    try:
        with tenant_session_scope(tenant_id) as session:
            acquire_tenant_advisory_lock(session, tenant_id, QUOTA_LOCK)
            row = _active_type(session, tenant_id, prepared.booking_type_id)
            acquire_tenant_advisory_lock(
                session, tenant_id, f"leadstack.booking:{row.host_user_id}"
            )
            since = now - timedelta(days=1)
            recent = (
                session.execute(
                    select(Appointment.booking_type_id)
                    .where(
                        Appointment.tenant_id == tenant_id,
                        Appointment.source == "public",
                        Appointment.created_at > since,
                    )
                    .limit(daily_cap)
                )
                .scalars()
                .all()
            )
            if len(recent) >= daily_cap or recent.count(row.id) >= type_daily_cap:
                raise BookingQuotaExceededError()
            day = starts_at.astimezone(ZoneInfo(row.time_zone)).date()
            if starts_at not in _free_slots(session, row, day, day, now):
                raise SlotUnavailableError()
            contact_id, _created = forms.match_or_create_contact(
                session,
                tenant_id,
                {"email": prepared.email, "first_name": prepared.first_name,
                 "last_name": prepared.last_name}
                | ({"phone": prepared.phone} if prepared.phone else {}),
                f"booking:{row.name}",
                row.host_user_id,
            )  # fmt: skip
            appointment = Appointment(tenant_id=tenant_id, booking_type_id=row.id,
                                      host_user_id=row.host_user_id, contact_id=contact_id,
                                      starts_at=starts_at,
                                      ends_at=starts_at + timedelta(minutes=row.duration_minutes),
                                      time_zone=row.time_zone, status="booked",
                                      source="public")  # fmt: skip
            session.add(appointment)
            session.flush()
            appointment_id = appointment.id
    except IntegrityError as exc:
        if _is_slot_conflict(exc):
            raise SlotUnavailableError() from None  # backstop: unique (host, start)
        raise
    _audit(tenant_id, ActorType.SYSTEM, None, "leadstack.appointment.booked", contact_id,
           {"appointment_id": str(appointment_id), "source": "public"})  # fmt: skip
    return appointment_id


def book(
    public_key: str,
    client: str,
    request: BookingRequest,
    *,
    daily_cap: int = DEFAULT_DAILY_PUBLIC_BOOKINGS,
    now: datetime | None = None,
) -> uuid.UUID:
    """prepare_booking + store_booking (the route rate limits in between)."""
    return store_booking(prepare_booking(public_key, client, request), daily_cap=daily_cap, now=now)


# --- Appointments (members) --------------------------------------------------------------


def list_appointments(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, start: datetime, end: datetime
) -> list[AppointmentView]:
    authorize(actor_id, tenant_id, perms.APPOINTMENTS_READ)
    if end <= start or end - start > timedelta(days=MAX_LIST_DAYS):
        raise InvalidRangeError()
    with tenant_session_scope(tenant_id) as session:
        rows = (
            session.execute(
                select(Appointment)
                .where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.starts_at < end,
                    Appointment.ends_at > start,
                )
                .order_by(Appointment.starts_at, Appointment.id)
                .limit(MAX_LISTED + 1)
            )
            .scalars()
            .all()
        )
        if len(rows) > MAX_LISTED:
            raise RangeTooLargeError()  # never silently truncated: narrow the range
        return [_appointment_view(r) for r in rows]


def create_appointment(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    host_user_id: uuid.UUID,
    contact_id: uuid.UUID,
    starts_at: datetime,
    duration_minutes: int,
    time_zone: str,
) -> AppointmentView:
    """Manual booking by a member: not limited to availability rules, but
    never overlapping another booked appointment of the host."""
    authorize(actor_id, tenant_id, perms.APPOINTMENTS_MANAGE)
    if not 5 <= duration_minutes <= 480:
        raise InvalidBookingTypeError()
    try:
        regional.validate_time_zone(time_zone)
    except regional.RegionalValueError:
        raise InvalidBookingTypeError() from None
    _require_host(host_user_id, tenant_id)
    start = starts_at.astimezone(UTC)
    end = start + timedelta(minutes=duration_minutes)
    try:
        with tenant_session_scope(tenant_id) as session:
            acquire_tenant_advisory_lock(session, tenant_id, f"leadstack.booking:{host_user_id}")
            exists = session.execute(
                select(Contact.id).where(Contact.tenant_id == tenant_id, Contact.id == contact_id)
            ).first()
            if exists is None:
                raise ContactNotFoundError()
            if _busy(session, tenant_id, host_user_id, start, end):
                raise SlotUnavailableError()
            row = Appointment(tenant_id=tenant_id, host_user_id=host_user_id,
                              contact_id=contact_id, starts_at=start, ends_at=end,
                              time_zone=time_zone, status="booked", source="manual",
                              created_by_user_id=actor_id)  # fmt: skip
            session.add(row)
            session.flush()
            session.refresh(row)
            view = _appointment_view(row)
    except IntegrityError as exc:
        if _is_slot_conflict(exc):
            raise SlotUnavailableError() from None
        raise
    _audit(tenant_id, ActorType.USER, actor_id, "leadstack.appointment.booked", contact_id,
           {"appointment_id": str(view.appointment_id), "source": "manual"})  # fmt: skip
    return view


def cancel_appointment(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, appointment_id: uuid.UUID
) -> AppointmentView:
    authorize(actor_id, tenant_id, perms.APPOINTMENTS_MANAGE)
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(Appointment)
            .where(Appointment.tenant_id == tenant_id, Appointment.id == appointment_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise AppointmentNotFoundError()
        changed = row.status != "cancelled"
        if changed:
            row.status, row.cancelled_at, row.cancelled_by_user_id = (
                "cancelled", datetime.now(UTC), actor_id,
            )  # fmt: skip
        session.flush()
        view = _appointment_view(row)
    if changed:
        _audit(tenant_id, ActorType.USER, actor_id, "leadstack.appointment.cancelled",
               view.contact_id, {"appointment_id": str(appointment_id)})  # fmt: skip
    return view
