"""Appointments: booking types, public booking keys and appointments.

- leadstack.booking_types: bookable meeting types with a host, duration,
  buffer, notice, horizon, IANA time zone and weekly availability rules
  (local wall-clock time). Tenant-owned, forced RLS.
- leadstack.booking_keys: public key -> (tenant, booking type). Looked up
  before any tenant context exists, so it cannot be under tenant RLS (the
  reviewed pattern of form_keys, ADR-0005/ADR-0008). Identifiers only.
- leadstack.appointments: UTC instants, host, contact, status. A partial
  unique index on (tenant, host, starts_at) for booked rows backs up the
  advisory-lock overlap check. Tenant-owned, forced RLS.

Revision ID: 0009_scheduling
Revises: 0008_messaging
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infra.db import tenant_rls_statements

revision: str = "0009_scheduling"
down_revision: str | Sequence[str] | None = "0008_messaging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "booking_types",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("public_key", sa.String(64), nullable=False, unique=True),
        sa.Column("host_user_id", sa.Uuid(), sa.ForeignKey("core.users.id"), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_notice_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("horizon_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_booking_types_tenant_id_id"),
        sa.CheckConstraint("duration_minutes BETWEEN 5 AND 480", name="ck_booking_duration"),
        sa.CheckConstraint("buffer_minutes BETWEEN 0 AND 240", name="ck_booking_buffer"),
        sa.CheckConstraint("min_notice_minutes BETWEEN 0 AND 43200", name="ck_booking_notice"),
        sa.CheckConstraint("horizon_days BETWEEN 1 AND 90", name="ck_booking_horizon"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_booking_status"),
        schema="leadstack",
    )
    op.create_table(
        "booking_keys",
        sa.Column("public_key", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("booking_type_id", sa.Uuid(), nullable=False, unique=True),
        schema="leadstack",
    )
    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("booking_type_id", sa.Uuid(), nullable=True),
        sa.Column("host_user_id", sa.Uuid(), sa.ForeignKey("core.users.id"), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", _TS, nullable=False),
        sa.Column("ends_at", _TS, nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="booked"),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("cancelled_at", _TS, nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("ends_at > starts_at", name="ck_appointments_range"),
        sa.CheckConstraint("status IN ('booked', 'cancelled')", name="ck_appointments_status"),
        sa.CheckConstraint("source IN ('public', 'manual')", name="ck_appointments_source"),
        sa.CheckConstraint(
            "status <> 'cancelled' OR cancelled_at IS NOT NULL", name="ck_appointments_cancelled"
        ),
        schema="leadstack",
    )
    op.execute(
        "ALTER TABLE leadstack.appointments ADD CONSTRAINT fk_appointments_contact "
        "FOREIGN KEY (tenant_id, contact_id) REFERENCES leadstack.contacts (tenant_id, id) "
        "ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE leadstack.appointments ADD CONSTRAINT fk_appointments_booking_type "
        "FOREIGN KEY (tenant_id, booking_type_id) "
        "REFERENCES leadstack.booking_types (tenant_id, id) "
        "ON DELETE SET NULL (booking_type_id)"
    )
    op.create_index(
        "uq_appointments_host_start_booked",
        "appointments",
        ["tenant_id", "host_user_id", "starts_at"],
        unique=True,
        schema="leadstack",
        postgresql_where=sa.text("status = 'booked'"),
    )
    op.create_index(
        "ix_appointments_host_range",
        "appointments",
        ["tenant_id", "host_user_id", "starts_at", "ends_at"],
        schema="leadstack",
    )
    op.create_index(
        "ix_appointments_contact", "appointments", ["tenant_id", "contact_id"], schema="leadstack"
    )
    for table in ("booking_types", "appointments"):
        for statement in tenant_rls_statements(table, schema="leadstack"):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("appointments", schema="leadstack")
    op.drop_table("booking_keys", schema="leadstack")
    op.drop_table("booking_types", schema="leadstack")
