# Deployment guide (single host)

Status: draft for the topology proposed in ADR-0009. It has **not** been
used for a real deployment. Read "Verified and unverified" at the end
before relying on it.

## Prerequisites

- A Linux host with Docker Engine and the Compose plugin (BuildKit
  enabled).
- A DNS record for the public host name pointing at the host; ports 80 and
  443 reachable.
- A read-only access token for the private SaaS-OS repository, used for
  building only.
- An OIDC application in the identity provider (hosted ZITADEL), with the
  redirect URI required by SaaS-OS's `/auth/callback` on the public host
  name.
- An SMTP relay and a sender domain with SPF, DKIM and DMARC records, if
  email is used.
- A decision on encryption between containers (D14, below).

## 1. Configure

```sh
cp deploy/production.env.example deploy/production.env
chmod 600 deploy/production.env
```

Fill in every value. Generate each password and key independently:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Decision D14.** With the defaults, SaaS-OS requires TLS to PostgreSQL and
Redis, and the bundled plaintext services are refused. The API, worker and
migration job exit with a clear configuration error. Choose one:

- **External TLS data stores** (recommended for production): fill in block
  B of `deploy/production.env` (`DATABASE_URL`, `MIGRATIONS_DATABASE_URL`,
  `REDIS_URL` with `rediss://`) and start without `--profile bundled`.
- **Bundled containers:** fill in block A, uncomment `DB_REQUIRE_TLS=false`
  and `REDIS_REQUIRE_TLS=false`, and start with `--profile bundled`. This
  accepts unencrypted traffic between containers on this one host's private
  network. The bundled Redis still requires a password, and neither data
  store is reachable from the edge or the frontend.

## 2. Build

```sh
export SAAS_OS_READ_TOKEN=...   # this shell only; never written to a file
docker compose -f deploy/compose.yaml --env-file deploy/production.env build
unset SAAS_OS_READ_TOKEN
```

The token is used as a BuildKit secret and does not end up in the images.
Images are tagged with `LEADSTACK_VERSION`.

## 3. Start

```sh
docker compose -f deploy/compose.yaml --env-file deploy/production.env --profile bundled up -d
docker compose -f deploy/compose.yaml --env-file deploy/production.env --profile bundled ps
```

Omit `--profile bundled` when using external data stores (block B of
`production.env`).

Order of events:

1. PostgreSQL creates the two roles (first start only).
2. `migrate` applies SaaS-OS's migrations, then LeadStack's, and exits.
3. `api` and `worker` start.
4. The edge obtains a certificate and serves traffic.

Check:

```sh
docker compose -f deploy/compose.yaml --env-file deploy/production.env logs migrate
docker compose -f deploy/compose.yaml --env-file deploy/production.env exec api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').status)"
```

## 4. After the first start

- Sign in through the browser and create the first workspace.
- After any upgrade that changes roles or permissions, reconcile existing
  workspaces:

  ```sh
  docker compose -f deploy/compose.yaml --env-file deploy/production.env exec -T worker leadstack-admin sync-roles
  ```

## Upgrading

1. Back up the database first (`docs/operations/backup-restore.md`).
2. Check out the new version and set `LEADSTACK_VERSION`.
3. Build, then `up -d`. The migration job runs before the new API and
   worker start.

Migrations are written to be backward compatible within one release step.
A rollback of the application alone is possible only when the release
notes say the migration is compatible. Otherwise restore the backup.

A change of the SaaS-OS commit pin is a separate, reviewed dependency
update (CLAUDE.md §2.5). It is never done as part of a routine deployment.

## Recurring jobs (until scheduling is decided, D6)

LeadStack has no scheduler yet. Run these from the host's scheduler, for
example cron:

| Command | Suggested frequency | Purpose |
|---|---|---|
| `leadstack-admin process-events` | every 5 minutes | Recover automation events whose dispatch was missed |
| `leadstack-admin send-queued --minutes 15` | every 15 minutes | Re-enqueue emails left queued |
| `leadstack-admin prune-automation --days 90` | daily | Delete old processed events and runs |

Each is run inside the running worker container:

```sh
docker compose -f deploy/compose.yaml --env-file deploy/production.env exec -T worker <command>
```

Do not use `compose run api ...` for these: `run` starts dependencies, which
re-runs the migration job with the schema-owner credentials on every call
(and, during an upgrade, could apply new migrations under the old API).

## Verified and unverified

Verified locally on 2026-10-10:

- `docker compose config` accepts the file, with and without the `bundled`
  profile. `caddy validate` accepts the Caddyfile.
- The bundled PostgreSQL and Redis services start from the compose file and
  become healthy. Redis refuses commands without the password.
- The role script creates both roles with no extra privileges on PostgreSQL
  16 and refuses an empty password. The full migration chain (SaaS-OS, then
  LeadStack to `0009_scheduling`) applies with the owner role, and SaaS-OS's
  role guard accepts the application role.
- The frontend image builds, starts as a non-root user with a read-only
  file system, passes its health check, and serves pages with the security
  headers. The confirmation and unsubscribe pages send
  `Referrer-Policy: no-referrer`.

Not verified:

- **The backend image has never been built.** It needs the SaaS-OS token,
  which was not available to the build.
- The full stack has never been started together.
- Certificate issuance, the login flow through the edge, and email
  delivery.
- Behaviour under load, and resource sizing.
- Backup and restore of a real installation.
