"""create form tables

Revision ID: 0006_forms
Revises: 0005_sales
Create Date: 2026-10-09 00:00:00.000000

- leadstack.forms: lead-capture form definitions. Tenant-owned, forced RLS.
- leadstack.form_submissions: accepted submissions (defined fields only,
  consent snapshot; no IP address is stored). Tenant-owned, forced RLS.
- leadstack.form_keys: public key -> (tenant, form). The public submission
  endpoint has no tenant context yet, so this lookup cannot be under tenant
  RLS (the same reviewed pattern as user_workspaces, ADR-0005). It holds
  identifiers only; the key is an unguessable random value.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infra.db import tenant_rls_statements
from sqlalchemy.dialects import postgresql

revision: str = "0006_forms"
down_revision: str | Sequence[str] | None = "0005_sales"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "forms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("public_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("consent_text", sa.Text(), nullable=False),
        sa.Column("success_message", sa.String(500), nullable=True),
        sa.Column("redirect_url", sa.String(2048), nullable=True),
        sa.Column("source_label", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_forms_tenant_id_id"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_forms_status"),
        sa.CheckConstraint("jsonb_typeof(fields) = 'array'", name="ck_forms_fields_array"),
        sa.CheckConstraint("length(trim(consent_text)) > 0", name="ck_forms_consent"),
        schema="leadstack",
    )
    op.create_table(
        "form_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("form_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("consent_text", sa.Text(), nullable=False),
        sa.Column("consented_at", _TS, nullable=False),
        sa.Column("submitted_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "form_id"],
            ["leadstack.forms.tenant_id", "leadstack.forms.id"],
            ondelete="CASCADE",
            name="fk_submissions_form",
        ),
        sa.CheckConstraint("octet_length(data::text) <= 32768", name="ck_submissions_data_size"),
        schema="leadstack",
    )
    op.execute(
        "ALTER TABLE leadstack.form_submissions ADD CONSTRAINT fk_submissions_contact "
        "FOREIGN KEY (tenant_id, contact_id) REFERENCES leadstack.contacts (tenant_id, id) "
        "ON DELETE SET NULL (contact_id)"
    )
    op.create_index(
        "ix_submissions_form",
        "form_submissions",
        ["tenant_id", "form_id", sa.text("submitted_at DESC")],
        schema="leadstack",
    )
    op.create_table(
        "form_keys",
        sa.Column("public_key", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("form_id", sa.Uuid(), nullable=False, unique=True),
        schema="leadstack",
    )
    for table in ("forms", "form_submissions"):
        for statement in tenant_rls_statements(table, schema="leadstack"):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("form_keys", schema="leadstack")
    op.drop_table("form_submissions", schema="leadstack")
    op.drop_table("forms", schema="leadstack")
