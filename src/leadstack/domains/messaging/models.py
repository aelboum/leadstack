"""Messaging tables (migration 0008)."""

from __future__ import annotations

import uuid
from datetime import datetime

from infra.db import Base, DateTime, Integer, Mapped, String, Text, mapped_column, now

# Advisory lock serializing every consent/suppression write, delivery-time
# consent checks and contact email changes in a workspace. Lock order:
# this lock first, then row locks.
CONSENT_LOCK = "leadstack.consent"


class ContactConsent(Base):
    __tablename__ = "contact_consents"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_submission_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    address_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmations_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class EmailSuppression(Base):
    """An opt-out for an address in a workspace (survives contact changes)."""

    __tablename__ = "email_suppressions"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    address_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_address: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
