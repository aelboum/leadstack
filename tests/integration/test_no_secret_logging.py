"""Startup and failure paths never write credentials to the logs.

Exercises the paths most likely to leak a connection string: successful
app startup, a readiness probe against an unreachable Redis whose URL
carries a password, and worker startup failures."""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from leadstack.app import create_app
from leadstack.worker import main as worker_main
from tests.conftest import clear_platform_config_caches
from tests.integration.conftest import DisposableServices

pytestmark = pytest.mark.integration


def test_app_startup_and_failed_readiness_do_not_log_secrets(
    migrated,
    platform_env: DisposableServices,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    redis_secret = secrets.token_urlsafe(24)
    monkeypatch.setenv("REDIS_URL", f"redis://:{redis_secret}@127.0.0.1:1/0")
    clear_platform_config_caches()

    with TestClient(create_app()) as client:
        assert client.get("/readyz").status_code == 503

    captured = capfd.readouterr()
    logs = captured.out + captured.err
    assert logs, "expected startup/readiness logs to be captured"
    for secret in (platform_env.app_password, redis_secret, platform_env.app_url):
        assert secret not in logs


def test_worker_startup_failure_does_not_log_secrets(
    migrated,
    platform_env: DisposableServices,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    db_secret = secrets.token_urlsafe(24)
    monkeypatch.setenv(
        "DATABASE_URL", f"postgresql+psycopg://leadstack_nobody:{db_secret}@127.0.0.1:1/none"
    )
    clear_platform_config_caches()

    assert worker_main([]) == 1

    captured = capfd.readouterr()
    logs = captured.out + captured.err
    assert "worker_startup_failed" in logs
    assert db_secret not in logs
    assert platform_env.app_password not in logs
