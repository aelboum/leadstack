"""Sales tables (migration 0005). Tenant-owned, forced RLS; composite
same-tenant foreign keys are defined in the migration.

`BigInteger` and `Date` come from `leadstack.db_types` (infra.db does not
export them; mapping BIGINT as Integer breaks amounts above 2^31-1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from infra.db import Base, DateTime, ForeignKey, Integer, Mapped, String, mapped_column, now

from leadstack.db_types import BigInteger, Date

__all__ = ["Deal", "DealStageHistory", "Pipeline", "PipelineStage"]


class Pipeline(Base):
    __tablename__ = "pipelines"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    probability: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class Deal(Base):
    __tablename__ = "deals"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    stage_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id"), nullable=True
    )
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="open")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class DealStageHistory(Base):
    __tablename__ = "deal_stage_history"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    deal_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    to_stage_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    changed_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
