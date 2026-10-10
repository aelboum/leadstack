"""Two independent migration histories, applied in SaaS-OS-first order, in
one disposable database (SaaS-OS ADR-0016). Runs against its own database,
separate from the session-wide `migrated` one, because it exercises the
un-migrated and rolled-back states."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from infra.db.migration_runner import core_head_revision
from psycopg import sql

from leadstack.migrate import build_alembic_config, downgrade, upgrade
from tests.conftest import clear_platform_config_caches
from tests.integration.conftest import APP_ROLE, DisposableServices

pytestmark = pytest.mark.integration

# The newest LeadStack revision, read from the shipped script directory.
PRODUCT_HEAD = ScriptDirectory.from_config(build_alembic_config()).get_current_head()


@pytest.fixture
def fresh_db(services: DisposableServices, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A brand-new, empty database; MIGRATIONS_DATABASE_URL points at it."""
    root = urlsplit(services.admin_url)._replace(path="/postgres").geturl()
    name = f"leadstack_mig_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(root, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    admin = urlsplit(services.admin_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("APP_DB_USER", APP_ROLE)
    monkeypatch.setenv(
        "MIGRATIONS_DATABASE_URL",
        f"postgresql+psycopg://{admin.username}:{admin.password}@{admin.hostname}:{admin.port}/{name}",
    )
    clear_platform_config_caches()
    try:
        yield admin._replace(path=f"/{name}").geturl()
    finally:
        clear_platform_config_caches()
        with psycopg.connect(root, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _scalar(url: str, query: str, *params: object) -> object:
    with psycopg.connect(url) as conn:
        row = conn.execute(query, params).fetchone()  # type: ignore[arg-type]
        return None if row is None else row[0]


def _version(url: str, table: str) -> object:
    if not _scalar(url, "SELECT to_regclass(%s) IS NOT NULL", f"public.{table}"):
        return None
    return _scalar(url, f"SELECT version_num FROM public.{table}")


def test_product_history_refuses_to_run_before_platform_history(fresh_db: str) -> None:
    with pytest.raises(RuntimeError, match="SaaS-OS migrations are at None"):
        command.upgrade(build_alembic_config(), "head")

    assert _version(fresh_db, "alembic_version") is None
    assert not _scalar(fresh_db, "SELECT count(*) FROM pg_namespace WHERE nspname = 'leadstack'")


def test_upgrade_applies_both_histories_in_order_with_separate_version_tables(
    fresh_db: str,
) -> None:
    upgrade()

    assert _version(fresh_db, "alembic_version_saas_os") == core_head_revision()
    assert _version(fresh_db, "alembic_version") == PRODUCT_HEAD
    assert _scalar(fresh_db, "SELECT count(*) FROM pg_namespace WHERE nspname = 'leadstack'") == 1
    assert _scalar(fresh_db, "SELECT to_regclass('core.tenants') IS NOT NULL") is True
    assert (
        _scalar(fresh_db, "SELECT has_schema_privilege(%s, 'leadstack', 'USAGE')", APP_ROLE) is True
    )
    assert (
        _scalar(fresh_db, "SELECT has_schema_privilege(%s, 'leadstack', 'CREATE')", APP_ROLE)
        is False
    )

    upgrade()  # re-running is a no-op, not an error
    assert _version(fresh_db, "alembic_version") == PRODUCT_HEAD


def test_product_rollback_leaves_platform_history_untouched(fresh_db: str) -> None:
    upgrade()

    downgrade("base")

    assert _version(fresh_db, "alembic_version") is None
    assert not _scalar(fresh_db, "SELECT count(*) FROM pg_namespace WHERE nspname = 'leadstack'")
    assert _version(fresh_db, "alembic_version_saas_os") == core_head_revision()
    assert _scalar(fresh_db, "SELECT to_regclass('core.tenants') IS NOT NULL") is True

    upgrade()  # and the rollback is reversible
    assert _version(fresh_db, "alembic_version") == PRODUCT_HEAD


def test_workspace_tables_roll_back_one_revision(fresh_db: str) -> None:
    upgrade()
    tables = ("workspaces", "invitation_grants", "user_workspaces")
    for table in tables:
        assert _scalar(fresh_db, "SELECT to_regclass(%s) IS NOT NULL", f"leadstack.{table}")
    assert _scalar(
        fresh_db,
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE oid = 'leadstack.workspaces'::regclass",
    )
    assert _scalar(
        fresh_db, "SELECT has_table_privilege(%s, 'leadstack.workspaces', 'SELECT')", APP_ROLE
    )

    downgrade("0001_leadstack_schema")

    assert _version(fresh_db, "alembic_version") == "0001_leadstack_schema"
    for table in tables:
        assert not _scalar(fresh_db, "SELECT to_regclass(%s) IS NOT NULL", f"leadstack.{table}")
    assert _scalar(fresh_db, "SELECT count(*) FROM pg_namespace WHERE nspname = 'leadstack'") == 1


def test_migrations_apply_with_a_non_superuser_owner(
    services: DisposableServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production runs migrations as a schema owner that is neither a
    superuser nor able to create roles (deploy/postgres/10-roles.sh). The
    other tests here use the test server's superuser, which would hide a
    migration that needs more privileges than production grants."""
    import secrets

    admin = urlsplit(services.admin_url)
    root = admin._replace(path="/postgres").geturl()
    suffix = uuid.uuid4().hex[:10]
    owner, database = f"leadstack_owner_{suffix}", f"leadstack_own_{suffix}"
    password = secrets.token_urlsafe(24)
    with psycopg.connect(root, autocommit=True) as conn:
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}"
            ).format(sql.Identifier(owner), sql.Literal(password))
        )
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database), sql.Identifier(owner)
            )
        )
        conn.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(APP_ROLE)
            )
        )
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("APP_DB_USER", APP_ROLE)
    monkeypatch.setenv(
        "MIGRATIONS_DATABASE_URL",
        f"postgresql+psycopg://{owner}:{password}@{admin.hostname}:{admin.port}/{database}",
    )
    clear_platform_config_caches()
    url = admin._replace(path=f"/{database}").geturl()
    try:
        upgrade()

        assert _version(url, "alembic_version_saas_os") == core_head_revision()
        assert _version(url, "alembic_version") == PRODUCT_HEAD
        assert (
            _scalar(url, "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = %s", owner)
            is False
        )
        owners = _scalar(
            url,
            "SELECT count(DISTINCT tableowner) FROM pg_tables WHERE schemaname = 'leadstack'",
        )
        assert owners == 1
        assert (
            _scalar(url, "SELECT has_schema_privilege(%s, 'leadstack', 'USAGE')", APP_ROLE) is True
        )
        unprotected = _scalar(
            url,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'leadstack' AND c.relkind = 'r' AND NOT c.relforcerowsecurity",
        )
        assert unprotected == 3  # user_workspaces, form_keys, booking_keys (reviewed)
    finally:
        clear_platform_config_caches()
        with psycopg.connect(root, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
            )
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner)))
