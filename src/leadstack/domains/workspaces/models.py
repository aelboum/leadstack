"""Workspace tables (schema `leadstack`, created by migration 0002).

- `Workspace`: the product profile of one SaaS-OS tenant (kind, display
  name, regional defaults). Tenant-owned, row-level security on `tenant_id`.
- `InvitationGrant`: the role a SaaS-OS invitation will confer when it is
  accepted (SaaS-OS invitations carry no role). Tenant-owned, RLS.
- `UserWorkspace`: a per-user index of workspaces the user joined through
  LeadStack. SaaS-OS offers no "list the tenants of a user" lookup and its
  membership table is tenant-scoped under RLS, so this index is the only way
  to find a user's workspaces. It is a hint, never authority: every entry is
  re-validated against the live SaaS-OS membership before use. It holds only
  identifiers, is keyed by user rather than tenant, and therefore has no
  tenant RLS policy (docs/adr/0004-workspace-model.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from infra.db import (
    Base,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Mapped,
    String,
    mapped_column,
    now,
)

WORKSPACE_KINDS = ("agency", "client", "direct")


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("kind IN ('agency', 'client', 'direct')", name="ck_workspaces_kind"),
        {"schema": "leadstack"},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class InvitationGrant(Base):
    __tablename__ = "invitation_grants"
    __table_args__ = {"schema": "leadstack"}

    invitation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class UserWorkspace(Base):
    __tablename__ = "user_workspaces"
    __table_args__ = {"schema": "leadstack"}

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
