# Operations runbook

Status: first version for the single-host topology (ADR-0009). The symptoms
and causes below come from the behaviour of the code and from failures
actually seen during development. No production incident has occurred yet.

All commands assume:

```sh
alias dc='docker compose -f deploy/compose.yaml --env-file deploy/production.env'
```

## Health

| Check | Command | Healthy |
|---|---|---|
| Containers | `dc ps` | all `running`/`healthy`; `migrate` exited 0 |
| API readiness (database and Redis reachable) | `dc exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').status)"` | `200` |
| Worker | `dc exec worker leadstack-worker --check` | exit code 0 |
| Schema versions | `dc run --rm --no-deps migrate leadstack-migrate current` | both histories at head |

`/healthz` says the process is alive; `/readyz` says its dependencies are
reachable. Neither is exposed to the internet.

## Logs

Logs are structured JSON on standard output (`dc logs api`, `dc logs worker`).
Every request has a correlation identifier. Application logs contain no
message bodies, tokens or connection strings. Edge access logs omit request
URIs, referrers, redirect targets and cookies, because link tokens travel
in URL paths. Container logs are rotated (5 files of 20 MB each).

The worker logs only the *type* of a start-up error, never its message
(a message could carry a connection string). To see the full configuration
error, check `dc logs migrate` or `dc logs api`, which fail on the same
settings.

There is no log shipping, metrics exporter or alerting yet (see "Gaps").

## Symptoms and causes

### API, worker or migrate exits at start with a configuration error

- **"TLS is required" for PostgreSQL or Redis.** Decision D14 has not been
  made. See `docs/operations/deployment.md`.
- **"Unsafe database role".** `DATABASE_URL` uses a superuser or a role
  with BYPASSRLS. Use the application role. This check must never be
  worked around: it is what keeps workspaces isolated.
- **"set FORWARDED_ALLOW_IPS".** The API was started outside compose
  without the edge address.

### Everyone gets "Too many requests" on public forms or booking

The API sees one client address for all visitors. Check that requests reach
the API only through the edge, and that `FORWARDED_ALLOW_IPS` is the edge's
address.

### 503 "temporarily unavailable"

- **On public forms or booking:** `LEADSTACK_FORM_TOKEN_SECRET` is missing
  or shorter than 32 characters, or Redis is unreachable. Rate limiting
  fails closed.
- **On sending email or on consent links:** `LEADSTACK_LINK_SIGNING_SECRET`
  is missing, or `PUBLIC_APP_ORIGIN` is not an `https://` origin.

### Emails stay "queued"

The enqueue failed (Redis was unavailable) or the worker is down.

1. Check the worker's health.
2. Run `dc exec -T worker leadstack-admin send-queued --minutes 15`.

### Emails are "failed"

| Error code | Meaning | Action |
|---|---|---|
| `email_not_configured` | SMTP settings or `EMAIL_DEFAULT_SENDER` missing | Fix the configuration; the message is not retried automatically |
| `provider_unavailable` | The SMTP relay failed three times | Check the relay |
| `outcome_unknown` | The worker stopped during a send | Check the relay's own log before resending by hand; LeadStack never sends twice |
| `invalid_address` | The relay or validation rejected the address | Correct the contact |

### Emails are "suppressed" (not sent, by design)

`consent_withdrawn`, `address_changed`, `no_consent_record`,
`already_decided`, `address_rate_limited`, `workspace_inactive`. These
protect recipients and are not errors.

### Automations do not run

1. Is the workflow paused? A workflow pauses itself when its creator loses
   permission or when something it refers to (a tag) was deleted.
2. Is the worker healthy?
3. Run `dc exec -T worker leadstack-admin process-events` to process events
   whose dispatch was missed.

### A member lacks a permission that their role should have

New permissions reach existing workspaces only after
`dc exec -T worker leadstack-admin sync-roles`.

### Integration tests or a local stack fail with database connection errors

This was seen in development: Docker was restarted and the containers were
stopped. Start them again; nothing is wrong with the code.

## Routine tasks

See "Recurring jobs" in `docs/operations/deployment.md`.

## Security events

- **A signing key or database password is exposed.** Rotate it in
  `deploy/production.env` and restart. Rotating
  `LEADSTACK_LINK_SIGNING_SECRET` invalidates every unsubscribe link
  already sent; recipients can then unsubscribe only by replying. Decide
  this deliberately.
- **Suspected cross-workspace access.** Stop the API, preserve the logs and
  the database, and review the audit log for the workspaces concerned. Do
  not delete data.
- **The SaaS-OS build token is exposed.** Revoke it in GitHub. It grants
  read access to the platform source only and is not present in images or
  at runtime.

## Gaps (not built)

- Metrics, dashboards and alerts. SaaS-OS provides tracing configuration;
  no exporter or collector is configured.
- Log shipping and retention.
- Automated backups and restore tests.
- A scheduler for the recurring jobs (decision D6).
- A status page and an on-call procedure.
- Load testing and capacity figures.
