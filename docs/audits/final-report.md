# LeadStack: final report

Date: 2026-10-10

This report closes the autonomous execution run that started with Phase 1.
It follows the report structure required by `CLAUDE.md` §19. It states what
exists, how it was verified, what is blocked and on whom, and what must
happen before a real launch. Per-phase detail is in the other reports in
this directory.

## 1. Verdict

**PASS WITH LIMITATIONS, and not launch-ready.**

- As an engineering baseline the repository is sound. The independent
  end-to-end audit found no critical defect. It found one high-severity
  issue, an unignored secrets file, which is fixed and now guarded by a
  test.
- **Verified** means, in this repository: automated tests against real
  PostgreSQL 16 and Redis 7, static checks, and independent read-only
  reviews. It does **not** mean a running production system.
- Three things have never happened and are the main limitations:
  - the backend container image has never been built;
  - the full stack has never been started together;
  - CI has never run (nothing is committed or pushed).
- The remaining roadmap work is blocked on owner decisions, listed with
  options and recommendations in `docs/decisions-needed.md`.

## 2. Baseline

- **Repository:** `main`, no commits, no remote. All files are untracked,
  as instructed (no commit, push, publication or deployment). Rechecked
  when this report was written.
- **Starting point:** `CLAUDE.md` is the only project file that predates
  the run. The recorded initial baseline is in `phase-1.md`.
- **SaaS-OS dependency:** unchanged, at commit
  `b126f1ad0c9cf4173ada15e20b14850d9e600c14` in `pyproject.toml` and
  `uv.lock`. No SaaS-OS source is copied or vendored (confirmed by the
  final audit). The SaaS-OS repository was only read.
- **`CLAUDE.md`:** unchanged (SHA-256 begins `6b5125958ffe8964`).
- **Runtime:** Python 3.13 with uv; Next.js 15 and React 19 for the
  frontend.

## 3. Changes

### 3.1 Status by phase

| # | Phase | Status | Report |
|---|---|---|---|
| 1 | Repository foundation, pinned SaaS-OS, app skeleton, CI definition | PASS WITH LIMITATIONS | `phase-1.md` |
| 2 | Workspaces, roles, invitations | PASS WITH LIMITATIONS | `phase-2.md` |
| 3 | Contacts and companies, frontend shell, i18n/RTL base | PASS WITH LIMITATIONS | `phase-3.md` |
| 4 | Notes, tasks, timeline, tags, custom fields, bulk actions | PASS WITH LIMITATIONS | `phase-4.md` |
| 5 | Pipelines and deals, money model | PASS WITH LIMITATIONS | `phase-5.md` |
| 6 | Lead-capture forms | PASS WITH LIMITATIONS | `phase-6.md` |
| 7 | Workflow automation (event-triggered, immediate actions) | PASS WITH LIMITATIONS | `phase-7.md` |
| 8 | Email consent, double opt-in, one-to-one email | PASS WITH LIMITATIONS | `phase-8.md` |
| 8 | SMS, WhatsApp, voice | BLOCKED (D13) | `decisions-needed.md` |
| 9 | Appointments and public booking | PASS WITH LIMITATIONS | `phase-9.md` |
| 9 | Reminders, booking emails | BLOCKED (D6) | `decisions-needed.md` |
| — | Frontend screens for phases 4 and 6–9 | PASS WITH LIMITATIONS | `frontend-consolidation.md` |
| 10 | AI features | BLOCKED (D5); not started | `decisions-needed.md` |
| 11 | Landing pages, custom domains | BLOCKED (hosting and domain decision); not started | — |
| 12 | White-label, plans, entitlements | BLOCKED (D7); not started | `decisions-needed.md` |
| 13 | Moroccan launch readiness | BLOCKED (legal review, native-speaker review); groundwork done | `phase-13-groundwork.md` |
| 14 | Production hardening and release | Groundwork only; nothing deployed | `phase-14-groundwork.md` |

### 3.2 What the repository contains

- **Backend** (`src/leadstack/`): seven product domains (workspaces, CRM,
  sales, forms, automation, messaging, scheduling), their HTTP routes,
  background jobs, an operator command line, and one email adapter.
- **Database:** nine product migrations (`0001` to `0009`) in the single
  `leadstack` schema, creating 27 tables.
- **Frontend** (`frontend/`): workspace screens for contacts, contact
  detail, companies, deals, forms, automations and calendar; public pages for forms,
  booking, email confirmation and unsubscribe; French, Arabic and English.
- **Deployment** (`deploy/`): container build files, a Compose file for a
  single host, edge proxy configuration, a database role script and an
  example settings file.
- **CI definition** (`.github/workflows/ci.yml`): five jobs (quality,
  integration, frontend, deploy, security). It has never run.
- **Documentation** (`docs/`): nine architecture decision records, the
  roadmap, the open decisions, three architecture documents,
  eleven proposals for the SaaS-OS project, three operations documents and
  thirteen audit reports.

### 3.3 Capability status against the target scope

Assessed by the independent final audit against `CLAUDE.md` §4.

| Capability | Status |
|---|---|
| Agency and client workspaces, invitations, roles, audit | Implemented and tested (API); member and invitation screens not built |
| Agency access to client workspaces | Partial: direct memberships only (D4) |
| White-label, subscriptions, entitlements | Not started (D7) |
| Contacts, companies, tags, custom fields, notes, tasks, timeline, search, bulk actions | Implemented and tested; settings and contact-edit screens not built |
| Import and export, duplicate detection and merge, segmentation | Not started |
| Consent and communication preferences | Email only; implemented and tested |
| Retention and deletion workflows | Partial: contact erasure and workspace purge; no retention policy |
| Pipelines, stages, deals, money and currency, stage history | Implemented and tested |
| Sales reporting | Partial: stage summary only |
| Workflows: event-triggered, idempotent, history, pause | Implemented and tested (3 triggers, 3 actions) |
| Workflows: delays, schedules | Blocked (D6) |
| Workflows: branches, provider actions | Not started |
| Email: one-to-one, double opt-in, unsubscribe, suppression | Implemented and tested with a fake provider; real delivery unverified |
| SMS, WhatsApp, voice | Blocked (D13) |
| Website chat, in-app notifications | Not started |
| AI features | Not started (D5) |
| Lead forms with spam protection | Implemented and tested |
| Landing pages, custom domains | Not started |
| Booking types, availability, public booking, time zones and DST, conflict prevention, cancellation | Implemented and tested |
| Rescheduling, external calendars, resource scheduling | Not started |
| French, Arabic and English interface; right-to-left | Implemented; translations lack native-speaker review |
| Deployment and operations | Groundwork only; never run end to end |

## 4. Architecture

- **Product over platform.** LeadStack is a separate repository that
  installs SaaS-OS as a private git dependency pinned to one commit. It
  uses the platform for the application shell, identity, tenancy, roles
  and permissions, audit log, database sessions, background jobs, rate
  limiting, secrets and email transport. The interfaces used are listed in
  `docs/architecture/saas-os-integration.md`.
- **No duplication.** LeadStack has no identity system, tenant system, job
  framework or audit log of its own. Four import-boundary rules, checked
  on every gate, keep product code on the supported platform interfaces
  and keep domains from reaching into each other's internals.
- **Tenancy.** A workspace is a platform tenant. Every product table
  carries the tenant identifier and has forced row-level security, except
  three reviewed lookup tables that hold identifiers only
  (`user_workspaces`, `form_keys`, `booking_keys`). Relations between
  tables use composite same-tenant foreign keys.
- **Platform gaps.** Eleven gaps (P1 to P11) are written up as proposals in
  `docs/upstream/saas-os-proposals.md`. None was worked around by modifying
  or copying the platform.
- **Deferred by decision.** There is no scheduler and no AI runtime in
  LeadStack. Both were deferred (D6, D5) rather than simulated.
- **Decisions on record.** ADR-0001 to ADR-0009 in `docs/adr/`. ADR-0009
  (deployment topology) is still *proposed*, pending D14 and the hosting
  choice.

## 5. Verification

### 5.1 Last full gate, on the final tree

Run on 2026-10-10 after every fix in section 6.2. Every check passed:

- ruff: all checks passed; format: 139 files already formatted;
- pyright: 0 errors;
- import contracts: 4 kept, 0 broken;
- `uv lock --check`: consistent;
- 99 unit tests and 256 integration tests passed (PostgreSQL 16, Redis 7);
- detect-secrets: clean (exit 0).

### 5.2 Frontend

- `tsc`, `eslint` and 37 vitest tests passed, rerun after the last gate.
- `next build` passed after the last frontend change. It was not rerun
  after the later backend-only fixes.
- `npm audit --omit=dev`: 0 vulnerabilities.

### 5.3 Other verification

- **Independent reviews:** every phase had at least one read-only review by
  a separate reviewer. Phase 8 needed three rounds (the first two verdicts
  were FAIL) and Phase 9 two. All findings were fixed, or documented as
  decisions or limitations.
- **Mutation checks:** for security-relevant fixes, the safeguard was
  disabled and the tests rerun to confirm they fail. The survivors, and why
  they survive, are listed in the phase reports.
- **Deployment files:**
  - compose and edge configuration validated;
  - database role script and the full migration chain run on PostgreSQL 16
    with a non-superuser owner;
  - frontend image built and run;
  - bundled data stores started from the compose file.
- **Final end-to-end audit** (read-only, cross-cutting) confirmed:
  - a clean platform boundary;
  - 27 tables, all with forced row-level security except the three
    reviewed lookup tables;
  - a permission dependency plus a service-level authorization on every
    workspace route;
  - rate limits and input bounds on every public route;
  - no secrets in the tree;
  - no skipped or weakened tests;
  - its spot-checked limits and constants match the code.

### 5.4 Not run

- The CI workflow (nothing is committed).
- The backend image build (needs the SaaS-OS read token).
- The full stack, and therefore any test through the edge proxy.
- Browser end-to-end tests (none exist).
- Sign-in against a real identity provider, and real email delivery.
- Any load test or penetration test.

### 5.5 Environment incidents

None of these changes a result:

- The development machine twice stopped a gate for lack of memory. Both
  gates were rerun.
- One gate errored because the test database container had stopped. It was
  rerun.

## 6. Security

### 6.1 Summary

- **Tenant isolation:**
  - forced row-level security on every tenant table;
  - composite same-tenant foreign keys;
  - the application role cannot bypass row-level security, and the app
    refuses to start otherwise;
  - cross-tenant tests in every domain.
- **Authorization:** checked at the route and again in the service, and
  re-checked at execution time for workflows and for email delivery.
- **Public endpoints** (forms, booking, consent links):
  - body size cap and per-client limits before parsing;
  - anti-bot tokens bound to the page and the client;
  - honeypots;
  - per-page and per-workspace caps.
- **Email:**
  - double opt-in;
  - signed links that are never stored;
  - an unsubscribe by the contact is permanent in the workspace and
    survives contact edits and deletion;
  - at-most-once delivery.
- **Secrets:** none in the tree; production secrets file ignored by git and
  Docker; the build token is a BuildKit secret; the CI token is scoped.
- **Migrations:** no applied migration was rewritten; the chain runs as a
  non-superuser owner without role-creation rights.

### 6.2 Final audit findings and resolution

| ID | Finding | Resolution |
|---|---|---|
| H1 | `deploy/production.env` (real passwords and keys) was not git-ignored, though the docs said it was; the target repository is public | Ignored (`*.env`, explicit path); guard test checks git and Docker ignore rules |
| H2 | No recorded full passing gate on the final tree; CI never run; backend image and full stack never run | Full gate rerun on the final tree (section 5.1). The other three remain open: they need a commit, the SaaS-OS token and a host |
| M1 | CI exposed the SaaS-OS token to the frontend job | Token scoped to the three Python jobs; test added |
| M2 | Any member can email any contact without prior consent through a shared sender; open workspace creation; no plan gate | Owner decision D15, with recommendation; not changed unilaterally |
| M3 | Member role is broad (send email, manage all appointments, record unsubscribes) | Part of D15 |
| M4 | Migrations were tested only as a superuser | New integration test runs both histories as a non-superuser owner without role-creation rights |
| M5 | CI did not cover `deploy/` | New CI job: compose validation, edge config validation, frontend image build and start |
| M6 | Email delivery did not re-check the sender's permission at send time | Re-authorized at delivery; an email queued by a member who lost access is suppressed (test; mutation killed) |
| Low | Route and service required different permissions to list invitations | Aligned |
| Low | Automation lock could be released by a worker that no longer held it | Compare-and-delete with an ownership token (test; mutation killed) |
| Low | `send-queued` did not skip inactive workspaces | Skips them |
| Low | Some tables lack a foreign key to the platform's tenant table; cleanup relies on the purge participant | Documented; candidate for a later migration |
| Low | Stale statements in the roadmap; incomplete interface table | Corrected |
| Low | CSP allows inline scripts; dev-tool audit is non-blocking | Open (frontend hardening) |

### 6.3 Not verified

Behaviour behind the real edge proxy, real identity provider login, real
email delivery, and resistance to a penetration test. No legal or
compliance claim is made anywhere in the product or its documentation.

## 7. Limitations

- **Never run end to end.** The backend image has never been built and the
  services have never run together. Rate limiting depends on the edge
  passing the client address correctly; this is configured and
  guard-tested but not observed.
- **CI is unproven.** The workflow is checked by unit tests for structure
  only. Its first real run may fail for reasons not visible locally
  (Linux runner, secret configuration).
- **Email uses a fake provider in tests.** Real delivery, bounce handling
  and sender-domain setup are not done.
- **No scheduler (D6).** There are no reminders, no booking emails and no
  delayed workflow steps. Recovery commands must be run from the host's
  cron.
- **Sending defaults are permissive (D15).** Any member can email any
  contact, and anyone can create a workspace.
- **Translations are unreviewed.** French and Arabic texts were written
  without a native speaker.
- **Booking splits a full name at the first space.** This is wrong for
  many names (gap G2 in `phase-13-groundwork.md`).
- **Screens not built:** tags and custom-field settings, contact edit,
  submissions list, workflow run history, manual appointment creation,
  members and invitations.
- **No observability beyond logs and health checks:** no metrics, alerts
  or log shipping.
- **No tested restore.** The backup procedure is written, not exercised.
- **Licence terms** of SaaS-OS for a commercial product are not recorded
  (D11).
- **Encryption to the data stores (D14):** the deployment files require
  TLS to PostgreSQL and Redis by default, which the bundled single-host
  stores do not provide without the documented opt-out.

## 8. Repository state

- All files are untracked on `main`. Nothing is staged, committed or
  pushed. No remote is configured.
- No package or image was published and nothing was deployed.
- The SaaS-OS repository and its pin were not modified. `CLAUDE.md` is
  unchanged.
- The disposable local test containers `leadstack-test-pg` and
  `leadstack-test-redis` are still running on the development machine.
  They can be removed with
  `docker rm -f leadstack-test-pg leadstack-test-redis`.

## 9. Remaining work

### 9.1 Blocked on an owner decision

| Priority | Decision | Unblocks |
|---|---|---|
| 1 | D12: repository visibility (the GitHub repository is public) | First commit and push, first CI run |
| 1 | D14 and hosting location: encryption to the data stores | First deployment, backups |
| 1 | D15: who may send email, and who may create a workspace | Opening sign-up safely |
| 2 | D6: scheduling mechanism | Reminders, booking emails, delayed workflow steps, automatic recovery |
| 2 | Legal review; native-speaker review of French and Arabic | Onboarding real customers (Phase 13) |
| 2 | D11: SaaS-OS licence terms | Commercial use |
| 3 | D7: billing provider | Plans, entitlements, white-label (Phase 12) |
| 3 | D13: SMS and WhatsApp provider | Those channels |
| 3 | D5: AI runtime | All AI features (Phase 10) |
| 3 | D4: agency access model | Agency management of client workspaces |

### 9.2 Not blocked, not built

| Priority | Item |
|---|---|
| 1 | Build the backend image and run the full stack once (needs the SaaS-OS read token) |
| 1 | Browser end-to-end tests for sign-in, form submission, booking and unsubscribe |
| 2 | Missing screens listed in section 7 |
| 2 | Name handling in booking (G2) |
| 2 | Metrics, alerts and log shipping |
| 2 | Stricter content security policy |
| 3 | Import and export, duplicate merge, segmentation |
| 3 | Rescheduling, external calendars |
| 3 | Workflow branches and provider actions |
| 3 | Foreign keys to the platform tenant table where missing |

### 9.3 Top risks

1. The stack has never run end to end.
2. Email abuse and legal exposure if sign-up is opened with the current
   sending defaults (D15, legal review).
3. No scheduler: no reminders, and recovery jobs depend on the host's
   cron (D6).
4. Unencrypted traffic between the application and the data stores if the
   single-host opt-out is chosen (D14); no tested restore; no alerts.
5. Dependence on a private platform whose licence terms for commercial use
   are not recorded (D11), and on eleven platform gaps (P1 to P11).

## 10. Next step

**Recommended next phase: first integration run ("Phase 14a").** It turns
the three "never happened" limitations into evidence, and needs only D12
and the SaaS-OS read token.

Steps, in order:

1. Decide D12, then authorize the first commit and push.
2. Add the `SAAS_OS_READ_TOKEN` repository secret and run CI.
3. Build the backend image and start the full stack on a disposable host
   or locally.

Acceptance criteria:

- One CI run with all five jobs green on the pushed commit.
- The backend image builds from the pinned dependency with the token
  passed only as a build secret, and the image contains no token.
- All services report healthy, and the migration job exits successfully as
  the non-superuser owner.
- Through the edge proxy, with a real browser: sign-in through the hosted
  login; a form submission creates a contact; a booking creates an
  appointment; a confirmation link and an unsubscribe link work.
- A per-client rate limit triggers for one client address and not for
  another, proving the edge passes the address correctly.
- A backup is taken and restored into an empty database, and the
  application starts against it.

After that, in order: D14 and hosting, D15, the legal and language
reviews, D6, and observability. Do not onboard real customers before the
first five are done.
