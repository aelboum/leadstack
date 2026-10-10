# Phase 3 audit report: CRM contacts and companies, frontend shell, i18n/RTL

Date: 2026-10-09

## 1. Verdict

**PASS WITH LIMITATIONS.**

- Contacts and companies work end to end through the real HTTP stack,
  with tenant isolation, authorization, validation, search and pagination
  tested.
- The frontend shell builds, type-checks, lints and passes its tests in
  French, Arabic (RTL) and English.
- An independent review found 1 high, 4 medium and 11 low issues, and no
  critical ones. All were fixed or documented.
- The high finding traced to a SaaS-OS defect (connection-pool nesting in
  `can()`). It is mitigated in LeadStack and proposed upstream.
- Limitations are listed in §9.

## 2. Scope completed

**Backend**

- Companies and contacts: create, read, update, delete.
- A contact's company must be in the same workspace (composite foreign
  key). Deleting a company unlinks its contacts and leaves them in place.
- The contact owner must be an active member of the workspace.
- Contacts and companies are deleted outright (erasure). The audit trail
  keeps only identifiers.
- Normalization:
  - phones are stored in E.164, interpreted for the workspace's country;
    with no workspace country, an international prefix is required;
  - emails have their domain lower-cased;
  - websites must be http/https.
- Search covers names, email and phone. Phone searches typed in national
  format also match. LIKE wildcards are matched literally.
- Keyset pagination is newest first, and ties on equal timestamps are
  safe.
- Partial updates: an omitted field is left unchanged; an explicit null
  clears it.

**Platform-level protections added**

- `StrictModel`: rejects unknown fields and control characters.
- `SameOriginMiddleware`: rejects cross-origin state-changing requests,
  including from sibling subdomains and `Origin: null`.
- `_bounded_can()`: bounds concurrent `can()` calls (see §6).
- `leadstack-admin sync-roles`: grants permissions added by a release to
  existing workspaces.

**Frontend** (Next.js 15.5.27, React 19.3, TypeScript 5.9, all exact pins)

- Pages: workspace picker, contacts (search, create, load more), companies
  (paginated).
- Hosted SaaS-OS login link.
- i18n:
  - fr/ar/en dictionaries, checked for completeness;
  - locale chosen from a cookie, then Accept-Language, never from IP;
  - `dir` on `<html>`;
  - logical CSS properties only (enforced by a test);
  - `<bdi dir="ltr">` around phones and emails;
  - Latin digits in Arabic date formatting.
- API errors are translated by their stable code.
- Security headers: CSP, frame-ancestors none, nosniff, referrer policy,
  permissions policy, and HSTS in production builds.

## 3. Architecture

- CRM is a domain package (`leadstack.domains.crm`) that uses SaaS-OS only
  through `infra.db` and the workspaces service.
- Routes stay thin.
- The four import-linter contracts are kept.
- The frontend is LeadStack's own application. Nothing is copied from the
  SaaS-OS frontend.

## 4. Files and migrations

- **Migration** `0003_crm_tables`: `companies`, `contacts`, forced RLS,
  composite foreign key with `ON DELETE SET NULL (company_id)`.
  - Before release, the unused `phone_raw` column was removed (review
    L7). This migration had only been applied to disposable databases.
- **Backend:**
  - `domains/crm/{models,service,contact_points}.py`
  - `routes/{crm,validation,origin}.py`
  - `admin.py`
- **Frontend:** `frontend/` (app, components, lib, config, tests).
- **CI:** frontend job (`npm ci --ignore-scripts`, typecheck, lint, tests,
  build, runtime `npm audit` as a blocking gate).
- **Docs:** `docs/upstream/saas-os-proposals.md`.

## 5. Tests and commands run

Final run, combined with Phase 4:

| Check | Result |
|---|---|
| ruff | pass |
| ruff format | pass |
| pyright | 0 errors |
| lint-imports | 4/4 kept |
| `uv lock --check` | pass |
| Unit tests | 45 passed |
| Integration tests (Postgres 16, Redis 7) | 102 passed |
| detect-secrets | clean |
| pip-audit | no known vulnerabilities |

Frontend:

| Check | Result |
|---|---|
| `tsc --noEmit` | pass |
| ESLint (`--max-warnings 0`) | pass |
| Vitest | 19 passed |
| `next build` | pass |
| `npm audit --omit=dev` | 0 vulnerabilities |

Phase 3 test files:
- `test_crm.py`: 17 tests.
- `test_crm_hardening.py`: 19 tests (concurrency, control characters,
  website schemes, null versus omitted fields, cross-tenant company
  references and cursors, wildcards, phone search, equal-timestamp
  pagination, RLS without tenant context, cross-origin rejection).
- `test_admin.py`.
- Frontend: `i18n.test.ts`, `components.test.tsx`, `contacts.test.tsx`.

Mutation checks (each killed):
- nested-checkout detector;
- `_bounded_can` bound;
- stale-search guard in the frontend.

## 6. Security and isolation audit (independent review)

| ID | Finding | Resolution |
|---|---|---|
| H1 | Pool self-deadlock on concurrent writes | LeadStack code no longer nests checkouts (enforced by a static test). Instrumentation showed SaaS-OS `can()` itself nests, so LeadStack bounds concurrent `can()` calls. 40-way concurrency test passes; mutation proves the bound is needed. Upstream P1 |
| M2 | NUL/control characters cause 500s; company delete race causes 500 | `StrictModel` and query check return 422; race mapped to `unknown_company` |
| M3 | `javascript:` websites | http/https only. An initial fix still accepted `javascript:`; caught by the new test and corrected |
| M4 | Purge test for CRM rows | Covered in `test_workspaces_hardening.py::test_tenant_purge_erases_every_leadstack_row` |
| M5 | Missing negative tests | Added (see §5) |
| L6 | Wrong error when clearing a company name | `company_name_required` |
| L7 | Unused `phone_raw` personal data | Column removed |
| L8 | Model/migration drift | Owner FK added to the model. Owner clean-up on member removal is documented future work |
| L9 | Phone search in national format | Supported |
| L10 | Out-of-order search results | Request sequence guard; component test; mutation killed |
| L11 | Companies list truncated | Paginated |
| L12 | Missing translations | Added |
| L13 | No CSP/HSTS | Added |
| L14 | CSRF relies on SameSite only | `SameOriginMiddleware`; upstream P6. Published pages must use a separate registrable domain (requirement for phase 11) |
| L15 | Static check bypassed by aliased imports; worker excluded | Detector resolves aliases; worker included through the allowlist |
| L16 | Downgrade destroys CRM data | Documented here: downgrading `0003` or `0004` drops CRM tables and their data. Use it only on disposable databases |

**Test-quality defect found and fixed during Phase 4.** Earlier
"no personal data in audit" assertions read `AuditLogEntry.metadata`. That
is SQLAlchemy's `MetaData` object; the record's metadata attribute is
`entry_metadata`. Those assertions were therefore vacuous. They now read
`entry_metadata`, and all pass.

## 7. SaaS-OS compatibility

Pin unchanged. A platform defect was found (upstream P1) and is mitigated
without modifying SaaS-OS.

## 8. Outstanding defects

None known within scope.

## 9. Limitations

- **CSP:** allows `'unsafe-inline'` scripts, because Next.js emits inline
  bootstrap scripts. A nonce-based policy is planned with production
  hardening.
- **Frontend end-to-end:** no browser end-to-end tests. Logging in needs a
  running ZITADEL instance. Component tests and API integration tests cover
  the behavior.
- **Dev-only advisory:** `npm audit` reports the `braces` advisory through
  `eslint-config-next`. This is development tooling only, and no patched
  version exists. Runtime dependencies are clean, and that is the blocking
  gate.
- **Search scale:** substring search scans within a tenant. Add a trigram
  index when data volume warrants it.
- **Duplicate detection and merge:** not yet implemented (CRM productivity
  backlog).
- **Load:** per-process authorization concurrency is bounded at 4, which
  limits authorization throughput per process. Tune it together with
  `DB_POOL_SIZE`, and scale horizontally.

## 10. Repository state

Untracked only. Nothing staged, committed or pushed. SaaS-OS untouched.
