"""Contacts and companies.

Every operation authorizes the verified actor through `core.rbac.can()` and
runs inside `tenant_session_scope()` with the verified tenant. Every query
also filters on `tenant_id` explicitly (defense in depth on top of forced
RLS). Audit events carry identifiers and changed field names only, never
personal data.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from core import audit_log
from core.audit_log import ActorType, AuditOutcome
from infra.db import (
    IntegrityError,
    acquire_tenant_advisory_lock,
    delete,
    select,
    tenant_session_scope,
)

from leadstack.domains import regional
from leadstack.domains.automation import events
from leadstack.domains.crm.activity_models import ContactTag
from leadstack.domains.crm.contact_points import (
    InvalidPhoneError,
    normalize_email,
    normalize_phone,
)
from leadstack.domains.crm.models import Company, Contact
from leadstack.domains.messaging.models import CONSENT_LOCK, ContactConsent
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.service import (
    authorize,
    is_active_member,
    workspace_country,
)

MAX_PAGE = 100
MAX_QUERY = 100
UNSET: Any = object()  # distinguishes "not provided" from an explicit null


class CrmError(Exception):
    code = "crm_error"


class ContactNotFoundError(CrmError):
    code = "contact_not_found"


class CompanyNotFoundError(CrmError):
    code = "company_not_found"


class UnknownCompanyReferenceError(CrmError):
    code = "unknown_company"


class InvalidOwnerError(CrmError):
    code = "invalid_owner"


class EmptyContactError(CrmError):
    code = "empty_contact"


class InvalidCursorError(CrmError):
    code = "invalid_cursor"


class FieldTooLongError(CrmError):
    code = "field_too_long"


class CompanyNameRequiredError(CrmError):
    code = "company_name_required"


class InvalidWebsiteError(CrmError):
    code = "invalid_website"


# --- Views ---------------------------------------------------------------------


@dataclass(frozen=True)
class ContactView:
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
    custom_fields: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CompanyView:
    company_id: uuid.UUID
    name: str
    website: str | None
    phone_e164: str | None
    country: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None


def _contact_view(row: Contact) -> ContactView:
    return ContactView(
        contact_id=row.id,
        first_name=row.first_name,
        last_name=row.last_name,
        email=row.email,
        phone_e164=row.phone_e164,
        company_id=row.company_id,
        job_title=row.job_title,
        source=row.source,
        owner_user_id=row.owner_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        custom_fields=dict(row.custom_fields or {}),
    )


def _company_view(row: Company) -> CompanyView:
    return CompanyView(
        company_id=row.id,
        name=row.name,
        website=row.website,
        phone_e164=row.phone_e164,
        country=row.country,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --- Shared helpers ----------------------------------------------------------------


def _audit(
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    resource: str,
    rid: object,
    fields_changed: list[str] | None = None,
) -> None:
    audit_log.record(
        tenant_id=tenant_id,
        actor_type=ActorType.USER,
        actor_user_id=actor_id,
        action=action,
        resource_type=resource,
        resource_id=str(rid),
        outcome=AuditOutcome.SUCCESS,
        metadata={"fields": sorted(fields_changed)} if fields_changed is not None else None,
    )


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps({"c": created_at.isoformat(), "i": str(row_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        data = json.loads(base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()))
        created_at, row_id = datetime.fromisoformat(data["c"]), uuid.UUID(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise InvalidCursorError() from None
    if created_at.tzinfo is None:
        raise InvalidCursorError()
    return created_at, row_id


def _page(model: type[Contact] | type[Company], statement, limit: int, cursor: str | None):
    """Newest first, keyset on (created_at, id)."""
    limit = max(1, min(limit, MAX_PAGE))
    if cursor:
        created_at, row_id = _decode_cursor(cursor)
        statement = statement.where(
            (model.created_at < created_at)
            | ((model.created_at == created_at) & (model.id < row_id))
        )
    return statement.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1), limit


def _finish[R, V](rows: list[R], limit: int, view: Callable[[R], V]) -> Page[V]:
    more = len(rows) > limit
    rows = rows[:limit]
    last = rows[-1] if rows else None
    next_cursor = (
        _encode_cursor(last.created_at, last.id)  # type: ignore[attr-defined]
        if more and last is not None
        else None
    )
    return Page(items=tuple(view(r) for r in rows), next_cursor=next_cursor)


def _clean(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) > max_length:
        raise FieldTooLongError()
    return value or None


# --- Companies --------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyInput:
    name: Any = UNSET
    website: Any = UNSET
    phone: Any = UNSET
    country: Any = UNSET
    notes: Any = UNSET

    def provided(self) -> dict[str, Any]:
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not UNSET
        }


def _company_values(tenant_id: uuid.UUID, data: CompanyInput) -> dict[str, Any]:
    values: dict[str, Any] = {}
    provided = data.provided()
    if "name" in provided:
        name = _clean(provided["name"], max_length=200)
        if not name:
            raise CompanyNameRequiredError()
        values["name"] = name
    if "website" in provided:
        values["website"] = _website(provided["website"])
    if "phone" in provided:
        values["phone_e164"] = (
            normalize_phone(provided["phone"], workspace_country(tenant_id))
            if provided["phone"]
            else None
        )
    if "country" in provided:
        values["country"] = (
            regional.validate_country(provided["country"]) if provided["country"] else None
        )
    if "notes" in provided:
        values["notes"] = _clean(provided["notes"], max_length=10_000)
    return values


def _website(value: str | None) -> str | None:
    """Only absolute http(s) URLs are stored: a `javascript:` or `data:`
    value would become stored XSS the moment any client renders a link."""
    text = _clean(value, max_length=2048)
    if text is None:
        return None
    if " " in text:
        raise InvalidWebsiteError()
    parts = urlsplit(text)
    if not parts.scheme:
        parts = urlsplit(f"https://{text}")  # bare domain: "example.ma"
    elif parts.scheme.lower() not in ("http", "https") or not text.lower().startswith(
        f"{parts.scheme.lower()}://"
    ):
        # Any other scheme (javascript:, data:, ftp:, mailto:, ...) is refused.
        raise InvalidWebsiteError()
    try:
        parts.port  # noqa: B018 -- validates the port
    except ValueError:
        raise InvalidWebsiteError() from None
    if not parts.hostname:
        raise InvalidWebsiteError()
    return parts.geturl()


def create_company(actor_id: uuid.UUID, tenant_id: uuid.UUID, data: CompanyInput) -> CompanyView:
    authorize(actor_id, tenant_id, perms.COMPANIES_CREATE)
    if data.name is UNSET:
        raise CompanyNameRequiredError()
    values = _company_values(tenant_id, data)
    with tenant_session_scope(tenant_id) as session:
        row = Company(tenant_id=tenant_id, created_by_user_id=actor_id, **values)
        session.add(row)
        session.flush()
        session.refresh(row)
        view = _company_view(row)
    _audit(tenant_id, actor_id, "leadstack.company.created", "leadstack.company", view.company_id)
    return view


def _get_company_row(session, tenant_id: uuid.UUID, company_id: uuid.UUID, *, lock=False):
    statement = select(Company).where(Company.tenant_id == tenant_id, Company.id == company_id)
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).scalar_one_or_none()
    if row is None:
        raise CompanyNotFoundError()
    return row


def get_company(actor_id: uuid.UUID, tenant_id: uuid.UUID, company_id: uuid.UUID) -> CompanyView:
    authorize(actor_id, tenant_id, perms.COMPANIES_READ)
    with tenant_session_scope(tenant_id) as session:
        return _company_view(_get_company_row(session, tenant_id, company_id))


def list_companies(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, *, q: str | None, limit: int, cursor: str | None
) -> Page[CompanyView]:
    authorize(actor_id, tenant_id, perms.COMPANIES_READ)
    statement = select(Company).where(Company.tenant_id == tenant_id)
    if q and q.strip():
        statement = statement.where(Company.name.ilike(_like(q.strip()[:MAX_QUERY]), escape="\\"))
    statement, limit = _page(Company, statement, limit, cursor)
    with tenant_session_scope(tenant_id) as session:
        rows = list(session.execute(statement).scalars())
        return _finish(rows, limit, _company_view)


def update_company(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, company_id: uuid.UUID, data: CompanyInput
) -> CompanyView:
    authorize(actor_id, tenant_id, perms.COMPANIES_UPDATE)
    values = _company_values(tenant_id, data)
    with tenant_session_scope(tenant_id) as session:
        row = _get_company_row(session, tenant_id, company_id, lock=True)
        for name, value in values.items():
            setattr(row, name, value)
        if values:
            row.updated_at = datetime.now(UTC)
        session.flush()
        session.refresh(row)
        view = _company_view(row)
    if values:
        _audit(
            tenant_id,
            actor_id,
            "leadstack.company.updated",
            "leadstack.company",
            company_id,
            list(values),
        )
    return view


def delete_company(actor_id: uuid.UUID, tenant_id: uuid.UUID, company_id: uuid.UUID) -> None:
    """Hard delete. Contacts linked to it keep existing, unlinked (the
    database sets their company to null)."""
    authorize(actor_id, tenant_id, perms.COMPANIES_DELETE)
    with tenant_session_scope(tenant_id) as session:
        session.delete(_get_company_row(session, tenant_id, company_id, lock=True))
    _audit(tenant_id, actor_id, "leadstack.company.deleted", "leadstack.company", company_id)


# --- Contacts ---------------------------------------------------------------------


@dataclass(frozen=True)
class ContactInput:
    first_name: Any = UNSET
    last_name: Any = UNSET
    email: Any = UNSET
    phone: Any = UNSET
    company_id: Any = UNSET
    job_title: Any = UNSET
    source: Any = UNSET
    owner_user_id: Any = UNSET

    def provided(self) -> dict[str, Any]:
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not UNSET
        }


def _prepare_contact(tenant_id: uuid.UUID, data: ContactInput) -> dict[str, Any]:
    """Validate and normalize input *before* any session is opened. Lookups
    that need their own database connection (the workspace country, the
    owner's membership) happen here, so a request never holds one pooled
    connection while waiting for another."""
    values: dict[str, Any] = {}
    provided = data.provided()
    for name, limit in (
        ("first_name", 100),
        ("last_name", 100),
        ("job_title", 150),
        ("source", 64),
    ):
        if name in provided:
            values[name] = _clean(provided[name], max_length=limit)
    if "email" in provided:
        values["email"] = normalize_email(provided["email"]) if provided["email"] else None
    if "phone" in provided:
        raw = provided["phone"]
        values["phone_e164"] = normalize_phone(raw, workspace_country(tenant_id)) if raw else None
    if "company_id" in provided:
        values["company_id"] = provided["company_id"]
    if "owner_user_id" in provided:
        owner = provided["owner_user_id"]
        if owner is not None and not is_active_member(tenant_id, owner):
            raise InvalidOwnerError()
        values["owner_user_id"] = owner
    return values


def _check_company(session, tenant_id: uuid.UUID, values: dict[str, Any]) -> None:
    company_id = values.get("company_id")
    if company_id is None:
        return
    exists = session.execute(
        select(Company.id).where(Company.tenant_id == tenant_id, Company.id == company_id)
    ).scalar_one_or_none()
    if exists is None:
        raise UnknownCompanyReferenceError()


def record_contact_created(session, row: Contact) -> None:
    """Outbox event in the creating transaction (non-personal attributes only)."""
    events.emit(
        session,
        row.tenant_id,
        events.CONTACT_CREATED,
        row.id,
        {"source": row.source, "has_email": bool(row.email), "has_phone": bool(row.phone_e164)},
    )


def _identifying(row_or_values: dict[str, Any]) -> bool:
    return any(
        row_or_values.get(name) for name in ("first_name", "last_name", "email", "phone_e164")
    )


def _get_contact_row(session, tenant_id: uuid.UUID, contact_id: uuid.UUID, *, lock=False):
    statement = select(Contact).where(Contact.tenant_id == tenant_id, Contact.id == contact_id)
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).scalar_one_or_none()
    if row is None:
        raise ContactNotFoundError()
    return row


def create_contact(actor_id: uuid.UUID, tenant_id: uuid.UUID, data: ContactInput) -> ContactView:
    authorize(actor_id, tenant_id, perms.CONTACTS_CREATE)
    values = _prepare_contact(tenant_id, data)
    if not _identifying(values):
        raise EmptyContactError()
    try:
        with tenant_session_scope(tenant_id) as session:
            _check_company(session, tenant_id, values)
            row = Contact(tenant_id=tenant_id, created_by_user_id=actor_id, **values)
            session.add(row)
            session.flush()
            record_contact_created(session, row)
            session.refresh(row)
            view = _contact_view(row)
    except IntegrityError:
        # The company was deleted between the check and the insert.
        raise UnknownCompanyReferenceError() from None
    _audit(tenant_id, actor_id, "leadstack.contact.created", "leadstack.contact", view.contact_id)
    return view


def get_contact(actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> ContactView:
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    with tenant_session_scope(tenant_id) as session:
        return _contact_view(_get_contact_row(session, tenant_id, contact_id))


def list_contacts(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    q: str | None,
    company_id: uuid.UUID | None,
    limit: int,
    cursor: str | None,
    tag_id: uuid.UUID | None = None,
    owner_user_id: uuid.UUID | None = None,
) -> Page[ContactView]:
    """Search matches first name, last name, email or phone (substring,
    case-insensitive). Results are newest first."""
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    statement = select(Contact).where(Contact.tenant_id == tenant_id)
    if company_id is not None:
        statement = statement.where(Contact.company_id == company_id)
    if owner_user_id is not None:
        statement = statement.where(Contact.owner_user_id == owner_user_id)
    if tag_id is not None:
        statement = statement.where(
            Contact.id.in_(
                select(ContactTag.contact_id).where(
                    ContactTag.tenant_id == tenant_id, ContactTag.tag_id == tag_id
                )
            )
        )
    if q and q.strip():
        text = q.strip()[:MAX_QUERY]
        pattern = _like(text)
        condition = (
            Contact.first_name.ilike(pattern, escape="\\")
            | Contact.last_name.ilike(pattern, escape="\\")
            | Contact.email.ilike(pattern, escape="\\")
            | Contact.phone_e164.ilike(pattern, escape="\\")
        )
        phone = _phone_query(tenant_id, text)
        if phone is not None:
            condition = condition | (Contact.phone_e164 == phone)
        statement = statement.where(condition)
    statement, limit = _page(Contact, statement, limit, cursor)
    with tenant_session_scope(tenant_id) as session:
        rows = list(session.execute(statement).scalars())
        return _finish(rows, limit, _contact_view)


def _phone_query(tenant_id: uuid.UUID, text: str) -> str | None:
    """A search typed like a phone number ("06 12 34 56 78") also matches
    its E.164 form, interpreted for the workspace's country."""
    if sum(c.isdigit() for c in text) < 6 or any(c.isalpha() for c in text):
        return None
    try:
        return normalize_phone(text, workspace_country(tenant_id))
    except InvalidPhoneError:
        return None


def update_contact(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, data: ContactInput
) -> ContactView:
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    values = _prepare_contact(tenant_id, data)
    try:
        with tenant_session_scope(tenant_id) as session:
            if "email" in values:
                # Lock order shared with messaging: consent lock, then rows.
                acquire_tenant_advisory_lock(session, tenant_id, CONSENT_LOCK)
            row = _get_contact_row(session, tenant_id, contact_id, lock=True)
            _check_company(session, tenant_id, values)
            merged = {
                name: values.get(name, getattr(row, name))
                for name in ("first_name", "last_name", "email", "phone_e164")
            }
            if not _identifying(merged):
                raise EmptyContactError()
            address_changed = (
                "email" in values and (values["email"] or "").lower() != (row.email or "").lower()
            )
            for name, value in values.items():
                setattr(row, name, value)
            if values:
                row.updated_at = datetime.now(UTC)
            if address_changed:
                # Consent covered the previous address, not the new one.
                session.execute(
                    delete(ContactConsent).where(
                        ContactConsent.tenant_id == tenant_id,
                        ContactConsent.contact_id == contact_id,
                    )
                )
            session.flush()
            session.refresh(row)
            view = _contact_view(row)
    except IntegrityError:
        raise UnknownCompanyReferenceError() from None
    changed = sorted({"phone" if k.startswith("phone") else k for k in values})
    if changed:
        _audit(
            tenant_id,
            actor_id,
            "leadstack.contact.updated",
            "leadstack.contact",
            contact_id,
            changed,
        )
    return view


def delete_contact(actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> None:
    """Hard delete (erasure). The audit trail keeps only the identifier."""
    authorize(actor_id, tenant_id, perms.CONTACTS_DELETE)
    with tenant_session_scope(tenant_id) as session:
        session.delete(_get_contact_row(session, tenant_id, contact_id, lock=True))
    _audit(tenant_id, actor_id, "leadstack.contact.deleted", "leadstack.contact", contact_id)
