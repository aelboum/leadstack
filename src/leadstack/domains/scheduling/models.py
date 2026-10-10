"""Scheduling tables (migration 0009)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from infra.db import JSON, Base, DateTime, ForeignKey, Integer, Mapped, String, mapped_column, now


class BookingType(Base):
    __tablename__ = "booking_types"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    host_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id"), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_notice_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )


class BookingKey(Base):
    """Public key -> tenant/booking type. No tenant RLS (looked up before
    any tenant context exists); identifiers only. See ADR-0008."""

    __tablename__ = "booking_keys"
    __table_args__ = {"schema": "leadstack"}

    public_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    booking_type_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = {"schema": "leadstack"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), nullable=False)
    booking_type_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    host_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id"), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="booked")
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=now()
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
