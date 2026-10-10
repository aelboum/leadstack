# Backup and restore

Status: procedure for the single-host topology (ADR-0009). The logical
dump and restore commands are standard PostgreSQL. This procedure has
**not** been exercised against a real LeadStack installation: run a restore
test before going live and on a schedule afterwards.

## What must be backed up

| Data | Where | Why |
|---|---|---|
| PostgreSQL database | `postgres-data` volume | All business data, both schemas' migration state, audit log |
| `deploy/production.env` | host file | Passwords and signing keys |
| Caddy data | `caddy-data` volume | TLS certificates (re-issuable; backing up avoids rate limits) |

Redis holds only queued jobs and rate-limit counters. It is not backed up:
after a loss, run the recovery commands at the end of this page.

**Signing keys matter.** If `LEADSTACK_LINK_SIGNING_SECRET` is lost or
changed, every unsubscribe and confirmation link already sent stops
working. If `LEADSTACK_FORM_TOKEN_SECRET` changes, open form and booking
pages must be reloaded.

## Backup

A logical dump, taken with the superuser so that every schema, ownership, grant and
row-level security policy is included:

```sh
docker compose -f deploy/compose.yaml --env-file deploy/production.env exec -T postgres \
  pg_dump -U postgres -d leadstack --format=custom \
  > leadstack-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Roles and passwords are not in a database dump. They are recreated from
`deploy/production.env` by the role script on a fresh data directory.

Requirements for a real installation (to decide, not provided here):

- encrypt backups and store them off the host;
- define retention, and a deletion process consistent with contact erasure
  (an erased contact remains in older backups until they expire);
- monitor that backups run and are restorable.

## Restore (to a fresh host or a fresh data directory)

1. Restore `deploy/production.env` (same passwords and keys).
2. Start only the database, so that the role script creates the two roles:

   ```sh
   docker compose -f deploy/compose.yaml --env-file deploy/production.env up -d postgres
   ```

3. Restore the dump as the superuser:

   ```sh
   docker compose -f deploy/compose.yaml --env-file deploy/production.env exec -T postgres \
     pg_restore -U postgres -d leadstack --clean --if-exists < leadstack-YYYYMMDDTHHMMSSZ.dump
   ```

4. Start the rest. The migration job brings the schema to the version of
   the images (a no-op when the dump is from the same version):

   ```sh
   docker compose -f deploy/compose.yaml --env-file deploy/production.env up -d
   ```

5. Verify:
   - `leadstack-migrate current` shows both histories at head;
   - the API reports ready;
   - a sign-in works and a workspace's contacts are visible;
   - a second workspace cannot see them (row-level security is active).

6. Recover queued work lost with Redis:

   ```sh
   docker compose -f deploy/compose.yaml --env-file deploy/production.env exec -T worker leadstack-admin process-events
   docker compose -f deploy/compose.yaml --env-file deploy/production.env exec -T worker leadstack-admin send-queued --minutes 1
   ```

   Emails that were in the `sending` state at the time of the loss are not
   re-sent: delivery is at most once, and they are marked failed with
   `outcome_unknown`.

## Restore test

A restore test is passed only when step 5 succeeds on a separate machine or
a separate data directory, never against the production database.
