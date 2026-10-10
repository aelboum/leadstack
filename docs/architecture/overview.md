# LeadStack architecture overview

LeadStack is an independent product repository. It consumes SaaS-OS as an
external, pinned package (ADR-0001) and never copies or modifies it.

## Shape

A modular monolith on SaaS-OS's stack (ADR-0003): one FastAPI API process,
one arq worker process, one PostgreSQL database with row-level security,
Redis for jobs and rate limits, and ZITADEL (through SaaS-OS) for identity.
A separate frontend arrives with the first user-facing phase.

```
src/leadstack/
  app.py         HTTP composition root: build_platform_app() + product routers
  routes/        HTTP routes, all under /v1
  domains/       business domains (none yet; one subpackage per domain)
  jobs.py        product background jobs
  worker.py      worker entrypoint: platform jobs + product jobs
  migrate.py     applies SaaS-OS's migrations, then LeadStack's
  migrations/    LeadStack's own Alembic history
```

## Dependency rules

Enforced by import-linter (`pyproject.toml`) on every test run and in CI:

1. LeadStack never imports `api.main` or `api.server` (SaaS-OS ADR-0017).
2. `leadstack.domains` never imports SQLAlchemy or a PostgreSQL driver;
   database access goes through `infra.db`.
3. `leadstack.domains` never imports FastAPI, Starlette, SaaS-OS's `api`
   package, or LeadStack's app, routes, worker or migration entrypoints.
4. `leadstack.routes` and `leadstack.jobs` never import SQLAlchemy or a
   driver directly.

Each contract was checked against a deliberate violation during Phase 1.

## Tenancy

See ADR-0002. Agencies are root tenants; client workspaces are child
tenants; direct customers are root tenants with no children. Every
tenant-owned row carries `tenant_id` with RLS, and is read only within a
verified tenant scope.

## Platform boundary

What LeadStack uses from SaaS-OS, and the platform's known limitations,
are in `saas-os-integration.md`. A missing platform capability is
documented and proposed upstream, never rebuilt inside LeadStack without an
approved decision.
