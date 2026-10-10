# Phase 14 groundwork report: deployment files, guards and runbooks

Date: 2026-10-10

## 1. Verdict

**PASS WITH LIMITATIONS** for the groundwork only. Phase 14 itself
(production hardening and release) is **not complete**, and nothing has
been deployed.

- LeadStack now owns a single-host deployment definition (decision D9). It
  has been reviewed, and the parts that could be run locally were run.
- **The backend image has never been built.** The private SaaS-OS
  dependency needs a read token at build time, and none was available to
  the build. Everything about the backend image comes from reading, not
  running.
- **The full stack has never been started together.**
- A decision is required before first use: D14, encryption between the
  application and the data stores.

## 2. What was added

- `deploy/Dockerfile.backend`: API, worker, migrations and admin commands
  in one image.
  - Runs as non-root.
  - Installs exactly the locked dependencies.
  - The SaaS-OS token is a BuildKit secret.
  - Forwarded headers are trusted from an explicit address only, with no
    default.
- `deploy/Dockerfile.frontend`: Next.js standalone server, non-root.
- `deploy/compose.yaml`:
  - services: edge (Caddy), frontend, api, worker, a one-shot migration
    job, and optional bundled PostgreSQL and Redis (profile `bundled`);
  - separate front, data (internal) and egress networks;
  - log rotation and health checks.
- `deploy/Caddyfile`:
  - automatic TLS and routing;
  - an unforgeable client address header;
  - health endpoints hidden;
  - security headers;
  - access logs without URIs, referrers, redirect targets or cookies.
- `deploy/postgres/10-roles.sh`: the two database roles, with no extra
  privileges.
- `deploy/production.env.example`: placeholders only.
- `.dockerignore`.
- `frontend/next.config.ts`:
  - standalone output for the image build only;
  - `Referrer-Policy: no-referrer` on the confirmation and unsubscribe
    pages.
- Docs:
  - `docs/adr/0009-deployment-topology.md` (status: proposed);
  - `docs/operations/deployment.md`, `backup-restore.md` and `runbook.md`.
- `tests/unit/test_deploy_config.py`: 15 guard tests.

No SaaS-OS file was copied. The SaaS-OS commit pin is unchanged.

## 3. Checks actually run

| Check | Result |
|---|---|
| `docker compose config`, with and without the `bundled` profile | Accepted |
| `caddy validate` on the Caddyfile | Valid configuration |
| Bundled PostgreSQL and Redis started from the compose file | Both healthy; Redis refuses commands without the password |
| Role script on PostgreSQL 16 | Two roles, neither superuser nor BYPASSRLS nor CREATEROLE nor CREATEDB; an empty password is refused |
| Full migration chain with the owner role (SaaS-OS head, then LeadStack `0009_scheduling`) | Applied; exit 0 |
| SaaS-OS role guard with the application role | Accepted |
| Tables without row-level security after migration | Only the three reviewed index tables |
| Frontend image build and run (read-only, no capabilities, non-root) | Pages return 200; security headers present; health check passes; `no-referrer` on `/c/` and `/u/` |
| Guard tests | 15 passed |
| Frontend checks after the review fixes | tsc, eslint, 37 vitest tests passed |

Full gate, run on 2026-10-10 with the deployment files and the new frontend
screens in place:

- ruff, format, pyright (0 errors) and import contracts (4 kept): passed;
- `uv lock --check`: consistent;
- 89 unit tests and 250 integration tests passed;
- detect-secrets: clean.

The review fixes that followed changed only deployment files, docs, the
frontend and the guard tests; no backend source changed. Afterwards these
were rerun and passed: ruff, format, pyright, 93 unit tests and
detect-secrets. The integration suite was not rerun.

All test containers and images created for these checks were removed.

Defects found by running rather than reading:

- **Frontend image.** The first frontend image did not start: Next.js
  needed TypeScript at runtime to read `next.config.ts`. It was replaced by
  the standalone build, which also cut the image from 813 MB to 392 MB.
- **Database and Redis TLS.** SaaS-OS refused the database connection in
  production mode without TLS. The same rule applies to Redis. This became
  decision D14.

## 4. Independent review and resolution

Verdict of the review: PASS WITH LIMITATIONS, with no critical or high
finding and no secret leak.

| ID | Finding | Resolution |
|---|---|---|
| M1 | Database health check could pass during initialisation; the migration job would then fail and block the stack | Health check over TCP; verified with the compose file |
| M2 | Documented `compose run api ...` commands re-run the migration job with owner credentials | Docs use `exec -T worker`; guard test |
| M3 | Link tokens reached access logs through `Referer` | Referer, Location and Set-Cookie removed from logs; `no-referrer` on token pages (verified) |
| M4 | TLS data stores could not be configured without editing compose; no OIDC client secret | URLs overridable; bundled stores behind a profile; `OIDC_CLIENT_SECRET` passed through |
| M5 | One flat network; unauthenticated Redis reachable from the edge and frontend (jobs are deserialized by the worker) | Front and internal data networks; Redis password required (verified) |
| M6 | API responses had no HSTS or nosniff | Set at the edge |
| Low | Owner role had CREATEROLE | Removed; migration chain re-verified |
| Low | No log rotation; no frontend health check; `.dockerignore` matched root `.env` files only | Fixed |
| Low | Images pinned by tag, not digest; no resource limits; fixed subnet | Documented in ADR-0009 as open work |
| Low | Worker start-up errors log only the exception type | Documented in the runbook |

The frontend review findings were also fixed: stale booking windows, the
selection surviving a window change, an unguarded "load more", times
without a zone, and a failed session lookup. They are covered by the
frontend checks above.

The reviewer confirmed from the source:

- the token never reaches an image layer or history;
- the wheel install includes the migrations and the three commands;
- `/readyz` and the worker check exist;
- the same-origin check and the login redirect work behind the proxy,
  because they derive from `PUBLIC_APP_ORIGIN`.

### Added after the final end-to-end audit

- `deploy/production.env` was **not** git-ignored when this report was
  first written, although the template said it was. It is now ignored,
  with a guard test for git and Docker.
- CI now validates the compose file and the edge configuration, and builds
  and starts the frontend image. The SaaS-OS token is scoped to the Python
  jobs only.
- An integration test runs both migration histories as a non-superuser
  owner.

## 5. Limitations and remaining Phase 14 work

- Build the backend image, with the SaaS-OS read token as a BuildKit
  secret, and start the full stack once.
- Verify end to end: certificate issuance, sign-in through the edge, a
  public form, a booking and an email.
- Decide D14, the hosting provider and region, and data residency.
- Backups: automation, encryption, off-host storage and a passed restore
  test.
- Observability: metrics and trace exporter, log shipping, alerts.
- A scheduler for the recurring commands (D6).
- Pin image digests; set resource limits after load testing.
- Secret management beyond a host file.
- Proxy header behaviour is verified from source only; it has not been
  tested through a running edge.

## 6. Repository state

Untracked only. No commit, push, image publication or deployment. SaaS-OS
untouched.
