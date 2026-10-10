"""create workspace tables

Revision ID: 0002_workspace_tables
Revises: 0001_leadstack_schema
Create Date: 2026-10-09 00:00:00.000000

- leadstack.workspaces: product profile of a SaaS-OS tenant. Tenant-owned;
  forced row-level security on tenant_id.
- leadstack.invitation_grants: the role an invitation confers. Tenant-owned;
  forced RLS; one row per SaaS-OS invitation. Deliberately *no* foreign key
  into core.invitations: SaaS-OS's tenant purge deletes invitations before
  product participants run, so such a key would block erasure. The
  LeadStack purge participant removes these rows instead.
- leadstack.user_workspaces: per-user index of joined workspaces (a lookup
  hint re-validated against SaaS-OS memberships; identifiers only). Keyed by
  user, so no tenant RLS policy -- see docs/adr/0004-workspace-model.md.

The application role receives DML through the schema's default privileges
(revision 0001). Downgrade drops only these tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infra.db import tenant_rls_statements

revision: str = "0002_workspace_tables"
down_revision: str | Sequence[str] | None = "0001_leadstack_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("locale", sa.String(16), nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('agency', 'client', 'direct')", name="ck_workspaces_kind"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_workspaces_currency"),
        sa.CheckConstraint(
            "country IS NULL OR country ~ '^[A-Z]{2}$'", name="ck_workspaces_country"
        ),
        sa.CheckConstraint("length(trim(display_name)) > 0", name="ck_workspaces_display_name"),
        schema="leadstack",
    )
    for statement in tenant_rls_statements("workspaces", schema="leadstack"):
        op.execute(statement)

    op.create_table(
        "invitation_grants",
        sa.Column("invitation_id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name="ck_invitation_grants_role"
        ),
        schema="leadstack",
    )
    op.create_index(
        "ix_invitation_grants_tenant_id", "invitation_grants", ["tenant_id"], schema="leadstack"
    )
    for statement in tenant_rls_statements("invitation_grants", schema="leadstack"):
        op.execute(statement)

    op.create_table(
        "user_workspaces",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("core.users.id"), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), primary_key=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        schema="leadstack",
    )


def downgrade() -> None:
    op.drop_table("user_workspaces", schema="leadstack")
    op.drop_table("invitation_grants", schema="leadstack")
    op.drop_table("workspaces", schema="leadstack")
