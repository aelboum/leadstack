"""Disposable PostgreSQL 16 and Redis for integration tests.

Required environment (never a shared, long-lived or production server):

    LEADSTACK_TEST_ADMIN_DATABASE_URL  postgresql://<superuser>:<pw>@host:port/postgres
    LEADSTACK_TEST_REDIS_URL           redis://host:port/<db>

Each test session creates a uniquely named database and drops only that
database afterwards. The restricted application role is created the way a
real deployment must create it (NOSUPERUSER NOBYPASSRLS -- SaaS-OS does not
ship its role-creation script in the package), with a random per-run
password. Tests are skipped when the variables are absent, and fail instead when
LEADSTACK_REQUIRE_INTEGRATION=1 (CI).
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import psycopg
import pytest
from psycopg import sql

from tests.conftest import clear_platform_config_caches

APP_ROLE = "leadstack_test_app"
# Per-run random signing secret for public form tokens (test-only).
FORM_TOKEN_SECRET = secrets.token_urlsafe(48)
LINK_SIGNING_SECRET = secrets.token_urlsafe(48)


@dataclass(frozen=True)
class DisposableServices:
    database: str
    admin_url: str  # psycopg form, connected to the disposable database
    migrations_url: str  # SQLAlchemy form, schema owner (superuser in tests)
    app_url: str  # SQLAlchemy form, restricted application role
    app_password: str
    redis_url: str

    def sqlalchemy_url(self, user: str, password: str) -> str:
        parts = urlsplit(self.admin_url)
        return (
            f"postgresql+psycopg://{quote(user)}:{quote(password)}"
            f"@{parts.hostname}:{parts.port}/{self.database}"
        )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        message = f"{name} is not set; integration services unavailable"
        # CI sets LEADSTACK_REQUIRE_INTEGRATION=1 so a misconfigured job fails
        # instead of reporting a green run made only of skips.
        if os.environ.get("LEADSTACK_REQUIRE_INTEGRATION") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return value


def _with_database(url: str, database: str) -> str:
    return urlsplit(url)._replace(path=f"/{database}").geturl()


@pytest.fixture(scope="session")
def services() -> Iterator[DisposableServices]:
    admin_root_url = _require("LEADSTACK_TEST_ADMIN_DATABASE_URL")
    redis_url = _require("LEADSTACK_TEST_REDIS_URL")
    database = f"leadstack_test_{uuid.uuid4().hex[:12]}"
    app_password = secrets.token_urlsafe(24)
    parts = urlsplit(admin_root_url)

    with psycopg.connect(admin_root_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        conn.execute(
            sql.SQL(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
                "CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE "
                "NOREPLICATION; END IF; END $$"
            ).format(name=sql.Literal(APP_ROLE), role=sql.Identifier(APP_ROLE))
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(app_password)
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(APP_ROLE)
            )
        )

    admin_url = _with_database(admin_root_url, database)
    owner = DisposableServices(
        database=database,
        admin_url=admin_url,
        migrations_url="",
        app_url="",
        app_password=app_password,
        redis_url=redis_url,
    )
    resolved = DisposableServices(
        database=database,
        admin_url=admin_url,
        migrations_url=owner.sqlalchemy_url(parts.username or "", parts.password or ""),
        app_url=owner.sqlalchemy_url(APP_ROLE, app_password),
        app_password=app_password,
        redis_url=redis_url,
    )
    try:
        yield resolved
    finally:
        clear_platform_config_caches()
        with psycopg.connect(admin_root_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
            )


@pytest.fixture
def platform_env(
    services: DisposableServices, monkeypatch: pytest.MonkeyPatch
) -> DisposableServices:
    """Point SaaS-OS configuration at the disposable services."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", services.app_url)
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", services.migrations_url)
    monkeypatch.setenv("REDIS_URL", services.redis_url)
    monkeypatch.setenv("APP_DB_USER", APP_ROLE)
    monkeypatch.setenv("LEADSTACK_FORM_TOKEN_SECRET", FORM_TOKEN_SECRET)
    monkeypatch.setenv("LEADSTACK_LINK_SIGNING_SECRET", LINK_SIGNING_SECRET)
    monkeypatch.setenv("PUBLIC_APP_ORIGIN", "http://testserver")
    # SaaS-OS email configuration; tests send through FakeEmailProvider only.
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    monkeypatch.setenv("EMAIL_DEFAULT_SENDER", "noreply@leadstack.test")
    clear_platform_config_caches()
    return services


@pytest.fixture(scope="session")
def migrated(services: DisposableServices) -> DisposableServices:
    """Both migration histories applied, in order, once per session."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ENVIRONMENT", "test")
        mp.setenv("MIGRATIONS_DATABASE_URL", services.migrations_url)
        mp.setenv("DATABASE_URL", services.app_url)
        mp.setenv("APP_DB_USER", APP_ROLE)
        clear_platform_config_caches()
        from leadstack.migrate import upgrade

        upgrade()
        clear_platform_config_caches()
    return services


@dataclass(frozen=True)
class ApiUser:
    user_id: uuid.UUID
    headers: dict[str, str]


@pytest.fixture
def api(migrated: DisposableServices, platform_env: DisposableServices) -> Iterator[object]:
    """The LeadStack app with its full lifespan, against the migrated
    disposable database."""
    from fastapi.testclient import TestClient

    from leadstack.app import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def make_user(api: object):
    """Create a SaaS-OS user with a real session; returns its bearer headers.
    (Authentication is SaaS-OS's session mechanism -- no test-only bypass.)"""
    from core.identity import create_user, issue_session

    def _make() -> ApiUser:
        user = create_user()
        _, token = issue_session(user.id)
        return ApiUser(user_id=user.id, headers={"Authorization": f"Bearer {token}"})

    return _make


@pytest.fixture(autouse=True)
def _fresh_product_limits(services):
    """Public-endpoint and sending limits are keyed by client address or
    hashed recipient, and every test uses the same TestClient address:
    start each test with fresh LeadStack buckets (platform keys untouched)."""
    import redis

    client = redis.Redis.from_url(services.redis_url)
    for pattern in ("leadstack:ratelimit*", "leadstack:confirm-address*"):
        for key in client.scan_iter(pattern):
            client.delete(key)
    yield
    client.close()
