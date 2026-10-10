"""`leadstack-migrate` -- apply both migration histories in the required order.

SaaS-OS ADR-0016 gives one physical database two independent Alembic
histories:

- SaaS-OS's own (`core.*`, `control_plane.*`, ...), tracked in
  `alembic_version_saas_os`, shipped inside the installed `saas-os` package
  and applied through `infra.db.run_core_migrations()`;
- LeadStack's own (`leadstack.*`), tracked in the default `alembic_version`
  table, script directory `leadstack/migrations/`.

The ADR fixes the order (SaaS-OS first) but ships nothing that enforces it.
This command enforces it:

    leadstack-migrate upgrade          # SaaS-OS to head, then LeadStack to head
    leadstack-migrate downgrade <rev>  # LeadStack history only
    leadstack-migrate current          # both recorded revisions

LeadStack's own environment additionally refuses to run unless SaaS-OS's
history is at the head the installed package ships (`migrations/env.py`),
so a bare `alembic upgrade` cannot run out of order either.

Downgrade never touches SaaS-OS's history: rolling the platform schema back
is a separate, deliberate operation (`saas-os-migrate downgrade`).

Connection: `MIGRATIONS_DATABASE_URL` (the schema-owner role) through
SaaS-OS's secrets provider -- never the application role in `DATABASE_URL`.
"""

from __future__ import annotations

import argparse
import sys
from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from infra.db import get_migrations_database_config, run_core_migrations
from infra.db.migration_runner import core_head_revision, current_core_revision
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def build_alembic_config() -> Config:
    """Alembic configuration for LeadStack's own history, resolved from the
    installed package (works the same from a source tree or a wheel)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(files("leadstack") / "migrations"))
    return cfg


def upgrade(revision: str = "head") -> None:
    run_core_migrations()
    command.upgrade(build_alembic_config(), revision)


def downgrade(revision: str) -> None:
    command.downgrade(build_alembic_config(), revision)


def current_product_revision() -> str | None:
    engine = create_engine(get_migrations_database_config().url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="leadstack-migrate",
        description="Apply SaaS-OS's migrations, then LeadStack's (SaaS-OS ADR-0016 order).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("upgrade", help="SaaS-OS to head, then LeadStack to a revision.")
    up.add_argument("revision", nargs="?", default="head")
    down = sub.add_parser("downgrade", help="Roll back LeadStack's own history only.")
    down.add_argument("revision")
    sub.add_parser("current", help="Show both recorded revisions.")
    args = parser.parse_args(argv)

    if args.command == "upgrade":
        upgrade(args.revision)
    elif args.command == "downgrade":
        downgrade(args.revision)
    else:
        print(f"saas-os:   {current_core_revision()} (package head {core_head_revision()})")
        print(f"leadstack: {current_product_revision()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
