# LeadStack

An agency CRM and marketing-automation product, built on the SaaS-OS
platform as an external, pinned package dependency. Phase 1 (foundation)
contains no product features yet. See `docs/roadmap.md`.

- Project instructions: `CLAUDE.md`
- Architecture: `docs/architecture/overview.md`
- SaaS-OS usage and known platform limitations: `docs/architecture/saas-os-integration.md`
- Decisions: `docs/adr/`

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/) (`uv.lock` is authoritative)
- Read access to the private SaaS-OS repository through your Git credential
  helper (for example `gh auth login`). Never put a token in a URL.
- Docker, for the integration tests' disposable PostgreSQL 16 and Redis 7

## Setup

```
uv sync --locked
cp .env.example .env    # local-only values; .env is gitignored
```

## Checks

```
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
uv run pytest                    # unit tests (hermetic; no services needed)
```

Integration tests need disposable services. Never point them at a shared or
production server: each run creates its own uniquely named database and
drops only that database afterwards.

```
docker run -d --name leadstack-test-pg -e POSTGRES_USER=leadstack_admin \
  -e POSTGRES_PASSWORD=<local-only> -p 127.0.0.1:55440:5432 postgres:16-alpine
docker run -d --name leadstack-test-redis -p 127.0.0.1:63800:6379 redis:7-alpine

LEADSTACK_TEST_ADMIN_DATABASE_URL=postgresql://leadstack_admin:<local-only>@127.0.0.1:55440/postgres \
LEADSTACK_TEST_REDIS_URL=redis://127.0.0.1:63800/0 \
uv run pytest -m integration
```

Security checks (the same commands CI runs):

```
uv export --frozen --format requirements-txt --no-emit-project --no-emit-package saas-os --all-groups -o audit.txt
uv run pip-audit -r audit.txt --require-hashes --disable-pip
git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline
```

## Running

```
uv run leadstack-migrate upgrade              # SaaS-OS migrations, then LeadStack's
uv run uvicorn --factory leadstack.app:create_app
uv run leadstack-worker
```

`DATABASE_URL` must use the restricted application role. The app and worker
refuse to start under a superuser or BYPASSRLS role. That role is created
by the deployment (NOSUPERUSER NOBYPASSRLS), because SaaS-OS does not ship
its role-creation script in the package.

## CI

`.github/workflows/ci.yml` runs lint, format, types, import contracts, unit
tests, integration tests against PostgreSQL 16 and Redis 7 service
containers, pip-audit and detect-secrets. It needs a repository secret,
`SAAS_OS_READ_TOKEN`: a read-only credential (Contents: read) for
`aelboum/saas-os` only.
