"""Form tables (migration 0006)."""

from __future__ import annotations

import uuid
from datetime import datetime

from infra.db import JSON, Base, DateTime, ForeignKey, Mapped, String, Text, mapped_column, now


class Form(Base):
    __tablename__ = "forms"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    fields: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    consent_text: Mapped[str] = mapped_column(Text, nullable=False)
    success_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    redirect_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_label: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class FormSubmission(Base):
    __tablename__ = "form_submissions"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    form_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    consent_text: Mapped[str] = mapped_column(Text, nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class FormKey(Base):
    """Public key -> tenant/form. No tenant RLS (looked up before any tenant
    context exists); identifiers only. See docs/adr/0005-public-form-keys.md."""

    __tablename__ = "form_keys"
    __table_args__ = {"schema": "leadstack"}

    public_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    form_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
