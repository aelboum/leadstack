"""Test-wide configuration.

SaaS-OS resolves configuration at import time in places (importing
`api.platform` registers platform jobs, which reads `REDIS_URL` through the
secrets provider, which requires `ENVIRONMENT`). These defaults make the
default (unit) suite hermetic: they point at addresses where nothing
listens, so no unit test can reach a real service by accident. They are
`setdefault`s -- an explicitly exported value always wins -- and integration
tests replace them with disposable services (tests/integration/conftest.py).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pytest

UNREACHABLE_DATABASE_URL = (
    "postgresql+psycopg://unit:unit@127.0.0.1:1/unit"  # pragma: allowlist secret
)
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/0"

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", UNREACHABLE_DATABASE_URL)
os.environ.setdefault("MIGRATIONS_DATABASE_URL", UNREACHABLE_DATABASE_URL)
os.environ.setdefault("REDIS_URL", UNREACHABLE_REDIS_URL)


def clear_platform_config_caches() -> None:
    """SaaS-OS caches configuration, engines and the secrets provider as
    process-wide singletons. A test that changes the environment must clear
    them, and dispose of the cached engine so no connection outlives it."""
    from core.email import get_email_config
    from infra.db import (
        get_database_config,
        get_engine,
        get_migrations_database_config,
        get_session_factory,
    )
    from infra.jobs import get_jobs_config
    from infra.secrets import get_secrets_provider

    if get_engine.cache_info().currsize:
        get_engine().dispose()
    for cached in (
        get_engine,
        get_session_factory,
        get_database_config,
        get_migrations_database_config,
        get_jobs_config,
        get_secrets_provider,
        get_email_config,
    ):
        cached.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_platform_config() -> Iterator[None]:
    clear_platform_config_caches()
    yield
    clear_platform_config_caches()


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    """SaaS-OS's `configure_logging()` replaces the root logger's handlers
    (`basicConfig(force=True)`); restore them so a handler bound to one
    test's captured stream never leaks into the next test."""
    root = logging.getLogger()
    level, handlers = root.level, root.handlers[:]
    yield
    for handler in root.handlers[:]:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
