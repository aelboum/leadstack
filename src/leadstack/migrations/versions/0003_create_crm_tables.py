"""create crm tables

Revision ID: 0003_crm_tables
Revises: 0002_workspace_tables
Create Date: 2026-10-09 00:00:00.000000

leadstack.companies and leadstack.contacts: tenant-owned, forced row-level
security. A contact's company is referenced through a composite foreign key
on (tenant_id, company_id), so it can only point at a company of the same
workspace. Deleting a company nulls only the contact's company_id
(PostgreSQL 15+ column-list SET NULL), never its tenant_id.

Indexes serve the list order (tenant_id, created_at DESC, id DESC) and
exact email/phone lookups. Substring search scans within one tenant; a
trigram index is deferred until real data volumes justify it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infra.db import tenant_rls_statements

revision: str = "0003_crm_tables"
down_revision: str | Sequence[str] | None = "0002_workspace_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("website", sa.String(2048), nullable=True),
        sa.Column("phone_e164", sa.String(16), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_companies_tenant_id_id"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_companies_name"),
        sa.CheckConstraint(
            "phone_e164 IS NULL OR phone_e164 ~ '^\\+[1-9][0-9]{6,14}$'",
            name="ck_companies_phone_e164",
        ),
        sa.CheckConstraint(
            "country IS NULL OR country ~ '^[A-Z]{2}$'", name="ck_companies_country"
        ),
        schema="leadstack",
    )
    op.create_index(
        "ix_companies_tenant_created",
        "companies",
        ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema="leadstack",
    )
    for statement in tenant_rls_statements("companies", schema="leadstack"):
        op.execute(statement)

    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("phone_e164", sa.String(16), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("job_title", sa.String(150), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("core.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "coalesce(first_name, last_name, email, phone_e164) IS NOT NULL",
            name="ck_contacts_identifiable",
        ),
        sa.CheckConstraint(
            "phone_e164 IS NULL OR phone_e164 ~ '^\\+[1-9][0-9]{6,14}$'",
            name="ck_contacts_phone_e164",
        ),
        schema="leadstack",
    )
    # Composite, same-tenant company reference; SET NULL clears only company_id.
    op.execute(
        "ALTER TABLE leadstack.contacts ADD CONSTRAINT fk_contacts_company_same_tenant "
        "FOREIGN KEY (tenant_id, company_id) REFERENCES leadstack.companies (tenant_id, id) "
        "ON DELETE SET NULL (company_id)"
    )
    op.create_index(
        "ix_contacts_tenant_created",
        "contacts",
        ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema="leadstack",
    )
    op.create_index(
        "ix_contacts_tenant_email",
        "contacts",
        ["tenant_id", sa.text("lower(email)")],
        schema="leadstack",
    )
    op.create_index(
        "ix_contacts_tenant_phone", "contacts", ["tenant_id", "phone_e164"], schema="leadstack"
    )
    op.create_index(
        "ix_contacts_tenant_company", "contacts", ["tenant_id", "company_id"], schema="leadstack"
    )
    for statement in tenant_rls_statements("contacts", schema="leadstack"):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("contacts", schema="leadstack")
    op.drop_table("companies", schema="leadstack")
