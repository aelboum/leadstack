"""Readiness reflects the real state of PostgreSQL and Redis, through the
full application lifespan (including the database-role guard)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from leadstack.app import create_app
from tests.conftest import UNREACHABLE_REDIS_URL, clear_platform_config_caches
from tests.integration.conftest import DisposableServices

pytestmark = pytest.mark.integration


def _checks(body: dict) -> dict[str, str]:
    return {check["name"]: check["status"] for check in body["checks"]}


def test_ready_when_database_and_redis_are_reachable(migrated, platform_env) -> None:
    with TestClient(create_app()) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert _checks(response.json()) == {"database": "healthy", "redis": "healthy"}


def test_not_ready_when_redis_is_down(
    migrated, platform_env: DisposableServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REDIS_URL", UNREACHABLE_REDIS_URL)
    clear_platform_config_caches()

    with TestClient(create_app()) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert _checks(response.json()) == {"database": "healthy", "redis": "unhealthy"}


def test_not_ready_when_database_is_down(
    migrated, platform_env: DisposableServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Start healthy (the lifespan role guard needs the database), then point
    # readiness at an unreachable database: readiness builds its own engine
    # from configuration on every probe.
    with TestClient(create_app()) as client:
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/none")
        from infra.db import get_database_config
        from infra.secrets import get_secrets_provider

        get_secrets_provider.cache_clear()
        get_database_config.cache_clear()
        response = client.get("/readyz")

    assert response.status_code == 503
    assert _checks(response.json()) == {"database": "unhealthy", "redis": "healthy"}
