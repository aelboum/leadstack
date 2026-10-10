"""Negative security tests: neither the HTTP app nor the worker may start
under a database role that bypasses Row-Level Security (SaaS-OS's
`infra.db.validate_application_role`, run by both entrypoints)."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from urllib.parse import urlsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from infra.db import UnsafeDatabaseRoleError
from psycopg import sql

from leadstack.app import create_app
from leadstack.worker import main as worker_main
from tests.conftest import clear_platform_config_caches
from tests.integration.conftest import DisposableServices

pytestmark = pytest.mark.integration

BYPASS_ROLE = "leadstack_test_bypassrls"


@pytest.fixture
def bypassrls_url(services: DisposableServices) -> Iterator[str]:
    """A NOSUPERUSER role that has BYPASSRLS -- unsafe even though it is not
    a superuser."""
    password = secrets.token_urlsafe(24)
    root = urlsplit(services.admin_url)._replace(path="/postgres").geturl()
    with psycopg.connect(root, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER BYPASSRLS PASSWORD {}").format(
                sql.Identifier(BYPASS_ROLE), sql.Literal(password)
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(services.database), sql.Identifier(BYPASS_ROLE)
            )
        )
    try:
        yield services.sqlalchemy_url(BYPASS_ROLE, password)
    finally:
        clear_platform_config_caches()
        with psycopg.connect(root, autocommit=True) as conn:
            conn.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
                    sql.Identifier(services.database), sql.Identifier(BYPASS_ROLE)
                )
            )
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(BYPASS_ROLE)))


@pytest.mark.parametrize("role", ["superuser", "bypassrls"])
def test_app_refuses_to_start_under_unsafe_role(
    role: str,
    platform_env: DisposableServices,
    bypassrls_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe = platform_env.migrations_url if role == "superuser" else bypassrls_url
    monkeypatch.setenv("DATABASE_URL", unsafe)
    clear_platform_config_caches()

    with pytest.raises(UnsafeDatabaseRoleError), TestClient(create_app()):
        pass


def test_app_starts_under_restricted_role(migrated, platform_env: DisposableServices) -> None:
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("role", ["superuser", "bypassrls"])
def test_worker_refuses_to_start_under_unsafe_role(
    role: str,
    platform_env: DisposableServices,
    bypassrls_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe = platform_env.migrations_url if role == "superuser" else bypassrls_url
    monkeypatch.setenv("DATABASE_URL", unsafe)
    clear_platform_config_caches()

    assert worker_main([]) == 1
