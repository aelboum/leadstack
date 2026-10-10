# API conventions

Applies to every LeadStack route. SaaS-OS's own `/auth/*`, `/healthz` and
`/readyz` keep their platform behavior.

## Paths and versioning

- All product routes live under `/v1`. The single-origin edge routes only
  `/auth/*` and `/v1/*` to the backend.
- Tenant-scoped routes are `/v1/workspaces/{tenant_id}/...`. The path
  `tenant_id` is only input to the SaaS-OS ingress chain; handlers act on
  the verified `RequestContext.tenant_id`.

## Authentication and authorization

- SaaS-OS session (bearer header or cookie), resolved by
  `api.dependencies`.
- Tenant routes declare `require_permission(resource, action)`. That
  dependency authenticates, resolves the tenant (direct membership in an
  accessible tenant), applies the per-tenant rate limit, then calls
  `core.rbac.can()`, auditing denials.
- Domain services call `can()` again. A route guard is never the only
  check.
- Routes that act before a tenant exists (workspace creation, invitation
  acceptance) authenticate with `get_current_actor` and apply per-user
  rate limits (`leadstack.routes.ratelimit`).

## Requests

- JSON bodies are Pydantic models with `extra="forbid"`: unknown fields are
  rejected, which prevents mass assignment.
- Strings are length-bounded. Enumerations use `Literal` types. Regional
  values are validated against explicit allowlists (`domains/regional.py`).

## Errors

The body is always `{"detail": ...}`.

- LeadStack errors: `{"detail": {"code": "<stable_code>", "message": "<text>"}}`.
- SaaS-OS ingress errors: `{"detail": "<text>"}` (401, 403, 404, 429, 503).
- Validation errors: FastAPI's `{"detail": [...]}` (422).

| Status | Meaning |
|---|---|
| 400 | A well-formed request that cannot be honored (for example an invalid invitation) |
| 401 | Not authenticated |
| 403 | Authenticated, but lacking permission or blocked by a protection rule |
| 404 | Not found, or not visible to you (never distinguished) |
| 409 | State conflict (for example a status change that is not allowed) |
| 422 | Invalid input |
| 429 | Rate limited |
| 503 | A dependency (for example the rate limiter) is unavailable; fails closed |

Messages never contain identifiers, internal state, exception text or
secrets.

## Pagination

- Query: `limit` (1–100, default 50) and an opaque `cursor`.
- Response: `{"items": [...], "next_cursor": "<opaque>" | null}`.
- Database-backed lists use keyset cursors (for example the audit trail:
  timestamp plus ids already returned at that timestamp). Small in-memory
  lists use offset cursors.
- Malformed cursors return 422 `invalid_cursor`.

## Responses carrying secrets

One-time secrets (invitation tokens) are returned once, with
`Cache-Control: no-store`, and never logged or audited.
