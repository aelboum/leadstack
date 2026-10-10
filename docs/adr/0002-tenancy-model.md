# ADR-0002: Tenancy model and the deferred agency-subtree authorization issue

Status: Accepted for planning (the model). The agency-subtree authorization
issue is open; see "Deferred issue".

## Context

LeadStack serves agencies that manage client workspaces, and also direct
customers with no agency. SaaS-OS (pinned commit, ADR-0001) provides:

- `core.tenancy`: tenants with an optional `parent_id`, a closure table for
  ancestry, depth limits and cycle protection, tenant lifecycle and purge;
- `core.identity`: users, per-tenant memberships (a user may belong to many
  tenants), invitations;
- `core.rbac.can()`: roles scoped to a tenant (`SELF`) or a tenant and its
  descendants (`SUBTREE`), delegation, explicit deny, support access;
- PostgreSQL row-level security keyed on one tenant per transaction
  (`infra.db.tenant_session_scope`, `infra.db.tenant_rls_statements`).

## Decision

- An agency is a root tenant. Each client workspace is a child tenant of
  its agency. A direct customer is a root tenant with no children.
- LeadStack does not build a second tenant, membership or authorization
  system. It uses the SaaS-OS ones.
- Every LeadStack tenant-owned table has a `tenant_id` foreign key to
  `core.tenants.id`, row-level security applied through
  `tenant_rls_statements()`, and is queried only inside
  `tenant_session_scope()` with a verified tenant (the request context the
  SaaS-OS ingress chain produced, or the job's validated tenant), plus an
  explicit ownership check.
- A CRM record belongs to exactly one workspace tenant. Agency-wide views
  are computed per client tenant, never by cross-tenant SQL.

## Deferred issue: agency staff access to client workspaces

SaaS-OS's HTTP tenant resolution (`api.dependencies.get_tenant_context`)
requires a direct membership in the tenant named in the URL. An agency user
whose only access to a client workspace is a `SUBTREE` role on the parent
agency therefore receives 404 over HTTP, although `core.rbac.can()` would
allow the action. No SaaS-OS test covers SUBTREE access over HTTP.

Options:

- (a) Propose a SaaS-OS change so HTTP tenant resolution honours SUBTREE
  roles. Recommended long term; must go through SaaS-OS's own process.
- (b) Give agency users explicit, direct memberships in each client
  workspace they work in. Works today with no platform change. Recommended
  for the first vertical slice.
- (c) A LeadStack-owned tenant-resolution dependency. Not recommended: it
  would duplicate a security-critical part of the platform's ingress chain.

Until a decision is recorded, LeadStack uses (b) and does not implement (c).

## Consequences

- With (b), adding a client workspace requires granting memberships to the
  agency staff who need it; removing staff access requires revoking them.
  This is explicit and auditable, but not automatic.
- RLS returns zero rows when no tenant is set. A missing scope hides bugs
  rather than raising, so LeadStack needs a static check equivalent to
  SaaS-OS's ADR-0022 test (not shipped in the package) before the first
  domain table lands.

## Security implications

Tenant isolation rests on the SaaS-OS ingress chain, `can()`, and RLS, with
LeadStack's own ownership checks on top. Cross-tenant read/write, missing
tenant context and privilege-escalation tests are mandatory for every
domain phase.

## Migration implications

Choosing (a) later needs no LeadStack data migration. Memberships granted
under (b) could be revoked once SUBTREE access works over HTTP.
