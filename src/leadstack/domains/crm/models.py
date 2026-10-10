"""CRM tables (schema `leadstack`, migration 0003). Tenant-owned, forced
row-level security. A contact's company must belong to the same workspace:
the reference is a composite foreign key on (tenant_id, company_id)."""

from __future__ import annotations

import uuid
from datetime import datetime

from infra.db import (
    JSON,
    Base,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Mapped,
    String,
    Text,
    UniqueConstraint,
    mapped_column,
    now,
)


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_companies_tenant_id_id"),
        {"schema": "leadstack"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(16), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["leadstack.companies.tenant_id", "leadstack.companies.id"],
            name="fk_contacts_company_same_tenant",
        ),
        {"schema": "leadstack"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(16), nullable=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(150), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id"), nullable=True
    )
    # Validated against leadstack.custom_field_definitions (migration 0004).
    custom_fields: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
