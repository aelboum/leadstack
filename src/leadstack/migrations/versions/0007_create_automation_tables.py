"""create automation tables

Revision ID: 0007_automation
Revises: 0006_forms
Create Date: 2026-10-09 00:00:00.000000

- leadstack.events: transactional outbox of domain events (identifiers
  only), written in the same transaction as the change that caused them.
- leadstack.workflows: event-triggered workflow definitions.
- leadstack.workflow_runs: one row per (workflow, event) -- the unique key
  makes processing idempotent under duplicate or concurrent delivery.

All tenant-owned with forced RLS. No delayed or scheduled execution exists
yet (roadmap decision D6).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infra.db import tenant_rls_statements
from sqlalchemy.dialects import postgresql

revision: str = "0007_automation"
down_revision: str | Sequence[str] | None = "0006_forms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", _TS, nullable=True),
        sa.CheckConstraint(
            "octet_length(attributes::text) <= 4096", name="ck_events_attributes_size"
        ),
        schema="leadstack",
    )
    op.create_index(
        "ix_events_pending",
        "events",
        ["tenant_id", "occurred_at"],
        schema="leadstack",
        postgresql_where=sa.text("processed_at IS NULL"),
    )

    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("trigger", sa.String(64), nullable=False),
        sa.Column("conditions", postgresql.JSONB(), nullable=False),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("paused_reason", sa.String(64), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workflows_tenant_id_id"),
        sa.CheckConstraint("status IN ('active', 'paused')", name="ck_workflows_status"),
        sa.CheckConstraint("jsonb_typeof(actions) = 'array'", name="ck_workflows_actions"),
        sa.CheckConstraint("jsonb_typeof(conditions) = 'object'", name="ck_workflows_conditions"),
        schema="leadstack",
    )
    op.create_index(
        "ix_workflows_trigger", "workflows", ["tenant_id", "trigger", "status"], schema="leadstack"
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("claim_token", sa.Uuid(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", _TS, nullable=True),
        sa.UniqueConstraint("workflow_id", "event_id", name="uq_workflow_runs_once"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["leadstack.workflows.tenant_id", "leadstack.workflows.id"],
            ondelete="CASCADE",
            name="fk_runs_workflow",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'skipped')", name="ck_runs_status"
        ),
        schema="leadstack",
    )
    op.create_index(
        "ix_runs_workflow",
        "workflow_runs",
        ["tenant_id", "workflow_id", sa.text("started_at DESC")],
        schema="leadstack",
    )
    for table in ("events", "workflows", "workflow_runs"):
        for statement in tenant_rls_statements(table, schema="leadstack"):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("workflow_runs", schema="leadstack")
    op.drop_table("workflows", schema="leadstack")
    op.drop_table("events", schema="leadstack")
