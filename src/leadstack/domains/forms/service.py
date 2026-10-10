"""Lead-capture forms.

Management (authenticated, tenant routes) and the public submission path
(unauthenticated). The public path:

1. resolves the form by its unguessable public key through the
   identifier-only `form_keys` index (no tenant context exists yet);
2. refuses inactive forms and workspaces that no longer admit principals;
3. verifies a signed form token (minimum fill time, maximum age), a
   honeypot field, and size limits -- spam controls that need no CAPTCHA
   provider;
4. validates only the fields the form defines and requires explicit consent;
5. creates the contact, or links an existing contact with the same email,
   and records the submission with a snapshot of the consent text.

No IP address or user agent is stored. Rate limits (per client and per
form) are applied by the HTTP layer before this module is called.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

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
from infra.secrets import get_secrets_provider

from leadstack.db_types import lower
from leadstack.domains.automation import events
from leadstack.domains.crm.contact_points import (
    InvalidEmailError,
    InvalidPhoneError,
    normalize_email,
    normalize_phone,
)
from leadstack.domains.crm.models import Contact
from leadstack.domains.crm.service import record_contact_created
from leadstack.domains.forms.models import Form, FormKey, FormSubmission
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.service import authorize, workspace_country

FIELD_TYPES = ("text", "email", "phone", "textarea")
CONTACT_TARGETS = ("first_name", "last_name", "email", "phone", "company", "message")
MAX_FIELDS = 20
MAX_FORMS = 50
MAX_VALUE = {"text": 200, "email": 254, "phone": 32, "textarea": 5000}
MIN_FILL_SECONDS = 2
MAX_TOKEN_AGE_SECONDS = 2 * 60 * 60
HONEYPOT_FIELD = "website"  # must stay empty; bots fill it
TOKEN_SECRET_NAME = "LEADSTACK_FORM_TOKEN_SECRET"  # noqa: S105  # pragma: allowlist secret (a name)
_KEY = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class FormError(Exception):
    code = "form_error"


class FormNotFoundError(FormError):
    code = "form_not_found"


class InvalidFormDefinitionError(FormError):
    code = "invalid_form"


class SubmissionRejectedError(FormError):
    """Invalid input the submitter can fix (shown to them)."""

    code = "invalid_submission"


class FormLimitError(FormError):
    code = "form_limit_reached"


class FormConfigurationError(RuntimeError):
    """The token secret is missing: fail closed, never accept unsigned forms."""


# --- Definitions -------------------------------------------------------------------


@dataclass(frozen=True)
class FormView:
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


def _view(row: Form) -> FormView:
    return FormView(
        form_id=row.id,
        name=row.name,
        public_key=row.public_key,
        status=row.status,
        fields=list(row.fields),
        consent_text=row.consent_text,
        success_message=row.success_message,
        redirect_url=row.redirect_url,
        source_label=row.source_label,
        created_at=row.created_at,
    )


def validate_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not 1 <= len(fields) <= MAX_FIELDS:
        raise InvalidFormDefinitionError()
    cleaned, keys, targets = [], set(), set()
    for field in fields:
        key = field.get("key")
        label = str(field.get("label", "")).strip()
        kind = field.get("type")
        target = field.get("maps_to")
        if (
            not isinstance(key, str)
            or not _FIELD_KEY.fullmatch(key)
            or key == HONEYPOT_FIELD
            or key in keys
            or not label
            or len(label) > 100
            or kind not in FIELD_TYPES
            or (target is not None and target not in CONTACT_TARGETS)
            or (target is not None and target in targets)
        ):
            raise InvalidFormDefinitionError()
        keys.add(key)
        if target is not None:
            targets.add(target)
        cleaned.append(
            {
                "key": key,
                "label": label,
                "type": kind,
                "required": bool(field.get("required", False)),
                "maps_to": target,
            }
        )
    # A lead must be reachable: at least one email or phone field.
    if not {"email", "phone"} & {f["maps_to"] for f in cleaned}:
        raise InvalidFormDefinitionError()
    return cleaned


def _redirect(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise InvalidFormDefinitionError()  # https only: never javascript:, data:, http
    return parts.geturl()


def create_form(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str,
    fields: list[dict[str, Any]],
    consent_text: str,
    success_message: str | None,
    redirect_url: str | None,
    source_label: str | None,
) -> FormView:
    authorize(actor_id, tenant_id, perms.FORMS_MANAGE)
    name, consent_text = name.strip(), consent_text.strip()
    if not name or len(name) > 100 or not consent_text or len(consent_text) > 2000:
        raise InvalidFormDefinitionError()
    cleaned = validate_fields(fields)
    redirect = _redirect(redirect_url)
    public_key = secrets.token_urlsafe(24)  # 32 chars, ~192 bits
    try:
        with tenant_session_scope(tenant_id) as session:
            acquire_tenant_advisory_lock(session, tenant_id, "leadstack.forms")
            count = len(session.execute(select(Form.id).where(Form.tenant_id == tenant_id)).all())
            if count >= MAX_FORMS:
                raise FormLimitError()
            row = Form(
                tenant_id=tenant_id,
                name=name,
                public_key=public_key,
                fields=cleaned,
                consent_text=consent_text,
                success_message=(success_message or "").strip()[:500] or None,
                redirect_url=redirect,
                source_label=(source_label or f"form:{name}")[:64],
                created_by_user_id=actor_id,
            )
            session.add(row)
            session.flush()
            # Same transaction: a form is never left without its key.
            session.add(FormKey(public_key=public_key, tenant_id=tenant_id, form_id=row.id))
            session.flush()
            session.refresh(row)
            view = _view(row)
    except IntegrityError:
        raise InvalidFormDefinitionError() from None
    _audit(tenant_id, ActorType.USER, actor_id, "leadstack.form.created", view.form_id)
    return view


def list_forms(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[FormView]:
    authorize(actor_id, tenant_id, perms.FORMS_READ)
    with tenant_session_scope(tenant_id) as session:
        rows = session.execute(
            select(Form).where(Form.tenant_id == tenant_id).order_by(Form.created_at)
        ).scalars()
        return [_view(r) for r in rows]


def set_form_status(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, form_id: uuid.UUID, status: str
) -> FormView:
    authorize(actor_id, tenant_id, perms.FORMS_MANAGE)
    if status not in ("active", "disabled"):
        raise InvalidFormDefinitionError()
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(Form).where(Form.tenant_id == tenant_id, Form.id == form_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise FormNotFoundError()
        row.status = status
        row.updated_at = datetime.now(UTC)
        session.flush()
        view = _view(row)
    _audit(tenant_id, ActorType.USER, actor_id, f"leadstack.form.{status}", form_id)
    return view


def delete_form(actor_id: uuid.UUID, tenant_id: uuid.UUID, form_id: uuid.UUID) -> None:
    """Deletes the form and its submissions; contacts it created remain."""
    authorize(actor_id, tenant_id, perms.FORMS_MANAGE)
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(Form).where(Form.tenant_id == tenant_id, Form.id == form_id)
        ).scalar_one_or_none()
        if row is None:
            raise FormNotFoundError()
        key = session.execute(
            select(FormKey).where(FormKey.tenant_id == tenant_id, FormKey.form_id == form_id)
        ).scalar_one_or_none()
        if key is not None:
            session.delete(key)
        session.delete(row)
    _audit(tenant_id, ActorType.USER, actor_id, "leadstack.form.deleted", form_id)


@dataclass(frozen=True)
class SubmissionView:
    submission_id: uuid.UUID
    contact_id: uuid.UUID | None
    data: dict[str, Any]
    consented_at: datetime
    submitted_at: datetime


def list_submissions(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, form_id: uuid.UUID, *, limit: int
) -> list[SubmissionView]:
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    with tenant_session_scope(tenant_id) as session:
        rows = session.execute(
            select(FormSubmission)
            .where(FormSubmission.tenant_id == tenant_id, FormSubmission.form_id == form_id)
            .order_by(FormSubmission.submitted_at.desc())
            .limit(max(1, min(limit, 100)))
        ).scalars()
        return [
            SubmissionView(r.id, r.contact_id, dict(r.data), r.consented_at, r.submitted_at)
            for r in rows
        ]


# --- Public path -------------------------------------------------------------------

# Total stored size of one submission's values (the database allows 32 KiB;
# leave headroom for JSON encoding).
MAX_SUBMISSION_BYTES = 24 * 1024
# Contacts a workspace's forms may create per rolling 24 hours (spam ceiling).
DEFAULT_DAILY_SUBMISSIONS = 2000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class FormQuotaExceededError(FormError):
    code = "form_quota_exceeded"


@dataclass(frozen=True)
class PublicForm:
    name: str
    fields: list[dict[str, Any]]
    consent_text: str
    success_message: str | None
    redirect_url: str | None
    form_token: str


def valid_public_key(public_key: str) -> bool:
    return bool(_KEY.fullmatch(public_key))


def _secret() -> bytes:
    value = get_secrets_provider().get(TOKEN_SECRET_NAME)
    if not value or len(value) < 32:
        raise FormConfigurationError(f"{TOKEN_SECRET_NAME} must be set (>= 32 characters)")
    return value.encode()


def _sign(public_key: str, client: str, issued_at: int) -> str:
    message = f"{public_key}.{client}.{issued_at}".encode()
    mac = hmac.new(_secret(), message, hashlib.sha256).digest()
    return f"{issued_at}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def issue_token(public_key: str, client: str, *, now: float | None = None) -> str:
    """Bound to the form *and* the client that fetched it: a token copied to
    other addresses is useless, so address rotation also needs fresh,
    rate-limited GETs."""
    return _sign(public_key, client, int(now if now is not None else time.time()))


def _check_token(public_key: str, client: str, token: str, *, now: float | None = None) -> None:
    try:
        issued_text, _ = token.split(".", 1)
        issued_at = int(issued_text)
    except ValueError:
        raise SubmissionRejectedError() from None
    expected = _sign(public_key, client, issued_at)
    if not hmac.compare_digest(expected.encode(), token.encode()):
        raise SubmissionRejectedError()
    age = (now if now is not None else time.time()) - issued_at
    if not MIN_FILL_SECONDS <= age <= MAX_TOKEN_AGE_SECONDS:
        raise SubmissionRejectedError()


def check_token(public_key: str, client: str, token: str, *, now: float | None = None) -> None:
    """Public form of the anti-bot token check (also used by public booking,
    with a scoped key so tokens are not interchangeable)."""
    _check_token(public_key, client, token, now=now)


def _resolve(public_key: str) -> tuple[uuid.UUID, uuid.UUID]:
    if not valid_public_key(public_key):
        raise FormNotFoundError()
    with session_scope() as session:
        key = session.get(FormKey, public_key)
        if key is None:
            raise FormNotFoundError()
        tenant_id, form_id = key.tenant_id, key.form_id
    try:
        tenant = get_tenant(tenant_id)
    except TenantNotFoundError:
        raise FormNotFoundError() from None
    if not is_accessible_to_principals(TenantStatus(tenant.status)):
        raise FormNotFoundError()  # suspended/closed workspaces accept nothing
    return tenant_id, form_id


def _active_form(session, tenant_id: uuid.UUID, form_id: uuid.UUID) -> Form:
    row = session.execute(
        select(Form).where(Form.tenant_id == tenant_id, Form.id == form_id)
    ).scalar_one_or_none()
    if row is None or row.status != "active":
        raise FormNotFoundError()
    return row


def public_form(public_key: str, client: str) -> PublicForm:
    tenant_id, form_id = _resolve(public_key)
    with tenant_session_scope(tenant_id) as session:
        row = _active_form(session, tenant_id, form_id)
        return PublicForm(
            name=row.name,
            fields=[{k: f[k] for k in ("key", "label", "type", "required")} for f in row.fields],
            consent_text=row.consent_text,
            success_message=row.success_message,
            redirect_url=row.redirect_url,
            form_token=issue_token(public_key, client),
        )


@dataclass(frozen=True)
class SubmissionResult:
    accepted: bool  # False only for silently discarded spam
    success_message: str | None
    redirect_url: str | None
    contact_id: uuid.UUID | None = None
    submission_id: uuid.UUID | None = None


@dataclass(frozen=True)
class PreparedSubmission:
    """A submission that passed every check and only awaits storage."""

    tenant_id: uuid.UUID
    form_id: uuid.UUID
    cleaned: dict[str, str]
    mapped: dict[str, str]
    consent_text: str
    source: str
    form_owner: uuid.UUID
    result: SubmissionResult


def _clean_values(
    definition: list[dict[str, Any]], values: dict[str, Any], country: str | None
) -> dict[str, str]:
    known = {f["key"]: f for f in definition}
    if set(values) - set(known):
        raise SubmissionRejectedError()  # only defined fields are accepted
    cleaned: dict[str, str] = {}
    for key, field in known.items():
        raw = values.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if field["required"]:
                raise SubmissionRejectedError()
            continue
        if not isinstance(raw, str) or len(raw) > MAX_VALUE[field["type"]]:
            raise SubmissionRejectedError()
        if _CONTROL.search(raw):
            raise SubmissionRejectedError()  # NUL and other controls cannot be stored
        text = raw.strip()
        try:
            if field["type"] == "email":
                text = normalize_email(text)
            elif field["type"] == "phone":
                text = normalize_phone(text, country)
        except (InvalidEmailError, InvalidPhoneError):
            raise SubmissionRejectedError() from None
        cleaned[key] = text
    if len(json.dumps(cleaned, ensure_ascii=False).encode()) > MAX_SUBMISSION_BYTES:
        raise SubmissionRejectedError()
    return cleaned


def prepare_submission(
    public_key: str,
    client: str,
    *,
    values: dict[str, Any],
    consent: bool,
    form_token: str,
    honeypot: str | None,
) -> PreparedSubmission | SubmissionResult:
    """Every check, no writes. Returns a `SubmissionResult` (not accepted)
    for honeypot hits, which look like success to the bot. Order matters:
    the form is resolved and must be active *before* the token is checked,
    so a disabled form is indistinguishable from an unknown key."""
    tenant_id, form_id = _resolve(public_key)
    with tenant_session_scope(tenant_id) as session:
        form = _active_form(session, tenant_id, form_id)
        definition = list(form.fields)
        consent_text, source, owner = form.consent_text, form.source_label, form.created_by_user_id
        result = SubmissionResult(True, form.success_message, form.redirect_url)
    _check_token(public_key, client, form_token)
    if honeypot:
        return SubmissionResult(False, result.success_message, result.redirect_url)
    if consent is not True:
        raise SubmissionRejectedError()
    country = workspace_country(tenant_id)  # before opening any write session
    cleaned = _clean_values(definition, values, country)
    mapped: dict[str, str] = {
        str(f["maps_to"]): cleaned[str(f["key"])]
        for f in definition
        if f["maps_to"] and str(f["key"]) in cleaned
    }
    if not (mapped.get("email") or mapped.get("phone")):
        raise SubmissionRejectedError()
    return PreparedSubmission(
        tenant_id, form_id, cleaned, mapped, consent_text, source, owner, result
    )


def store_submission(prepared: PreparedSubmission, *, daily_cap: int) -> SubmissionResult:
    """Writes the contact link and the submission. Consent from a public
    form is *unverified* (anyone can type someone else's address): it must
    never by itself authorize marketing messages; messaging requires a
    verified opt-in (docs/adr/0005-public-form-keys.md)."""
    now = datetime.now(UTC)
    with tenant_session_scope(prepared.tenant_id) as session:
        acquire_tenant_advisory_lock(session, prepared.tenant_id, "leadstack.form_quota")
        recent = len(
            session.execute(
                select(FormSubmission.id)
                .where(
                    FormSubmission.tenant_id == prepared.tenant_id,
                    FormSubmission.submitted_at > now - timedelta(days=1),
                )
                .limit(daily_cap)
            ).all()
        )
        if recent >= daily_cap:
            raise FormQuotaExceededError()
        contact_id, created = _match_or_create_contact(
            session, prepared.tenant_id, prepared.mapped, prepared.source, prepared.form_owner
        )
        submission = FormSubmission(
            tenant_id=prepared.tenant_id,
            form_id=prepared.form_id,
            contact_id=contact_id,
            data=prepared.cleaned,
            consent_text=prepared.consent_text,
            consented_at=now,
        )
        session.add(submission)
        session.flush()
        submission_id = submission.id
        events.emit(
            session,
            prepared.tenant_id,
            events.FORM_SUBMITTED,
            submission_id,
            {
                "form_id": str(prepared.form_id),
                "contact_id": str(contact_id),
                "contact_is_new": created,
            },
        )
    _audit(prepared.tenant_id, ActorType.SYSTEM, None, "leadstack.form.submitted", submission_id,
           {"form_id": str(prepared.form_id), "contact_id": str(contact_id)})  # fmt: skip
    return SubmissionResult(
        True,
        prepared.result.success_message,
        prepared.result.redirect_url,
        contact_id=contact_id,
        submission_id=submission_id,
    )


def _match_or_create_contact(
    session, tenant_id: uuid.UUID, mapped: dict[str, str], source: str, form_owner: uuid.UUID
) -> tuple[uuid.UUID, bool]:
    """An existing contact with the same email (case-insensitive) is linked,
    never overwritten -- a public form must not be able to change existing
    CRM data. Otherwise a new contact is created. The lookup matches the
    `lower(email)` index."""
    email = mapped.get("email")
    if email:
        existing = session.execute(
            select(Contact.id)
            .where(Contact.tenant_id == tenant_id, lower(Contact.email) == email.lower())
            .order_by(Contact.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
    contact = Contact(
        tenant_id=tenant_id,
        first_name=(mapped.get("first_name") or "")[:100] or None,
        last_name=(mapped.get("last_name") or "")[:100] or None,
        email=email,
        phone_e164=mapped.get("phone"),
        source=source[:64],
        # The form's creator is the creator of record; `source` names the form.
        created_by_user_id=form_owner,
    )
    session.add(contact)
    session.flush()
    record_contact_created(session, contact)
    return contact.id, True


match_or_create_contact = _match_or_create_contact  # shared with public booking


def _audit(tenant_id, actor_type, actor_id, action: str, rid, metadata=None) -> None:
    audit_log.record(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_user_id=actor_id,
        action=action,
        resource_type="leadstack.form",
        resource_id=str(rid),
        outcome=AuditOutcome.SUCCESS,
        metadata=metadata,
    )
