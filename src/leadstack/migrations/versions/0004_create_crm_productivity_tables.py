"""create crm productivity tables

Revision ID: 0004_crm_productivity
Revises: 0003_crm_tables
Create Date: 2026-10-09 00:00:00.000000

Tags, contact tags, notes, tasks and custom-field definitions, plus a
`custom_fields` JSON object on contacts. All tenant-owned with forced RLS.
Every cross-table reference is a composite (tenant_id, id) foreign key so a
row can only point at a row of the same workspace:

- contact_tags -> contacts, tags: ON DELETE CASCADE (a link dies with either end)
- notes -> contacts: ON DELETE CASCADE (notes are part of the contact record)
- tasks -> contacts: ON DELETE SET NULL (contact_id) (the task survives, unlinked)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infra.db import tenant_rls_statements
from sqlalchemy.dialects import postgresql

revision: str = "0004_crm_productivity"
down_revision: str | Sequence[str] | None = "0003_crm_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)
_TABLES = ("contact_tags", "notes", "tasks", "tags", "custom_field_definitions")


def _now(name: str) -> sa.Column:
    return sa.Column(name, _TS, nullable=False, server_default=sa.func.now())


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_contacts_tenant_id_id", "contacts", ["tenant_id", "id"], schema="leadstack"
    )
    op.add_column(
        "contacts",
        sa.Column(
            "custom_fields",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="leadstack",
    )
    op.create_check_constraint(
        "ck_contacts_custom_fields_object",
        "contacts",
        "jsonb_typeof(custom_fields) = 'object'",
        schema="leadstack",
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant(),
        sa.Column("name", sa.String(50), nullable=False),
        _now("created_at"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tags_tenant_id_id"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_tags_name"),
        schema="leadstack",
    )
    op.create_index(
        "uq_tags_tenant_name",
        "tags",
        ["tenant_id", sa.text("lower(name)")],
        unique=True,
        schema="leadstack",
    )

    op.create_table(
        "contact_tags",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        _now("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "contact_id", "tag_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["leadstack.contacts.tenant_id", "leadstack.contacts.id"],
            ondelete="CASCADE",
            name="fk_contact_tags_contact",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tag_id"],
            ["leadstack.tags.tenant_id", "leadstack.tags.id"],
            ondelete="CASCADE",
            name="fk_contact_tags_tag",
        ),
        schema="leadstack",
    )
    op.create_index(
        "ix_contact_tags_tag", "contact_tags", ["tenant_id", "tag_id"], schema="leadstack"
    )

    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant(),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        _now("created_at"),
        _now("updated_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["leadstack.contacts.tenant_id", "leadstack.contacts.id"],
            ondelete="CASCADE",
            name="fk_notes_contact",
        ),
        sa.CheckConstraint("length(trim(body)) > 0", name="ck_notes_body"),
        sa.CheckConstraint("length(body) <= 10000", name="ck_notes_body_length"),
        schema="leadstack",
    )
    op.create_index(
        "ix_notes_contact",
        "notes",
        ["tenant_id", "contact_id", sa.text("created_at DESC")],
        schema="leadstack",
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant(),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", _TS, nullable=True),
        sa.Column(
            "assignee_user_id",
            sa.Uuid(),
            sa.ForeignKey("core.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("completed_at", _TS, nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint("status IN ('open', 'done')", name="ck_tasks_status"),
        sa.CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)", name="ck_tasks_completion"
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_tasks_title"),
        schema="leadstack",
    )
    op.execute(
        "ALTER TABLE leadstack.tasks ADD CONSTRAINT fk_tasks_contact "
        "FOREIGN KEY (tenant_id, contact_id) REFERENCES leadstack.contacts (tenant_id, id) "
        "ON DELETE SET NULL (contact_id)"
    )
    op.create_index(
        "ix_tasks_assignee_open",
        "tasks",
        ["tenant_id", "assignee_user_id", "status", "due_at"],
        schema="leadstack",
    )
    op.create_index("ix_tasks_contact", "tasks", ["tenant_id", "contact_id"], schema="leadstack")

    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant(),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("field_type", sa.String(16), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        _now("created_at"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_custom_fields_tenant_key"),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_custom_fields_key"),
        sa.CheckConstraint(
            "field_type IN ('text', 'number', 'date', 'boolean', 'select')",
            name="ck_custom_fields_type",
        ),
        sa.CheckConstraint(
            "(field_type = 'select') = (options IS NOT NULL)", name="ck_custom_fields_options"
        ),
        schema="leadstack",
    )

    for table in _TABLES:
        for statement in tenant_rls_statements(table, schema="leadstack"):
            op.execute(statement)


def downgrade() -> None:
    for table in ("contact_tags", "notes", "tasks", "tags", "custom_field_definitions"):
        op.drop_table(table, schema="leadstack")
    op.drop_constraint("ck_contacts_custom_fields_object", "contacts", schema="leadstack")
    op.drop_column("contacts", "custom_fields", schema="leadstack")
    op.drop_constraint("uq_contacts_tenant_id_id", "contacts", schema="leadstack")
