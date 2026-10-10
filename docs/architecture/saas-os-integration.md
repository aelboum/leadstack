# SaaS-OS integration

How LeadStack uses SaaS-OS at the pinned commit
`b126f1ad0c9cf4173ada15e20b14850d9e600c14` (ADR-0001), and the known
limitations of that version. Re-verify this page on every pin change.

## Interfaces LeadStack uses

| Interface | Used by | Notes |
|---|---|---|
| `api.platform.build_platform_app(title, version)` | `leadstack.app` | Lifespan (logging, tracing, DB-role guard), correlation middleware, `/healthz`, `/readyz`, `/auth/*`. |
| `api.worker.registered_job_functions()` | `leadstack.worker` | Platform job list. Lives in SaaS-OS's own worker entrypoint module, which ADR-0017 does not classify; a unit test detects interface changes. |
| `api.worker.check_health()` | `leadstack.worker --check` | Redis health-sentinel probe. Same caveat as above. |
| `infra.jobs.build_worker`, `register_job`, `enqueue_job`, `get_jobs_config`, `TenantJobPayload` | `leadstack.worker`, `leadstack.jobs` | Exported in `infra.jobs.__all__`. |
| `infra.db.run_core_migrations`, `get_migrations_database_config`, `get_engine`, `validate_application_role` | `leadstack.migrate`, migrations env, worker | Exported in `infra.db.__all__`. |
| `infra.db.migration_runner.current_core_revision`, `core_head_revision` | `leadstack.migrate`, migrations env | Public functions of the module behind `saas-os-migrate`; not re-exported from `infra.db`. |
| `infra.observability.configure_logging`, `configure_tracing` | `leadstack.worker`, `leadstack.admin` | Exported. |
| `core.tenancy`, `core.identity`, `core.rbac`, `core.audit_log` (exported functions) | `leadstack.domains.workspaces`, `leadstack.domains.crm` | Tenants, memberships, invitations, roles, `can()`, audit records. |
| `api.dependencies.require_permission`, `get_current_actor`; `api.context.RequestContext`; `api.errors` | `leadstack.routes` | No `__all__` in these modules; public by documented use (ADR-0017, reference consumer). |
| `infra.db.tenant_session_scope`, `session_scope`, `select`/`delete`, ORM primitives, `tenant_rls_statements`, `acquire_tenant_advisory_lock` | domains, migrations | Exported. |
| `infra.ratelimit.enforce_rate_limit`, `get_ratelimit_config`, `RateLimitExceededError` | `leadstack.routes.ratelimit`, `leadstack.jobs` (per-address confirmation limit) | Exported. |
| `core.email.send_email`, `EmailMessage`, `get_email_config`, error types, `FakeEmailProvider` (tests only) | `leadstack.integrations.email`, `leadstack.domains.messaging` | Exported. Transactional email only: no custom headers (proposal P11), no templates, no unsubscribe management. |
| `infra.secrets.get_secrets_provider` | form tokens, link signing | Exported. |
| `core.tenancy.get_tenant`, `TenantStatus`, `is_accessible_to_principals`, `is_closed` | forms, automation, messaging, scheduling, admin | Exported. Used to refuse work for suspended or closed workspaces. |
| `infra.db.acquire_tenant_advisory_lock` | forms, CRM settings, automation, messaging (consent lock), scheduling (host and quota locks) | Exported. Transaction-scoped. |
| `core.tenancy.purge_participants.default_registry`, `DuplicateTenantPurgeParticipantError` | `leadstack.domains.workspaces.purge` | Documented consumer mechanism, **not** in `core.tenancy.__all__`; isolated in one module. |

LeadStack never imports `api.main` or `api.server` (enforced by
import-linter).

## Runtime model

- One PostgreSQL database, two Alembic histories (SaaS-OS ADR-0016):
  SaaS-OS's (`alembic_version_saas_os`) then LeadStack's (`alembic_version`).
  `leadstack-migrate upgrade` applies both in order. LeadStack's environment
  refuses to run unless SaaS-OS's history is at the installed package's head.
- Two roles: the schema owner (`MIGRATIONS_DATABASE_URL`) and the restricted
  application role (`DATABASE_URL`, NOSUPERUSER NOBYPASSRLS). The app and
  worker refuse to start under an unsafe role.
- One API process (`uvicorn --factory leadstack.app:create_app`) and one
  worker process (`leadstack-worker`) carrying platform and product jobs.
- Backend paths: `/auth/*` (platform) and `/v1/*` (product). This matches
  SaaS-OS's single-origin edge (ADR-0024), which routes only those prefixes
  to the backend.

## Known limitations of the pinned SaaS-OS version

Found in the bootstrap audit and Phase 1. Each needs a decision or an
upstream proposal before the phase that depends on it.

1. **Agency → client access over HTTP.** Tenant resolution requires direct
   membership; SUBTREE roles are not honoured over HTTP. See ADR-0002.
2. **No scheduled or delayed jobs.** `enqueue_job` has no defer option and
   there is no cron facility. Blocks workflow delays, reminders and
   appointment notifications (roadmap D6).
3. **No consumer job extension in `api.worker`.** Handled by LeadStack's own
   worker entrypoint, which depends on two unclassified functions in
   `api.worker` (table above).
4. **Import-time configuration.** Importing `api.platform` (and
   `core.identity`) registers platform jobs, which reads `ENVIRONMENT` and
   `REDIS_URL` at import time. Importing the app without configuration
   fails. LeadStack defers its own job registration to worker build time.
5. **No AI model runtime.** The AI Control Plane governs tools (registry,
   permissions, autonomy tiers, approvals, audit) but has no model calls,
   agent loop or conversation memory. Approvals do not check the approver's
   permission. Roadmap D5.
6. **API gaps.** No API-key authentication over HTTP, error responses are
   bare `{"detail": ...}` with no error codes, no pagination convention, and
   no HTTP routes for tenants, members or invitations.
7. **Billing.** Plans carry no currency or amount; Stripe is the only
   provider; no Stripe webhook route. Payment options for Morocco are
   unverified (roadmap D7).
8. **Notifications.** In-app and email (SMTP) only; no SMS, WhatsApp or push;
   no templates.
9. **Login branding.** Login is a redirect to ZITADEL; product-owned
   embedded login (SaaS-OS ADR-0026) is a draft, not accepted (roadmap D8).
10. **Operations.** Readiness checks cannot be extended with product checks.
    Observability exports to the console only. Redis persistence is not
    configured in SaaS-OS's reference setup, so queued jobs can be lost on a
    Redis restart. No production edge or compose setup is provided for
    consumers (roadmap D9).
11. **Identity and tenancy gaps (ADR-0004).** No "tenants of a user"
    lookup; invitations carry no role and are not bound to an email (users
    have no email); membership status changes are unauthorized by design
    (the caller authorizes). LeadStack works around each one; upstream
    proposals are listed in ADR-0004.
12. **Packaging details.** The application-role creation script is not
    shipped; deployments create the role themselves. The tenant purge-hook
    module is not in `core.tenancy.__all__`. SaaS-OS's static check for
    queries without tenant context (its ADR-0022) is not shipped.
13. **Defaults to note.** The platform builder leaves FastAPI's `/docs`,
    `/redoc` and `/openapi.json` enabled. The single-origin edge does not
    route those paths to the backend, but a deployment that exposes the
    backend directly would serve them.
