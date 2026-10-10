"""create leadstack schema

Revision ID: 0001_leadstack_schema
Revises:
Create Date: 2026-10-09 00:00:00.000000

The product-owned schema namespace (SaaS-OS ADR-0016: `<project>.*` is owned
by the consuming project). No tables yet -- the first arrive with the CRM
vertical slice, each with `tenant_id` referencing `core.tenants` and
`infra.db.tenant_rls_statements()` applied.

The application role (`APP_DB_USER`, the restricted NOSUPERUSER/NOBYPASSRLS
role SaaS-OS's own migrations also grant to) receives USAGE on the schema
and default DML privileges on tables created in it later. Whether the
product uses one schema or one per domain is still open (docs/roadmap.md,
D10); this namespace is valid under either answer.
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0001_leadstack_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_APP_ROLE = "saas_os_app"


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", _DEFAULT_APP_ROLE)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", role):
        raise ValueError(f"APP_DB_USER must be a plain SQL identifier, got: {role!r}")
    return role


def upgrade() -> None:
    app_role = _app_role()
    op.execute("CREATE SCHEMA leadstack")
    op.execute(f'GRANT USAGE ON SCHEMA leadstack TO "{app_role}"')
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA leadstack "
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_role}"'
    )


def downgrade() -> None:
    app_role = _app_role()
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA leadstack "
        f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM "{app_role}"'
    )
    op.execute(f'REVOKE USAGE ON SCHEMA leadstack FROM "{app_role}"')
    # RESTRICT (the default): refuses to drop while anything still lives in
    # the schema -- this downgrade must never cascade over data.
    op.execute("DROP SCHEMA leadstack")
