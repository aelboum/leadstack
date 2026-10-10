"""Domain events (transactional outbox).

A domain operation calls `emit(session, ...)` inside the *same* session
(and therefore transaction) as the change itself, so an event exists if and
only if the change committed. Events carry identifiers and small,
non-personal attributes only (no names, emails, phone numbers or message
text). After the request, the HTTP layer enqueues processing; if that fails,
events stay pending and `leadstack-admin process-events` replays them.
"""

from __future__ import annotations

import uuid

from leadstack.domains.automation.models import Event

CONTACT_CREATED = "contact.created"
FORM_SUBMITTED = "form.submitted"
DEAL_STAGE_CHANGED = "deal.stage_changed"
EVENT_TYPES = (CONTACT_CREATED, FORM_SUBMITTED, DEAL_STAGE_CHANGED)


def emit(
    session,
    tenant_id: uuid.UUID,
    event_type: str,
    subject_id: uuid.UUID,
    attributes: dict[str, object] | None = None,
) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type!r}")
    session.add(
        Event(
            tenant_id=tenant_id,
            event_type=event_type,
            subject_id=subject_id,
            attributes=attributes or {},
        )
    )
