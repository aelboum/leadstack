"""create messaging tables

Revision ID: 0008_messaging
Revises: 0007_automation
Create Date: 2026-10-09 00:00:00.000000

- leadstack.contact_consents: one row per (contact, channel) with status
  pending / granted / withdrawn and evidence (source, timestamps, the form
  submission that requested it). Tenant-owned, forced RLS.
- leadstack.messages: outbound message log (email first). Keeps the address
  used and the content sent, because the CRM shows what was sent to whom.
  Tenant-owned, forced RLS. Deleting a contact (erasure) deletes its consent
  records and its messages, including the address and content.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infra.db import tenant_rls_statements

revision: str = "0008_messaging"
down_revision: str | Sequence[str] | None = "0007_automation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "contact_consents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("evidence_submission_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("requested_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("granted_at", _TS, nullable=True),
        sa.Column("withdrawn_at", _TS, nullable=True),
        # Who withdrew: the contact through the unsubscribe link, or a member.
        sa.Column("withdrawn_via", sa.String(16), nullable=True),
        # SHA-256 of the normalized address the consent covers.
        sa.Column("address_hash", sa.String(64), nullable=False),
        sa.Column("confirmations_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_requested_at", _TS, nullable=True),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "contact_id", "channel", name="uq_consents_contact_channel"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["leadstack.contacts.tenant_id", "leadstack.contacts.id"],
            ondelete="CASCADE",
            name="fk_consents_contact",
        ),
        sa.CheckConstraint("channel IN ('email')", name="ck_consents_channel"),
        sa.CheckConstraint(
            "status IN ('pending', 'granted', 'withdrawn')", name="ck_consents_status"
        ),
        sa.CheckConstraint("source IN ('form', 'manual', 'import')", name="ck_consents_source"),
        sa.CheckConstraint(
            "status <> 'granted' OR (granted_at IS NOT NULL AND withdrawn_at IS NULL)",
            name="ck_consents_granted",
        ),
        sa.CheckConstraint(
            "status <> 'withdrawn' OR (withdrawn_at IS NOT NULL AND withdrawn_via IS NOT NULL)",
            name="ck_consents_withdrawn",
        ),
        sa.CheckConstraint(
            "withdrawn_via IS NULL OR withdrawn_via IN ('link', 'member')",
            name="ck_consents_withdrawn_via",
        ),
        sa.CheckConstraint("confirmations_sent >= 0", name="ck_consents_confirmations"),
        schema="leadstack",
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("to_address", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", _TS, nullable=True),
        sa.CheckConstraint("channel IN ('email')", name="ck_messages_channel"),
        sa.CheckConstraint("kind IN ('one_to_one', 'confirmation')", name="ck_messages_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'sending', 'sent', 'failed', 'suppressed')",
            name="ck_messages_status",
        ),
        sa.CheckConstraint("length(body) <= 20000", name="ck_messages_body_length"),
        schema="leadstack",
    )
    op.execute(
        "ALTER TABLE leadstack.messages ADD CONSTRAINT fk_messages_contact "
        "FOREIGN KEY (tenant_id, contact_id) REFERENCES leadstack.contacts (tenant_id, id) "
        "ON DELETE CASCADE"
    )
    op.create_index(
        "ix_messages_contact",
        "messages",
        ["tenant_id", "contact_id", sa.text("created_at DESC")],
        schema="leadstack",
    )
    op.create_index(
        "ix_messages_tenant_created", "messages", ["tenant_id", "created_at"], schema="leadstack"
    )
    # Opt-outs outlive consent resets and contact deletion: keyed by the
    # address hash, never by the contact (ADR-0007).
    op.create_table(
        "email_suppressions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("address_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "channel", "address_hash", name="uq_email_suppressions_address"
        ),
        sa.CheckConstraint("channel IN ('email')", name="ck_suppressions_channel"),
        sa.CheckConstraint("reason IN ('link', 'member')", name="ck_suppressions_reason"),
        schema="leadstack",
    )
    for table in ("contact_consents", "messages", "email_suppressions"):
        for statement in tenant_rls_statements(table, schema="leadstack"):
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("email_suppressions", schema="leadstack")
    op.drop_table("messages", schema="leadstack")
    op.drop_table("contact_consents", schema="leadstack")
