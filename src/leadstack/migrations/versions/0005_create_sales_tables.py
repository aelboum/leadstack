"""create sales tables

Revision ID: 0005_sales
Revises: 0004_crm_productivity
Create Date: 2026-10-09 00:00:00.000000

Pipelines, stages, deals and deal stage history. Tenant-owned, forced RLS.

- A stage belongs to one pipeline (composite FK, cascade with the pipeline).
- A deal's stage must belong to the deal's pipeline: the deal references
  (tenant_id, pipeline_id, stage_id) -> stages(tenant_id, pipeline_id, id).
  RESTRICT: a stage that still holds deals cannot be deleted.
- Money is `amount_minor BIGINT` + `currency CHAR(3)`, both or neither.
- Contact/company links are same-tenant composite FKs that clear on delete.
- History rows cascade with their deal.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infra.db import tenant_rls_statements

revision: str = "0005_sales"
down_revision: str | Sequence[str] | None = "0004_crm_productivity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)
_TABLES = ("pipelines", "pipeline_stages", "deals", "deal_stage_history")


def _now(name: str) -> sa.Column:
    return sa.Column(name, _TS, nullable=False, server_default=sa.func.now())


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("core.tenants.id"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "pipelines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant(),
        sa.Column("name", sa.String(100), nullable=False),
        _now("created_at"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pipelines_tenant_id_id"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_pipelines_name"),
        schema="leadstack",
    )
    op.create_index(
        "uq_pipelines_tenant_name",
        "pipelines",
        ["tenant_id", sa.text("lower(name)")],
        unique=True,
        schema="leadstack",
    )

    op.create_table(
        "pipeline_stages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant(),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("probability", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        _now("created_at"),
        sa.UniqueConstraint(
            "tenant_id", "pipeline_id", "id", name="uq_pipeline_stages_tenant_pipeline_id"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pipeline_id"],
            ["leadstack.pipelines.tenant_id", "leadstack.pipelines.id"],
            ondelete="CASCADE",
            name="fk_stages_pipeline",
        ),
        sa.CheckConstraint("kind IN ('open', 'won', 'lost')", name="ck_stages_kind"),
        sa.CheckConstraint("probability BETWEEN 0 AND 100", name="ck_stages_probability"),
        sa.CheckConstraint("position >= 0", name="ck_stages_position"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_stages_name"),
        schema="leadstack",
    )
    op.create_index(
        "ix_stages_pipeline_position",
        "pipeline_stages",
        ["tenant_id", "pipeline_id", "position"],
        schema="leadstack",
    )

    op.create_table(
        "deals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant(),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("stage_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("core.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expected_close_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column("closed_at", _TS, nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        _now("created_at"),
        _now("updated_at"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_deals_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pipeline_id", "stage_id"],
            [
                "leadstack.pipeline_stages.tenant_id",
                "leadstack.pipeline_stages.pipeline_id",
                "leadstack.pipeline_stages.id",
            ],
            name="fk_deals_stage_in_pipeline",
        ),
        sa.CheckConstraint("status IN ('open', 'won', 'lost')", name="ck_deals_status"),
        sa.CheckConstraint("(status = 'open') = (closed_at IS NULL)", name="ck_deals_closed_at"),
        sa.CheckConstraint(
            "(amount_minor IS NULL) = (currency IS NULL)", name="ck_deals_money_pair"
        ),
        sa.CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_deals_currency"),
        sa.CheckConstraint(
            "amount_minor IS NULL OR (amount_minor >= 0 AND amount_minor < 1000000000000000)",
            name="ck_deals_amount_range",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_deals_title"),
        schema="leadstack",
    )
    for column, table in (("contact_id", "contacts"), ("company_id", "companies")):
        op.execute(
            f"ALTER TABLE leadstack.deals ADD CONSTRAINT fk_deals_{column} "
            f"FOREIGN KEY (tenant_id, {column}) REFERENCES leadstack.{table} (tenant_id, id) "
            f"ON DELETE SET NULL ({column})"
        )
    op.create_index(
        "ix_deals_pipeline_stage",
        "deals",
        ["tenant_id", "pipeline_id", "stage_id"],
        schema="leadstack",
    )
    op.create_index(
        "ix_deals_tenant_created",
        "deals",
        ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
        schema="leadstack",
    )

    op.create_table(
        "deal_stage_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant(),
        sa.Column("deal_id", sa.Uuid(), nullable=False),
        sa.Column("from_stage_id", sa.Uuid(), nullable=True),
        sa.Column("to_stage_id", sa.Uuid(), nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=False),
        _now("changed_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "deal_id"],
            ["leadstack.deals.tenant_id", "leadstack.deals.id"],
            ondelete="CASCADE",
            name="fk_history_deal",
        ),
        schema="leadstack",
    )
    op.create_index(
        "ix_history_deal", "deal_stage_history", ["tenant_id", "deal_id"], schema="leadstack"
    )

    for table in _TABLES:
        for statement in tenant_rls_statements(table, schema="leadstack"):
            op.execute(statement)


def downgrade() -> None:
    for table in ("deal_stage_history", "deals", "pipeline_stages", "pipelines"):
        op.drop_table(table, schema="leadstack")
