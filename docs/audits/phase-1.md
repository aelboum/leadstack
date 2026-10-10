# Phase 1 audit report: repository foundation and SaaS-OS integration

Date: 2026-10-09

## 1. Verdict

**PASS WITH LIMITATIONS.**

All 13 acceptance criteria are met by evidence gathered locally. The
limitation: the CI workflow has not run on GitHub. There is no remote and no
commit, and the `SAAS_OS_READ_TOKEN` secret is not configured. Every command
in CI was run locally on Windows with the same tool versions, but the Linux
CI run itself is unverified.

## 2. Baseline

- Path: `C:\Users\imran\Documents\Leadstack`.
- Initial contents: `CLAUDE.md` only (40,819 bytes, unchanged since the
  bootstrap audit: same size and modification time,
  2026-10-09 15:51:11 +02:00). No Git repository.
- SHA-256 of `CLAUDE.md`:
  `6b5125958ffe8964c159f07cf7bb37617d2ad22b5b8f7f5750d1f447adf4300b`,
  identical at the start and end of the phase.
- SaaS-OS `main` head was still `b126f1ad0c9cf4173ada15e20b14850d9e600c14`
  (checked with `git ls-remote`).
- Tools:
  - Python 3.13.14, already installed and found by uv.
  - uv 0.12.3.
  - Docker 29.7.2.
  - gh, authenticated through the keyring.
- Private-repository access works through the local Git credential helper.
  No token was printed or written.

## 3. Changes

All files are new and untracked. Created:

- **Project:** `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`,
  `.env.example`, `alembic.ini`, `README.md`, `.secrets.baseline`.
- **Package `src/leadstack/`:**
  - `app.py`: `create_app()` through `build_platform_app()`.
  - `routes/`: `GET /v1/meta`.
  - `jobs.py`: the `leadstack_noop` job.
  - `worker.py`: platform jobs plus product jobs, and `--check`.
  - `migrate.py`: an ordered two-history migration command.
  - `migrations/`: Alembic environment with an ordering guard, and
    revision `0001_leadstack_schema`.
  - `domains/__init__.py`: anchors the import contracts.
- **Tests:** `tests/unit/` (16 tests) and `tests/integration/` (15 tests).
- **CI:** `.github/workflows/ci.yml`.
- **Documentation:**
  - `docs/architecture/overview.md` and `saas-os-integration.md`
  - `docs/roadmap.md`
  - `docs/adr/0001` to `0003`
  - this report

No CRM or other product feature was implemented.

## 4. Architecture

- SaaS-OS is consumed as a built, non-editable package from the pinned
  commit.
- LeadStack builds its own app with `api.platform.build_platform_app()` and
  never imports `api.main` or `api.server`.
- Migrations follow SaaS-OS ADR-0016: two histories with separate version
  tables. `leadstack-migrate upgrade` enforces the SaaS-OS-first order.
  LeadStack's Alembic environment refuses to run unless the platform history
  is at the installed package's head.
- The worker reuses `api.worker.registered_job_functions()` and
  `infra.jobs.build_worker()`, because SaaS-OS's own worker has no consumer
  extension point. No scheduler was introduced.
- Import-linter enforces four contracts. Each was proven to fail on a
  deliberate violation, in a scratch copy outside the repository.

## 5. Verification

Commands that were actually run, and their results:

**Dependency and lockfile**

- `uv lock` resolved 79 packages. `uv lock --check` passes.
- `uv sync --locked` passes.
- `direct_url.json` of the installed `saas-os` shows commit `b126f1a…`,
  `vcs: git` and no editable flag.
- No SaaS-OS module resolves outside `site-packages`. `products` is not
  installed.

**Imports from outside any checkout**

- Run with `python -I` from a scratch directory, they succeed for
  `api.platform`, `core.tenancy`, `infra.db` and
  `control_plane.orchestration`.
- They require `ENVIRONMENT` and `REDIS_URL` to be set. See limitation 2.

**Clean, reproducible install**

- A fresh copy was installed with `uv sync --locked --no-cache`, fetching
  SaaS-OS from GitHub again.
- Its package set is identical to the development environment's. The only
  difference is LeadStack's own editable path.

**LeadStack wheel**

- `uv build --wheel` succeeds.
- The wheel contains the migration environment and revision.

**Static checks**

- `ruff check .`: passes.
- `ruff format --check .`: 33 files formatted.
- `pyright`: 0 errors.
- `lint-imports --no-cache`: 4 contracts kept, 0 broken.

**Tests**

- `pytest` (unit): 16 passed.
- `pytest -m integration`: 15 passed, run with
  `LEADSTACK_REQUIRE_INTEGRATION=1` against disposable `postgres:16-alpine`
  and `redis:7-alpine` containers on Windows.

  | Area | Tests |
  |---|---|
  | Migrations | 3 |
  | Readiness | 3 |
  | Role guard | 5 |
  | Worker runtime | 2 |
  | Secret logging | 2 |

**Security tools**

- `pip-audit` on the hash-locked export (77 packages): no known
  vulnerabilities.
- `detect-secrets-hook` against the baseline: clean. It exits 1 on a
  planted secret.

### Migration verification

- Running LeadStack's history before SaaS-OS's fails, and leaves no product
  version table and no schema behind.
- `upgrade` writes:
  - `alembic_version_saas_os` = the package head;
  - `alembic_version` = `0001_leadstack_schema`.
- The `leadstack` schema exists. The application role has USAGE on it but
  not CREATE. Re-running `upgrade` changes nothing.
- `downgrade base` removes the product schema and version, and leaves the
  SaaS-OS history and `core.tenants` intact. Upgrading again succeeds.
- Each run used uniquely named, freshly created databases. They were
  dropped afterwards, along with the test role that has BYPASSRLS.

### Health and readiness

- `/healthz` returns 200.
- `/readyz` returns 200 only when the real `SELECT 1` and `PING` both
  succeed. It returns 503 when Redis is down, when PostgreSQL is down, or
  when both are unreachable.

## 6. Security

- **Database-role guard:** both the app and the worker refuse to start under
  a superuser role and under a NOSUPERUSER BYPASSRLS role. They start under
  the restricted role.
- **Secrets in logs:** startup, a failed readiness probe with a
  password-bearing Redis URL, and worker startup failures log none of the
  generated secrets. Worker startup failures log only the exception type.
- **Credentials:** none in `pyproject.toml`, `uv.lock` or the workflow. A
  test enforces this for the manifests. CI injects the read-only credential
  as process-scoped Git configuration from a secret.
- **Default posture:** integration tests fail instead of skipping in CI. A
  missing `SAAS_OS_READ_TOKEN` fails the job with an explicit error.
- **Finding (informational):** the SaaS-OS builder leaves `/docs`, `/redoc`
  and `/openapi.json` enabled. The single-origin edge does not route them,
  but a deployment that exposes the backend directly would serve them.
  Revisit before production (roadmap D9).
- **Isolation:** no tenant-owned tables exist yet, so cross-tenant tests
  start with Phase 2/3.

## 7. Limitations

1. CI has not run on GitHub. It needs a remote, a commit, and the
   `SAAS_OS_READ_TOKEN` secret. Integration tests ran on Windows, not
   Linux. GitHub Actions are pinned by major tag, not by SHA.
2. Importing `api.platform` needs `ENVIRONMENT` and `REDIS_URL` set,
   because SaaS-OS registers jobs at import time.
3. The worker depends on two functions in `api.worker`, which SaaS-OS
   ADR-0017 does not classify as public. A unit test detects changes.
4. `saas-os` itself cannot be audited by pip-audit, because it is not on
   PyPI. Its dependencies are audited.
5. On Windows, arq cannot install signal handlers, so the worker process
   test ends the process with terminate. Graceful SIGTERM shutdown is
   unverified until CI runs on Linux.
6. The application role in a real deployment must be created by the
   operator. SaaS-OS does not ship its role script.
7. The SaaS-OS platform limitations are listed in
   `docs/architecture/saas-os-integration.md`.

## 8. Repository state

- Git initialised on `main`. No commits, nothing staged, no remote, nothing
  pushed.
- All files are untracked. `.venv/` is gitignored.
- During the phase, one listing command briefly marked files
  intent-to-add and immediately reset the index. The index is empty.
- SaaS-OS was not modified. The read-only scratch clone has no changes
  and is at `b126f1a…`.
- The disposable test containers were removed.

## 9. Remaining work

- **High:** push and run CI on GitHub with the read-only secret. Decide D4
  before Phase 2.
- **Medium:** add a static check for queries without tenant context before
  the first domain table. Pin GitHub Actions by SHA.
- **Open decisions:** D5–D11 in `docs/roadmap.md`.

## 10. Next step

**Phase 2: workspace tenancy integration**

Scope:
- provision agency and client tenants through `core.tenancy`;
- register LeadStack permissions and roles through `core.rbac`;
- HTTP routes for membership and invitations under `/v1`, protected by
  `api.dependencies.require_permission`;
- audit events through `core.audit_log`.

Acceptance criteria:
- cross-tenant read and write attempts return 404;
- a suspended tenant is inaccessible;
- an invitation can be used once only, and replay fails;
- authorization is not granted by a client-supplied tenant or role;
- denials are audited.

Prerequisites: decision D4 and a green CI run on GitHub.
