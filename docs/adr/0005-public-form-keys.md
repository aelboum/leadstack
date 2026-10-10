# ADR-0005: Public form keys and unauthenticated submissions

Status: Accepted (Phase 6, 2026-10-09)

## Context

Lead-capture forms are submitted by anonymous visitors, often from pages on
other origins. A submission has no session and therefore no tenant context.
Form definitions and submissions are tenant data under forced RLS, so the
tenant must be found before any tenant-scoped read is possible.

## Decision

- **Public key.** Each form gets a random public key from
  `secrets.token_urlsafe(24)` (about 192 bits). The key is an unguessable
  identifier, not a credential that grants access to data. It only allows
  posting to that one form.
- **Key index.** `leadstack.form_keys (public_key, tenant_id, form_id)`
  resolves a key to its form. It holds identifiers only and has no tenant
  RLS, the same reviewed pattern as `user_workspaces` (ADR-0004). It is
  queried only by exact key. Unknown keys, disabled forms and workspaces
  that no longer admit principals all return the same 404.
- **Spam controls without a third-party CAPTCHA:**
  - an HMAC-SHA256 form token, keyed by `LEADSTACK_FORM_TOKEN_SECRET`
    (read from the SaaS-OS secrets provider), bound to the form key, with a
    minimum fill time (2 s) and a maximum age (2 h);
  - a honeypot field: when filled, the request answers success but stores
    nothing;
  - fixed-window rate limits per client (hashed address) and per form,
    which fail closed when Redis is unavailable;
  - strict field validation: only defined fields are accepted, values are
    length-bounded, and email and phone are normalized.
- **Consent.** Every submission requires explicit consent. The consent text
  shown at the time is stored with the submission (snapshot) along with a
  timestamp.
- **Minimization.** No IP address or user agent is stored. The client
  address exists only as a hash inside the rate-limit key in Redis.
- **CRM effect.** A submission creates a contact or links an existing one
  with the same email. It never overwrites existing contact data, so a
  public form cannot be used to change CRM records.
- **CORS.** `/v1/public/*` allows any origin without credentials. Every
  other path keeps the default same-origin policy and the Origin guard.

## Alternatives considered

- **Third-party CAPTCHA:** it adds an external provider, cost, and a
  privacy assessment. It can be layered on later behind an adapter.
- **Including the tenant ID in the public URL:** it reveals tenant
  identifiers and still needs a lookup.

## Consequences

- A valid token can be reused within its age window. Per-client and
  per-form rate limits bound the abuse. Single-use tokens would need
  server-side state; this is revisited if abuse is observed.
- Behind a proxy, the server must be configured to trust forwarded headers
  from the edge only (uvicorn `--proxy-headers --forwarded-allow-ips`).
  Otherwise every visitor shares the proxy's rate limit.
- Published pages that embed forms must live on a separate registrable
  domain (see the Phase 3 CSRF note).

## Security implications

The public surface is limited to reading a form definition and posting a
validated submission. A tenant cannot be discovered from a key without
knowing the key. Spam can exhaust a form's hourly quota, which affects only
that form.
