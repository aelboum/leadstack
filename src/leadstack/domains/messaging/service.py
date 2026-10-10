"""Email consent (double opt-in) and outbound email.

Consent is recorded per (contact, channel=email) **and bound to the address
it covers** (`address_hash`); changing a contact's email address resets it.
Opt-outs are additionally kept in a per-workspace suppression list keyed by
the address hash, so they survive address edits, contact deletion and
duplicate contacts. Every consent write and the delivery check take the
same per-workspace advisory lock, so they are serialized.

- `pending`   -- requested (e.g. via a public form, which anyone can fill
                 in with any address). NOT sufficient for marketing.
- `granted`   -- confirmed by the address owner through the signed link, or
                 recorded by an admin who obtained it directly (`manual`).
- `withdrawn` -- unsubscribed. A withdrawal made by the contact through the
                 unsubscribe link is permanent for that address in the
                 workspace: no member, admin or owner can reverse it, and
                 no confirmation email is sent to it again. (A
                 contact-initiated re-subscription flow is not built.)

Sending rules (no legal compliance claim; docs/adr/0007-email-messaging.md):
- one-to-one emails written by a member are allowed unless the contact has
  withdrawn consent (business correspondence, not marketing);
- double opt-in confirmations are rate limited per consent record (one per
  24 h, at most 3 ever) and per address across all workspaces (job layer);
- every email carries an unsubscribe link.

Links are signed **at delivery time** and never stored: message bodies kept
in the CRM contain placeholders only, so no member can read and use a
confirmation or unsubscribe link meant for the contact.

Delivery is asynchronous (job) and at-most-once: `queued` -> `sending` under
a row lock before the provider call; a message found in `sending` is never
re-sent (outcome unknown -> `failed`). Provider failures are retried up to
MAX_ATTEMPTS; configuration errors fail immediately.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core import audit_log
from core.audit_log import ActorType, AuditOutcome
from core.email import (
    EmailConfigurationError,
    EmailMessage,
    EmailProviderError,
    InvalidEmailAddressError,
    get_email_config,
)
from core.tenancy import TenantNotFoundError, TenantStatus, get_tenant, is_accessible_to_principals
from infra.db import acquire_tenant_advisory_lock, select, tenant_session_scope

from leadstack.domains.crm.models import Contact
from leadstack.domains.crm.service import ContactNotFoundError
from leadstack.domains.messaging import links
from leadstack.domains.messaging.models import (
    CONSENT_LOCK,
    ContactConsent,
    EmailSuppression,
    Message,
)
from leadstack.domains.messaging.templates import consent_texts
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.service import (
    PermissionDeniedError,
    authorize,
    workspace_display_name,
    workspace_profile,
)
from leadstack.integrations import email as email_adapter

logger = logging.getLogger(__name__)

EMAIL = "email"
MAX_SUBJECT = 200
MAX_BODY = 10_000
MAX_ATTEMPTS = 3
CONFIRMATION_INTERVAL = timedelta(hours=24)
MAX_CONFIRMATIONS = 3
CONFIRM_PLACEHOLDER = "[confirmation link]"
UNSUBSCRIBE_PLACEHOLDER = "[unsubscribe link]"


class MessagingError(Exception):
    code = "messaging_error"


class NoEmailAddressError(MessagingError):
    code = "no_email_address"


class ConsentWithdrawnError(MessagingError):
    code = "consent_withdrawn"


class WithdrawnByContactError(MessagingError):
    """The contact unsubscribed themselves; only they can opt back in."""

    code = "withdrawn_by_contact"


class InvalidMessageError(MessagingError):
    code = "invalid_message"


class MessagingConfigurationError(RuntimeError):
    """Public link origin or signing secret missing: fail closed."""


@dataclass(frozen=True)
class ConsentView:
    channel: str
    status: str
    source: str
    requested_at: datetime
    granted_at: datetime | None
    withdrawn_at: datetime | None
    withdrawn_via: str | None


@dataclass(frozen=True)
class MessageView:
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


def address_hash(address: str) -> str:
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()


def _consent_view(row: ContactConsent) -> ConsentView:
    return ConsentView(row.channel, row.status, row.source, row.requested_at, row.granted_at,
                       row.withdrawn_at, row.withdrawn_via)  # fmt: skip


def _message_view(row: Message) -> MessageView:
    return MessageView(row.id, row.contact_id, row.kind, row.to_address, row.subject, row.body,
                       row.status, row.error_code, row.created_at, row.sent_at)  # fmt: skip


def _audit(tenant_id, actor_type, actor_id, action: str, contact_id, metadata=None) -> None:
    # Resource is the contact, so messaging appears on the contact timeline.
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


def _app_origin() -> str:
    origin = os.environ.get("PUBLIC_APP_ORIGIN", "").rstrip("/")
    insecure_ok = os.environ.get("ENVIRONMENT") in ("development", "test")
    if not origin.startswith("https://") and not (insecure_ok and origin.startswith("http://")):
        raise MessagingConfigurationError("PUBLIC_APP_ORIGIN must be an https origin")
    return origin


def _link(claims: links.LinkClaims) -> str:
    path = "c" if claims.purpose == links.CONFIRM else "u"
    return f"{_app_origin()}/{path}/{links.sign(claims)}"


def _consent_row(session, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> ContactConsent | None:
    return session.execute(
        select(ContactConsent).where(
            ContactConsent.tenant_id == tenant_id,
            ContactConsent.contact_id == contact_id,
            ContactConsent.channel == EMAIL,
        )
    ).scalar_one_or_none()


def _contact_email(session, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> str | None:
    row = session.execute(
        select(Contact.email).where(Contact.tenant_id == tenant_id, Contact.id == contact_id)
    ).first()
    if row is None:
        raise ContactNotFoundError()
    return row[0]


def _lock(session, tenant_id: uuid.UUID) -> None:
    acquire_tenant_advisory_lock(session, tenant_id, CONSENT_LOCK)


def _suppression(session, tenant_id: uuid.UUID, hashed: str) -> EmailSuppression | None:
    return session.execute(
        select(EmailSuppression).where(
            EmailSuppression.tenant_id == tenant_id,
            EmailSuppression.channel == EMAIL,
            EmailSuppression.address_hash == hashed,
        )
    ).scalar_one_or_none()


def _other_withdrawals(session, tenant_id: uuid.UUID, hashed: str, contact_id: uuid.UUID) -> bool:
    return (
        session.execute(
            select(ContactConsent.id)
            .where(
                ContactConsent.tenant_id == tenant_id,
                ContactConsent.address_hash == hashed,
                ContactConsent.status == "withdrawn",
                ContactConsent.contact_id != contact_id,
            )
            .limit(1)
        ).first()
        is not None
    )


def _suppress_address(session, tenant_id: uuid.UUID, hashed: str, reason: str) -> None:
    """Record an opt-out (caller holds the consent lock). A contact's own
    unsubscribe (`link`) is never downgraded to a member record."""
    existing = _suppression(session, tenant_id, hashed)
    if existing is None:
        session.add(EmailSuppression(tenant_id=tenant_id, channel=EMAIL,
                                     address_hash=hashed, reason=reason))  # fmt: skip
    elif reason == "link":
        existing.reason = "link"


# --- Consent -----------------------------------------------------------------------


def get_consent(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID
) -> ConsentView | None:
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    with tenant_session_scope(tenant_id) as session:
        _contact_email(session, tenant_id, contact_id)
        row = _consent_row(session, tenant_id, contact_id)
        return _consent_view(row) if row else None


def record_consent(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, status: str
) -> ConsentView:
    """Members record a withdrawal received outside the link (member role);
    recording a directly obtained grant needs `leadstack.consent:manage`
    (admin) and never overrides an unsubscribe made by the contact."""
    if status not in ("granted", "withdrawn"):
        raise InvalidMessageError()
    needed = perms.CONSENT_MANAGE if status == "granted" else perms.CONTACTS_UPDATE
    authorize(actor_id, tenant_id, needed)
    now = datetime.now(UTC)
    with tenant_session_scope(tenant_id) as session:
        _lock(session, tenant_id)
        address = _contact_email(session, tenant_id, contact_id)
        if not address:
            raise NoEmailAddressError()
        hashed = address_hash(address)
        suppression = _suppression(session, tenant_id, hashed)
        row = _consent_row(session, tenant_id, contact_id)
        if status == "granted" and (
            (suppression is not None and suppression.reason == "link")
            or (row is not None and row.withdrawn_via == "link")
        ):
            raise WithdrawnByContactError()
        if status == "withdrawn" and row is not None and row.status == "withdrawn":
            # Idempotent: never rewrites who withdrew (a contact's own
            # unsubscribe must not be relabelled as a member action).
            _suppress_address(session, tenant_id, hashed, row.withdrawn_via or "member")
            return _consent_view(row)
        if row is None:
            row = ContactConsent(tenant_id=tenant_id, contact_id=contact_id, channel=EMAIL,
                                 status=status, source="manual", address_hash=hashed,
                                 confirmations_sent=0)  # fmt: skip
            session.add(row)
        row.status, row.recorded_by_user_id, row.updated_at = status, actor_id, now
        if status == "granted":
            row.granted_at, row.withdrawn_at, row.withdrawn_via = now, None, None
            row.source, row.address_hash = "manual", hashed
            if suppression is not None and not _other_withdrawals(
                session, tenant_id, hashed, contact_id
            ):
                # A member-recorded opt-out, reversed by an admin -- kept while
                # another contact with this address is still withdrawn.
                session.delete(suppression)
        else:
            row.withdrawn_at, row.withdrawn_via = now, "member"
            _suppress_address(session, tenant_id, hashed, "member")
        session.flush()
        view = _consent_view(row)
    _audit(tenant_id, ActorType.USER, actor_id, f"leadstack.consent.{status}", contact_id,
           {"channel": EMAIL, "source": "manual"})  # fmt: skip
    return view


def request_double_opt_in(
    tenant_id: uuid.UUID, contact_id: uuid.UUID, submission_id: uuid.UUID
) -> uuid.UUID | None:
    """Queue a confirmation email for a form lead. Skipped when consent is
    granted or withdrawn, and rate limited per consent record (one per
    CONFIRMATION_INTERVAL, at most MAX_CONFIRMATIONS): a public form cannot
    be used to flood an address. Returns the queued message id, if any."""
    # Before opening the session; collapsed to one line (it reaches a header).
    workspace = " ".join(workspace_display_name(tenant_id).split())
    texts = consent_texts(workspace_profile(tenant_id).locale)  # the workspace's language
    now = datetime.now(UTC)
    with tenant_session_scope(tenant_id) as session:
        _lock(session, tenant_id)
        address = _contact_email(session, tenant_id, contact_id)
        if not address:
            return None
        if _suppression(session, tenant_id, address_hash(address)) is not None:
            return None
        row = _consent_row(session, tenant_id, contact_id)
        if row is not None and row.status in ("granted", "withdrawn"):
            return None
        if row is not None and (
            row.confirmations_sent >= MAX_CONFIRMATIONS
            or (row.last_requested_at and now - row.last_requested_at < CONFIRMATION_INTERVAL)
        ):
            return None
        if row is None:
            row = ContactConsent(tenant_id=tenant_id, contact_id=contact_id, channel=EMAIL,
                                 status="pending", source="form",
                                 evidence_submission_id=submission_id,
                                 address_hash=address_hash(address),
                                 confirmations_sent=0)  # fmt: skip
            session.add(row)
        row.confirmations_sent += 1
        row.last_requested_at = now
        body = (
            f"{texts.confirm_intro.format(workspace=workspace)}\n"
            f"{texts.confirm_action} {CONFIRM_PLACEHOLDER}\n\n"
            f"{texts.confirm_ignore}\n"
            f"{UNSUBSCRIBE_PLACEHOLDER}\n"
        )
        message = Message(tenant_id=tenant_id, contact_id=contact_id, channel=EMAIL,
                          kind="confirmation", to_address=address,
                          subject=texts.confirm_subject.format(workspace=workspace)[:200],
                          body=body, status="queued", created_at=now)  # fmt: skip
        session.add(message)
        session.flush()
        message_id = message.id
    _audit(tenant_id, ActorType.SYSTEM, None, "leadstack.consent.requested", contact_id,
           {"channel": EMAIL, "source": "form"})  # fmt: skip
    return message_id


def _apply_link(token: str, purpose: str) -> None:
    claims = links.verify(token, purpose)
    try:
        tenant = get_tenant(claims.tenant_id)
    except TenantNotFoundError:
        raise links.InvalidLinkError() from None
    if purpose == links.CONFIRM and not is_accessible_to_principals(TenantStatus(tenant.status)):
        raise links.InvalidLinkError()  # unsubscribing works even for a suspended workspace
    now = datetime.now(UTC)
    with tenant_session_scope(claims.tenant_id) as session:
        _lock(session, claims.tenant_id)
        row = session.execute(
            select(ContactConsent)
            .where(
                ContactConsent.tenant_id == claims.tenant_id,
                ContactConsent.id == claims.ref_id,
                ContactConsent.contact_id == claims.contact_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if purpose == links.UNSUBSCRIBE:
            # Always honoured, even if the consent record was reset or the
            # contact deleted since: the opt-out is stored for the address
            # the link was sent to.
            _suppress_address(session, claims.tenant_id, claims.address_hash, "link")
            if row is not None and row.address_hash == claims.address_hash:
                row.status, row.withdrawn_at, row.withdrawn_via = "withdrawn", now, "link"
                row.updated_at = now
        else:
            if row is None or row.address_hash != claims.address_hash:
                raise links.InvalidLinkError()  # the address changed since the link was sent
            if row.status == "withdrawn" or _suppression(
                session, claims.tenant_id, claims.address_hash
            ):
                raise links.InvalidLinkError()  # an old confirmation cannot undo unsubscribing
            row.status, row.granted_at, row.withdrawn_at, row.withdrawn_via = (
                "granted", now, None, None,
            )  # fmt: skip
            row.updated_at = now
    action = "confirmed" if purpose == links.CONFIRM else "withdrawn"
    _audit(claims.tenant_id, ActorType.SYSTEM, None, f"leadstack.consent.{action}",
           claims.contact_id, {"channel": EMAIL, "via": "link"})  # fmt: skip


def confirm(token: str) -> None:
    _apply_link(token, links.CONFIRM)


def unsubscribe(token: str) -> None:
    _apply_link(token, links.UNSUBSCRIBE)


# --- One-to-one email ----------------------------------------------------------------


def queue_email(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, *, subject: str, body: str
) -> MessageView:
    """A member writes to one contact. Refused if the contact has no address
    or has withdrawn consent; an unsubscribe link is added at delivery."""
    authorize(actor_id, tenant_id, perms.MESSAGES_SEND)
    subject, body = subject.strip(), body.strip()
    if not subject or len(subject) > MAX_SUBJECT or not body or len(body) > MAX_BODY:
        raise InvalidMessageError()
    if any(ch in subject for ch in "\r\n"):
        raise InvalidMessageError()  # no header injection through the subject
    _app_origin()  # fail before queuing if links cannot be produced
    links.require_secret()
    footer = consent_texts(workspace_profile(tenant_id).locale).unsubscribe_footer
    with tenant_session_scope(tenant_id) as session:
        _lock(session, tenant_id)
        address = _contact_email(session, tenant_id, contact_id)
        if not address:
            raise NoEmailAddressError()
        consent = _consent_row(session, tenant_id, contact_id)
        if consent is not None and consent.status == "withdrawn":
            raise ConsentWithdrawnError()
        if _suppression(session, tenant_id, address_hash(address)) is not None:
            raise ConsentWithdrawnError()
        if consent is None:
            session.add(
                ContactConsent(tenant_id=tenant_id, contact_id=contact_id, channel=EMAIL,
                               status="pending", source="manual",
                               address_hash=address_hash(address), confirmations_sent=0)
            )  # fmt: skip
        message = Message(tenant_id=tenant_id, contact_id=contact_id, channel=EMAIL,
                          kind="one_to_one", to_address=address, subject=subject,
                          body=f"{body}\n\n--\n{footer} {UNSUBSCRIBE_PLACEHOLDER}\n",
                          status="queued", created_by_user_id=actor_id)  # fmt: skip
        session.add(message)
        session.flush()
        session.refresh(message)
        view = _message_view(message)
    _audit(tenant_id, ActorType.USER, actor_id, "leadstack.message.queued", contact_id,
           {"message_id": str(view.message_id), "channel": EMAIL})  # fmt: skip
    return view


def list_messages(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, *, limit: int
) -> list[MessageView]:
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    with tenant_session_scope(tenant_id) as session:
        _contact_email(session, tenant_id, contact_id)
        rows = session.execute(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.contact_id == contact_id)
            .order_by(Message.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).scalars()
        return [_message_view(r) for r in rows]


# --- Delivery (job) --------------------------------------------------------------------


@dataclass(frozen=True)
class OutboundInfo:
    kind: str
    address_hash: str
    first_attempt: bool  # quota is charged once per message, not per retry


def outbound_info(tenant_id: uuid.UUID, message_id: uuid.UUID) -> OutboundInfo | None:
    """For the job layer's cross-workspace per-address limit (confirmations)."""
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(Message.kind, Message.to_address, Message.attempts, Message.status).where(
                Message.tenant_id == tenant_id, Message.id == message_id
            )
        ).first()
        if row is None:
            return None
        return OutboundInfo(row[0], address_hash(row[1]), row[2] == 0 and row[3] == "queued")


def suppress(tenant_id: uuid.UUID, message_id: uuid.UUID, reason: str) -> None:
    with tenant_session_scope(tenant_id) as session:
        row = session.get(Message, message_id, with_for_update=True)
        if row is not None and row.status == "queued":
            row.status, row.error_code = "suppressed", reason


def deliver_message(tenant_id: uuid.UUID, message_id: uuid.UUID) -> str:
    """At-most-once delivery; re-checks workspace, consent and address at
    send time, and signs the links only now (they are never stored)."""
    try:
        if not is_accessible_to_principals(TenantStatus(get_tenant(tenant_id).status)):
            return _finish_message(tenant_id, message_id, "suppressed", "workspace_inactive")
    except TenantNotFoundError:
        return "missing"
    try:
        sender = get_email_config().default_sender
        origin = _app_origin()
        links.require_secret()
    except (EmailConfigurationError, MessagingConfigurationError, links.LinkConfigurationError):
        return _finish_message(tenant_id, message_id, "failed", "email_not_configured")
    if not sender:
        return _finish_message(tenant_id, message_id, "failed", "email_not_configured")
    # Authorization at execution time (delayed work, CLAUDE.md 6.2): a member
    # who lost access since queuing the email no longer sends it. Checked
    # before the session opens (authorization opens its own connection).
    creator = _message_creator(tenant_id, message_id)
    if creator is not None:
        try:
            authorize(creator, tenant_id, perms.MESSAGES_SEND)
        except PermissionDeniedError:
            return _finish_message(tenant_id, message_id, "suppressed", "sender_not_authorized")
    with tenant_session_scope(tenant_id) as session:
        _lock(session, tenant_id)  # an unsubscribe is either fully before or after this check
        message = session.execute(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.id == message_id)
            .with_for_update()
        ).scalar_one_or_none()
        if message is None:
            return "missing"
        if message.status == "sending":
            message.status, message.error_code = "failed", "outcome_unknown"
            return "failed"
        if message.status != "queued":
            return message.status
        consent = _consent_row(session, tenant_id, message.contact_id)
        current = _contact_email(session, tenant_id, message.contact_id)
        if current is None or current.lower() != message.to_address.lower():
            message.status, message.error_code = "suppressed", "address_changed"
            return "suppressed"
        if consent is None or consent.address_hash != address_hash(current):
            message.status, message.error_code = "suppressed", "no_consent_record"
            return "suppressed"
        if consent.status == "withdrawn" or _suppression(session, tenant_id, consent.address_hash):
            message.status, message.error_code = "suppressed", "consent_withdrawn"
            return "suppressed"
        if message.kind == "confirmation" and consent.status != "pending":
            message.status, message.error_code = "suppressed", "already_decided"
            return "suppressed"
        contact_id, consent_id, bound_hash = message.contact_id, consent.id, consent.address_hash

        def link(purpose: str, path: str) -> str:
            claims = links.LinkClaims(purpose, tenant_id, contact_id, consent_id, bound_hash)
            return f"{origin}/{path}/{links.sign(claims)}"

        text = message.body.replace(UNSUBSCRIBE_PLACEHOLDER, link(links.UNSUBSCRIBE, "u"))
        if message.kind == "confirmation":
            text = text.replace(CONFIRM_PLACEHOLDER, link(links.CONFIRM, "c"))
        message.status = "sending"
        message.attempts += 1
        attempts = message.attempts
        outgoing = EmailMessage(
            sender=sender, to=(message.to_address,), subject=message.subject, text_body=text
        )
    try:
        result = email_adapter.deliver(outgoing)
    except InvalidEmailAddressError:
        return _finish_message(tenant_id, message_id, "failed", "invalid_address")
    except EmailConfigurationError:
        return _finish_message(tenant_id, message_id, "failed", "email_not_configured")
    except EmailProviderError:
        if attempts >= MAX_ATTEMPTS:
            return _finish_message(tenant_id, message_id, "failed", "provider_unavailable")
        _requeue(tenant_id, message_id)
        raise  # the job layer retries with backoff
    _finish_message(tenant_id, message_id, "sent", None, result.provider_message_id)
    _audit(tenant_id, ActorType.SYSTEM, None, "leadstack.message.sent", contact_id,
           {"message_id": str(message_id), "channel": EMAIL})  # fmt: skip
    return "sent"


def _message_creator(tenant_id: uuid.UUID, message_id: uuid.UUID) -> uuid.UUID | None:
    """The member who wrote the message (None for system emails)."""
    with tenant_session_scope(tenant_id) as session:
        return session.execute(
            select(Message.created_by_user_id).where(
                Message.tenant_id == tenant_id, Message.id == message_id
            )
        ).scalar_one_or_none()


def _finish_message(
    tenant_id, message_id, status: str, error: str | None, provider_id: str | None = None
) -> str:
    with tenant_session_scope(tenant_id) as session:
        row = session.get(Message, message_id, with_for_update=True)
        if row is not None:
            row.status, row.error_code = status, error
            if provider_id:
                row.provider_message_id = provider_id[:255]
            if status == "sent":
                row.sent_at = datetime.now(UTC)
    return status


def _requeue(tenant_id: uuid.UUID, message_id: uuid.UUID) -> None:
    with tenant_session_scope(tenant_id) as session:
        row = session.get(Message, message_id, with_for_update=True)
        if row is not None and row.status == "sending":
            row.status = "queued"


def stale_queued(tenant_id: uuid.UUID, *, older_than: timedelta) -> list[uuid.UUID]:
    """Messages whose enqueue failed (recovery: `leadstack-admin send-queued`)."""
    cutoff = datetime.now(UTC) - older_than
    with tenant_session_scope(tenant_id) as session:
        return list(
            session.execute(
                select(Message.id)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.status == "queued",
                    Message.created_at < cutoff,
                )
                .limit(500)
            ).scalars()
        )
