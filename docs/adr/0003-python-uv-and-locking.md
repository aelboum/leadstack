# ADR-0003: Python 3.13, uv, and dependency locking

Status: Accepted (approved as decisions D1 and D2, 2026-10-09)

## Context

SaaS-OS at the pinned commit requires Python >= 3.13 and is built on
FastAPI, SQLAlchemy 2, Alembic, psycopg 3, Redis and arq (SaaS-OS ADR-0011).
SaaS-OS itself uses pip and setuptools. CLAUDE.md requires reproducible
builds and an exact, recorded SaaS-OS pin.

## Decision

- Python 3.13 only (`requires-python = ">=3.13,<3.14"`, `.python-version`).
  Moving to 3.14 is a deliberate, tested change.
- uv manages the environment and the lockfile. `uv.lock` pins every
  package (with hashes for index packages) and the SaaS-OS Git commit.
  `uv sync --locked` is the only supported install; CI also runs
  `uv lock --check`.
- Build backend: hatchling, `src/` layout, a single `leadstack` top-level
  package. LeadStack must never define its own top-level `api`, `core`,
  `infra`, `contracts` or `control_plane` package, because the SaaS-OS
  wheel installs those names.
- Development tools (pytest, ruff, pyright, import-linter, pip-audit,
  detect-secrets) live in the `dev` dependency group. detect-secrets is
  pinned exactly so the baseline stays valid.
- The same backend stack as SaaS-OS; no second web framework, ORM, or job
  system.

## Alternatives considered

- pip with pip-tools: matches SaaS-OS's own toolchain, but uv was already
  installed, resolves and locks Git dependencies to a commit natively, and
  provides `--locked`/`--check` enforcement.
- Poetry: not installed; no advantage here.

## Consequences

- Developers need uv and a Python 3.13 interpreter.
- SaaS-OS and LeadStack use different installers for the same package
  format. Both consume standard metadata, so this does not affect
  compatibility.

## Security implications

Hash-verified installs and a lock check reduce supply-chain drift.
pip-audit runs against the hash-locked export.

## Reversal

Switching to pip-tools means generating a hashed requirements file from
the same `pyproject.toml`; no code changes.
