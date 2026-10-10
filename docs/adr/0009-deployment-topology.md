# ADR-0009: Deployment topology (single host, LeadStack-owned)

Status: Proposed (Phase 14 groundwork, 2026-10-10). Decision D9 fixed that
LeadStack owns its deployment configuration. The topology details below
need approval before any real deployment. Nothing has been deployed.

## Context

LeadStack must be deployable independently of SaaS-OS (CLAUDE.md §2.4).
SaaS-OS ships its own deployment files for its own applications; LeadStack
does not copy them (D9). The first production target is a small
installation, Morocco first, with unknown hosting (cloud provider and
region are not decided). SaaS-OS imposes several rules at the pinned commit:

- **Two database roles.** A schema owner is used only for migrations. A
  restricted application role (NOSUPERUSER, NOBYPASSRLS) is used by the API
  and the worker, which refuse to start under a more privileged role.
- **TLS to PostgreSQL and Redis is required in production.** The operator
  can opt out explicitly with `DB_REQUIRE_TLS=false` and
  `REDIS_REQUIRE_TLS=false`.
- **Configuration** is read from the process environment in production.

## Decision (proposed)

One host running Docker Compose (`deploy/compose.yaml`):

| Service | Image | Role |
|---|---|---|
| `edge` | Caddy 2 | TLS termination (automatic certificates), routing, the only published ports |
| `frontend` | LeadStack frontend (Next.js standalone) | Pages |
| `api` | LeadStack backend | HTTP API and the SaaS-OS `/auth/*` login flow |
| `worker` | LeadStack backend | Background jobs (email delivery, automation) |
| `migrate` | LeadStack backend | One-shot: both migration histories, in order, with the owner role |
| `postgres` | PostgreSQL 16 | Database, two roles created at first start |
| `redis` | Redis 7 (append-only file) | Job queue and rate-limit counters |

External services: the identity provider (hosted ZITADEL, D8) and an SMTP
relay.

Rules encoded in the files and guarded by `tests/unit/test_deploy_config.py`:

- **Ports.** Only the edge publishes ports. Health endpoints are not
  exposed to the internet.
- **Network separation.** The edge and the frontend share a front network
  with the API. The database and Redis sit on an internal data network
  that only the API, the worker and the migration job can reach. Jobs are
  deserialized by the worker, so write access to Redis must not be
  available to the internet-facing containers; the bundled Redis also
  requires a password. The worker has a separate outbound network for the
  SMTP relay.
- **Client addresses.** The API trusts `X-Forwarded-For` from the edge's
  fixed address only, and the edge overwrites the header. Without this,
  per-client rate limits would be shared by all visitors or could be
  spoofed (ADR-0005; Phase 6 review M4).
- **Owner credentials.** Only the `migrate` job receives the schema-owner
  credentials. The API and the worker start only after it succeeds.
- **Container hardening.** Application containers run as non-root, with a
  read-only file system, no Linux capabilities and no privilege
  escalation.
- **Build token.** The private SaaS-OS dependency is fetched with a
  BuildKit secret. The token is never a build argument, an image layer or
  runtime configuration.
- **Locked dependencies.** Images install exactly the committed lock files,
  so the SaaS-OS commit pin cannot drift at build time.
- **No silent defaults.** Every required setting must be supplied; compose
  refuses to start without it.
- **Access logs** omit request URIs, referrers, redirect targets, cookies
  and authorization headers, because link tokens travel in URL paths. The
  confirmation and unsubscribe pages send no referrer.
- **Operational commands** run inside the running worker
  (`compose exec`). `compose run` would re-run the migration job.

## Open decision D14: encryption between containers

The bundled `postgres` and `redis` services speak plaintext on the private
Docker network. With SaaS-OS's production default (TLS required) the stack
therefore **refuses to start**. This is deliberate: the files do not opt
out on the operator's behalf. The options are:

1. **Managed PostgreSQL and Redis with TLS** (recommended for production).
   Set `DATABASE_URL`, `MIGRATIONS_DATABASE_URL` and `REDIS_URL`
   (`rediss://`) in `production.env` and start without the `bundled`
   profile. This also gives managed backups and failover.
2. **TLS on the bundled services** with an operator-run certificate
   authority. Not built: it needs certificate issuance, rotation and
   trust-store configuration.
3. **Explicit opt-out** (`DB_REQUIRE_TLS=false`, `REDIS_REQUIRE_TLS=false`)
   for a single host where all traffic stays on one machine's private
   bridge network. This is the simplest option and acceptable only while
   everything runs on one host.

## Alternatives considered

- **Copying SaaS-OS's deployment files:** rejected (D9; platform boundary).
- **Kubernetes:** rejected for now as unnecessary for a first single-host
  installation. The images and the role and migration model carry over
  unchanged.
- **Running migrations inside the API at startup:** rejected, because it
  would give the application process the schema-owner credentials.

## Consequences

- A single host is a single point of failure. Availability depends on
  backups and a tested restore (`docs/operations/backup-restore.md`).
- Scaling out (several API or worker replicas, a separate database host)
  requires option 1 or 2 above and a load balancer.
- The Redis append-only file reduces, but does not eliminate, the loss of
  queued jobs on a crash. Email and automation are designed to tolerate
  this (at-most-once delivery, outbox recovery commands).

## Security implications

- **Secrets** live in `deploy/production.env` on the host (file mode 600,
  never committed). A secret manager is a later improvement.
- **The PostgreSQL superuser** is used once, to create the two roles.
  Neither role can create roles or databases.
- **Images are pinned by tag, not by digest.** A rebuilt upstream tag would
  change the image. Pinning digests, with an update process, is open work.
- **No resource limits** are set: sizing is unknown until load is
  measured.
- **Encryption at rest** depends on the host's disk encryption. It is not
  configured here.

## What has and has not been verified

See `docs/audits/phase-14-groundwork.md`.
