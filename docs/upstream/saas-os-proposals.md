# Proposed SaaS-OS changes (from LeadStack)

LeadStack does not modify SaaS-OS. This file collects the evidence for
platform changes that should go through SaaS-OS's own process. Each entry
records the gap, the evidence at the pinned commit (`b126f1a`), LeadStack's
current workaround, and the proposed change. Priority reflects impact on
LeadStack.

## P1 (high): `can()` holds one connection while checking out more; ingress blocks the event loop

**Evidence.** Pool checkouts were instrumented per thread during a single
contact creation. `core.rbac.authorization.can()` holds a session while
`get_user`, `_tenant_denies_permission`, `get_ancestor_ids`,
`get_membership` and `_tenant_grants_permission` each open another one
(nesting depth 2).

Effect: with many concurrent callers, every caller holds one connection
and waits for a second, the pool (5 + 10) is exhausted, and requests fail
with `QueuePool limit ... timed out` after 30 s. 40 concurrent contact
creations produced 11 failures.

Separately, `api.dependencies.require_permission()` runs this synchronous,
blocking `can()` inside an `async` dependency, so it blocks the event loop
for every request.

**LeadStack workaround.**
- LeadStack's own `can()` calls go through `_bounded_can()`, which allows 4
  concurrent calls per process (`LEADSTACK_MAX_CONCURRENT_AUTHZ`).
- Membership operations are bounded to 2.
- `tests/unit/test_tenant_scope_discipline.py` forbids LeadStack code from
  nesting checkouts.
- `test_concurrent_contact_writes_do_not_exhaust_the_pool` covers it; a
  mutation check confirms the bound is necessary.

**Proposal.**
- Evaluate `can()` within a single session (pass it through), or release
  the outer connection before the helper calls.
- Run blocking ingress steps in a worker thread (`run_in_threadpool`), or
  make the ingress dependencies synchronous.

## P2 (high): tenant resolution ignores SUBTREE roles over HTTP

ADR-0002. `api.dependencies.get_tenant_context` requires a direct
membership. **Proposal:** let a SUBTREE role on an ancestor satisfy tenant
resolution, with tests.

## P3 (medium): no delayed or scheduled jobs; no consumer job registration in the stock worker

- `infra.jobs.enqueue_job` cannot defer a job.
- There is no periodic trigger.
- `api.worker.registered_job_functions()` is a fixed list.

This blocks workflow delays, reminders and appointments (roadmap D6).

**Proposal:**
- `enqueue_job(..., defer_until=/defer_by=)`, passed through to arq;
- a cron facility (arq `cron_jobs`);
- a public `build_worker`-based entrypoint that accepts consumer jobs.

## P4 (medium): no "tenants of a user" lookup

ADR-0004. **Proposal:** `core.identity.list_memberships_for_user(user_id)`,
running under a reviewed, narrowly scoped policy.

## P5 (medium): invitations carry no role and are not bound to an identity

ADR-0004. **Proposal:**
- an optional role on `create_invitation`, granted atomically on acceptance;
- optional binding to a verified email claim.

## P6 (medium): no Origin or CSRF check for cookie sessions

The session cookie is `SameSite=Lax`, with no Origin check.

LeadStack adds `SameOriginMiddleware` for `/v1/*`.

**Proposal:** a platform-level same-origin check for state-changing
requests authenticated by cookie.

## P7 (low): public-surface gaps

- `core.tenancy.purge_participants` is not in `core.tenancy.__all__`, yet it
  is the documented product mechanism.
- `api.dependencies`, `api.context` and `api.errors` declare no `__all__`.
- `api.worker.registered_job_functions()` and `check_health()` are
  unclassified (ADR-0017).

## P8 (low): import-time configuration

Importing `api.platform` (via `core.identity`) calls `register_job()`. That
resolves `REDIS_URL` and requires `ENVIRONMENT` at import time.

**Proposal:** register jobs lazily.

## P9 (low): readiness checks cannot be extended; no HTTP API-key authentication; bare error bodies; no pagination convention

Documented in `docs/architecture/saas-os-integration.md`.

## P10 (medium): `infra.db` does not export `BigInteger`, `Date` or SQL functions

Domain code may not import SQLAlchemy. Mapping a BIGINT column as `Integer`
breaks with SQLAlchemy 2.1: binds render as `::INTEGER`, so values above
2^31−1 fail with a 500 (found in the LeadStack Phase 5 review).

**LeadStack workaround.** `leadstack/db_types.py` provides `BigInteger`,
`Date` and `lower`. It is the single import edge allowed by LeadStack's
import contracts.

**Proposal.** Export these from `infra.db`.

## P11 (medium): `EmailMessage` cannot carry custom headers

- **Observed:** `core.email.EmailMessage` (pinned commit) has sender,
  recipients, subject and text/HTML bodies only. There is no way to set
  `List-Unsubscribe` / `List-Unsubscribe-Post` (RFC 8058), `Reply-To` or a
  message reference header.
- **Impact on LeadStack:** consent emails and one-to-one emails carry an
  unsubscribe link in the body only. Large mailbox providers expect the
  one-click unsubscribe headers for bulk senders. Per-workspace reply
  addresses are not possible.
- **Proposal:** an optional, validated `headers` mapping (an allowlist
  such as `List-Unsubscribe`, `List-Unsubscribe-Post` and `Reply-To`, with
  CR/LF rejected) applied by `SmtpEmailProvider`.
- **LeadStack workaround:** body links only; documented in ADR-0007.

