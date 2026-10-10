# Phase 2 audit report: workspaces and access

Date: 2026-10-09. Phase 1 was re-verified independently before this phase
began (all gates green). Phase 1 follow-up closed: GitHub Actions pinned by
commit SHA.

## 1. Verdict

**PASS WITH LIMITATIONS.**

- Every acceptance criterion is evidenced by tests run against disposable
  PostgreSQL 16 and Redis 7.
- An independent review found 1 high, 4 medium and 6 low issues, and no
  critical ones. All were fixed or explicitly documented.
- Limitations are listed in §9. The main ones:
  - invitation tokens are not bound to an email address (a platform
    constraint);
  - CI has not yet run on GitHub.

## 2. Scope completed

- Agency, client and direct-customer workspaces on SaaS-OS tenants:
  - provisioning: PENDING, then ACTIVE, with compensation to DELETED on
    failure;
  - a profile with explicit locale, IANA time zone, ISO 4217 currency and
    country.
- Agency client management:
  - create clients (with a direct membership for the creator, per ADR-0002
    option b);
  - list clients;
  - suspend and reactivate clients.
- Roles `owner` ⊃ `admin` ⊃ `member`, reconciled idempotently on every
  workspace.
- Members:
  - list members;
  - change a member's role;
  - suspend, reactivate and revoke members;
  - protect owners: only owners act on owners, no self-management, and a
    workspace always keeps at least one owner.
  - These changes are serialized per workspace with an advisory lock.
- Invitations:
  - role-carrying invitations; the inviter must hold every permission of
    the role;
  - the role is granted under the inviter's authority, re-checked at
    acceptance;
  - single use, tenant-bound, 72-hour expiry for owner/admin invitations;
  - the token is shown once with `no-store`;
  - cancellation;
  - invitee emails visible only to people who may invite.
- "My workspaces" through a re-validated, user-keyed index (ADR-0004).
  Dead hints are pruned.
- Audit trail API with keyset pagination that is safe for equal
  timestamps. Product events are audited without personal data.
- A tenant purge participant that erases every LeadStack row (idempotent).
- Per-user rate limits on workspace creation and invitation acceptance.
  They fail closed.

Excluded: email delivery of invitations (comes with the communications
phase), workspace deletion and purge routes, and agency subtree access.

## 3. Architecture

- All tenancy, identity, RBAC and audit functions are SaaS-OS's own. No
  parallel tenant, auth or role system exists.
- Product data lives in the `leadstack` schema under forced RLS.
- The domain service re-checks `can()` independently of the HTTP ingress
  chain.
- ADR-0004 records the four platform gaps worked around and the upstream
  proposals that would retire those workarounds.

## 4. Files and migrations

- **New code:**
  - `src/leadstack/domains/regional.py`
  - `domains/workspaces/{models,permissions,errors,service,purge}.py`
  - `routes/{workspaces,errors,pagination,ratelimit}.py`
- **New migration:** `0002_workspace_tables`, which creates `workspaces`,
  `invitation_grants` and `user_workspaces`.
  - Before any release, its foreign key from `invitation_grants` into
    `core.invitations` was removed (finding H1). This migration had only
    ever been applied to disposable test databases.
- **New documentation:** ADR-0004 and `docs/architecture/api-conventions.md`.

## 5. Tests and commands run

- Full local gate: ruff, ruff format, pyright, lint-imports, `uv lock
  --check`, unit tests, integration tests and detect-secrets. All green
  after remediation (see the Phase 3 report for the combined final run).
- Phase 2 integration tests:
  - `test_workspaces.py`: 25 tests;
  - `test_workspaces_hardening.py`: 12 tests;
  - `test_schema_security.py`: 2 tests.
- Unit tests:
  - `test_regional_and_permissions.py`;
  - `test_audit_cursor.py` (equal-timestamp walks);
  - `test_tenant_scope_discipline.py` (static check that sessions are
    tenant-scoped).
- Mutation checks:
  - These mutations were applied, run, and restored by a helper that
    always restores the file. Each was **killed**:
    - removing the owner guard;
    - removing the client-hierarchy check;
    - removing the invitation role pre-check;
    - removing RLS on `workspaces`;
    - removing the membership advisory lock (3 of 3 runs).
  - The first owner-guard mutation **survived**. That exposed a test gap,
    which was fixed with a two-owner scenario.

## 6. Security and isolation audit

Independent review findings and their resolution:

| ID | Finding | Resolution |
|---|---|---|
| H1 | FK into `core.invitations` would block SaaS-OS tenant purge; no purge participant | FK removed; `LeadStackPurgeParticipant` registered by the app and the worker; purge test |
| M1 | Invitation not bound to the invited email | Platform users carry no email. Owner/admin invitations expire in 72 h; bearer risk documented in ADR-0004 |
| M2 | Owner check-then-act race | Per-workspace advisory-lock mutex; concurrency test; mutation proves it is needed |
| M3 | Cancelling an accepted invitation returned 500; some platform errors unmapped | Mapped to 409/403/404; tests |
| M4 | Accept partial failure and ambiguous invitation resolution | Acceptance serialized; invitation resolved as the newly accepted one; roleless member is harmless and fixable by an admin (documented) |
| L1 | Agency staff keep client access after leaving the agency | Documented consequence of ADR-0002 (b); offboarding flow is future work |
| L2 | Members could read invitee emails | Requires `leadstack.members:invite` |
| L3 | Audit cursor and equal timestamps | Unit-tested tie walk. Over 100 identical timestamps remains a documented edge |
| L4 | Invitation without its grant on partial failure | Compensating revoke |
| L5 | Per-member queries; index cap ordering | Newest-first index, dead hints pruned; per-member cost documented |
| L6 | Delegated ownership honoured | Ownership now requires the owner role on the actor's own active membership |

Test gaps the review pointed out were added:
- inviter loses authority;
- suspended client;
- revoked member re-joining;
- existing member re-accepting;
- cross-tenant identifiers;
- tokens absent from logs and responses;
- purge.

## 7. SaaS-OS compatibility

- Pin unchanged: `b126f1a`.
- New interfaces used:
  - `core.tenancy.purge_participants`, a documented consumer mechanism but
    not in `__all__`, isolated in `purge.py`;
  - `infra.db.acquire_tenant_advisory_lock`.

## 8. Outstanding defects

None known within scope.

## 9. Limitations and deferred items

- Invitation tokens are bearer credentials (ADR-0004).
- No email delivery of invitations yet.
- No route to delete or purge workspaces. Purge is an operator action and
  the participant is registered.
- Agency subtree access is not available (ADR-0002). Offboarding must
  revoke client memberships one by one.
- Member listing makes one query per member; acceptable at small-team
  scale, but needs revisiting for large workspaces.

## 10. Approval-dependent decisions

None new. D4 remains open (option b in use).

## 11. Repository state

Untracked files only. Nothing staged, committed or pushed. SaaS-OS
untouched.
