# ADR-0004: Workspace model, roles, invitations and the user-workspace index

Status: Accepted (Phase 2, 2026-10-09), within the boundaries of ADR-0002.

## Context

ADR-0002 maps agencies, clients and direct customers onto SaaS-OS tenants
and requires direct membership for agency staff in client workspaces.
Phase 2 needed the product-side model and found four gaps in the pinned
SaaS-OS version:

1. Tenants carry only a name; there is no place for product settings.
2. SaaS-OS invitations carry no role.
3. There is no "list the tenants of a user" lookup. Memberships live in a
   tenant-scoped table under forced RLS, so the application role cannot
   find a user's tenants without already knowing them.
4. Membership status changes (`suspend`/`reactivate`/`revoke`) perform no
   authorization of their own; the caller must.

## Decision

- **Workspace profile.** `leadstack.workspaces` holds one row per tenant:
  `kind` (agency, client, direct), display name, locale, IANA time zone,
  ISO 4217 currency and optional country. Forced RLS on `tenant_id`.
  Every regional value is explicit per workspace; nothing defaults to a
  country. Clients inherit unspecified settings from their agency.
- **Provisioning.** The tenant is created PENDING. Membership, roles, the
  profile and the index entry are created, then the tenant is set ACTIVE.
  If any step fails, the tenant is moved to DELETED, so a half-built
  workspace is never usable. SaaS-OS calls are separate transactions, so
  this is compensation, not atomicity.
- **Roles.** Each workspace gets `owner` ⊃ `admin` ⊃ `member`
  (`permissions.py`), reconciled idempotently by
  `ensure_workspace_roles()`. The only difference between owner and admin
  is `leadstack.ownership:manage`.
- **Owner protection.** Only owners act on owners or grant ownership. No
  one changes their own membership. A workspace keeps at least one active
  owner. SaaS-OS's `assign_role` independently refuses any role carrying a
  permission the actor lacks.
- **Invitations.** `leadstack.invitation_grants` records the role an
  invitation confers. The inviter must hold every permission of that role
  when inviting. On acceptance, the role is granted *under the inviter's
  authority*, which SaaS-OS re-checks at that moment. If the inviter has
  lost that authority, the membership is created with no role and the
  refusal is audited. The raw token is returned once (`Cache-Control:
  no-store`); only its hash is stored, by SaaS-OS. The token is a bearer
  credential and is not bound to the invited email, because SaaS-OS users
  carry no email. Email delivery arrives with the communications phase;
  until then the inviter shares the link.
- **User-workspace index.** `leadstack.user_workspaces (user_id,
  tenant_id)` records the workspaces a user joined through LeadStack. It
  holds identifiers only and has no tenant RLS policy, because it is keyed
  by user. It is a *hint*: `list_my_workspaces()` re-validates every
  candidate against the live SaaS-OS tenant status, membership status and
  `can()` before returning it. Queries always filter on the verified
  actor id.

## Alternatives considered

- **Cross-tenant membership query** (iterate every tenant, or a privileged
  database role). Rejected: it would bypass RLS or need a BYPASSRLS
  connection.
- **Storing roles on the SaaS-OS invitation.** Not possible without a
  platform change.
- **Upstream proposals** (recommended, not blocking): a SaaS-OS
  `list_tenants_for_user(user_id)` interface, an optional role on
  invitations, and SUBTREE-aware HTTP tenant resolution (ADR-0002). Once
  these exist, the index table and the grant table can be retired.

## Consequences

- An index row can be stale (for example after a revocation). Stale rows
  are filtered out, never trusted.
- A user who joined through some path other than LeadStack's routes would
  not be listed until indexed. All LeadStack paths index.
- Tenant purge is not offered. LeadStack tables reference `core.tenants`,
  so a purge would first need LeadStack purge participants (the SaaS-OS
  hook module is not in its public `__all__`). Deferred.

## Security implications

Tenant data stays behind forced RLS and the SaaS-OS ingress chain. The
index exposes no tenant data, only identifiers, and only for the verified
actor. Owner protection and the inviter re-check prevent privilege
escalation through role changes and invitations; integration tests and
mutation checks cover both.

## Migration implications

Revision `0002_workspace_tables`. Downgrade drops only these three tables
and is tested.
