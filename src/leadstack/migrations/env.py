"""LeadStack's own Alembic environment (SaaS-OS ADR-0016).

Independent of SaaS-OS's environment: its own script directory and the
default `alembic_version` table. Connects as the schema-owner role through
SaaS-OS's `get_migrations_database_config()` -- the same database, the same
secrets plumbing, never the application role.

Ordering guard: refuses to run unless SaaS-OS's history is at the head the
installed `saas-os` package ships. LeadStack tables will take foreign keys
into `core.*`, so running ahead of the platform schema must fail loudly, not
half-apply. Use `leadstack-migrate upgrade`, which runs both in order.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from infra.db import get_migrations_database_config
from infra.db.migration_runner import core_head_revision, current_core_revision
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


class SaaSOSMigrationsNotAppliedError(RuntimeError):
    pass


def _require_platform_schema_at_head() -> None:
    current, head = current_core_revision(), core_head_revision()
    if current != head:
        raise SaaSOSMigrationsNotAppliedError(
            f"SaaS-OS migrations are at {current!r}, the installed package ships {head!r}. "
            "Apply them first: `leadstack-migrate upgrade` (or `saas-os-migrate upgrade`)."
        )


def run_migrations_offline() -> None:
    context.configure(
        url=get_migrations_database_config().url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _require_platform_schema_at_head()
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_migrations_database_config().url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
