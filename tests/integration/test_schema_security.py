"""Catalog-level schema security, checked against the real migrated database.

Every LeadStack table with a `tenant_id` column must have row-level
security enabled *and forced*, with at least one policy, unless it is an
explicitly reviewed exception. The application role must never own a
table, because table owners bypass non-forced RLS, and must never be able
to create objects in the schema."""

from __future__ import annotations

import psycopg
import pytest

from tests.integration.conftest import APP_ROLE, DisposableServices

pytestmark = pytest.mark.integration

# Tables with a tenant_id column that are deliberately not tenant-RLS'd.
REVIEWED_EXCEPTIONS = {
    "user_workspaces": "user-keyed index, identifiers only (ADR-0004)",
    "form_keys": "public-key index, identifiers only (ADR-0005)",
    "booking_keys": "public-key index, identifiers only (ADR-0008)",
}


def _rows(services: DisposableServices, query: str, *params: object) -> list[tuple]:
    with psycopg.connect(services.admin_url) as conn:
        return conn.execute(query, params).fetchall()  # type: ignore[arg-type]


def test_every_tenant_table_has_forced_rls_with_a_policy(migrated: DisposableServices) -> None:
    tables = _rows(
        migrated,
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'leadstack' AND c.relkind = 'r'
          AND EXISTS (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
        """,
    )
    assert tables, "no tenant tables found -- the check would be vacuous"
    unprotected = [
        name
        for name, enabled, forced, policies in tables
        if not (enabled and forced and policies) and name not in REVIEWED_EXCEPTIONS
    ]
    assert unprotected == []
    assert set(REVIEWED_EXCEPTIONS) <= {name for name, *_ in tables}


def test_application_role_owns_nothing_and_cannot_create(migrated: DisposableServices) -> None:
    owned = _rows(
        migrated,
        """
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_roles r ON r.oid = c.relowner
        WHERE n.nspname = 'leadstack' AND r.rolname = %s
        """,
        APP_ROLE,
    )
    can_create = _rows(migrated, "SELECT has_schema_privilege(%s, 'leadstack', 'CREATE')", APP_ROLE)
    assert owned == []
    assert can_create == [(False,)]
