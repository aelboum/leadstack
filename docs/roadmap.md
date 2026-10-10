# LeadStack roadmap and decision log

The order is driven by dependencies. Since 2026-10-09 phases advance
autonomously under the owner's standing authorization; each closes only
with an audit report (CLAUDE.md §19). Approval-dependent decisions stay
listed below and block only the work that depends on them.
No time estimates are given.

## Phases

| # | Phase | Status | Depends on / gate |
|---|---|---|---|
| 1 | Repository foundation, pinned SaaS-OS, app skeleton, health checks, migrations, worker, CI | PASS WITH LIMITATIONS (`docs/audits/phase-1.md`); re-verified 2026-10-09; actions SHA-pinned | D1–D3 |
| 2 | Workspace tenancy integration: agency/client provisioning, roles, invitations over HTTP | PASS WITH LIMITATIONS (`docs/audits/phase-2.md`) | D4 (option b in use) |
| 3 | CRM contacts and companies (first vertical slice), frontend shell, i18n/RTL base | PASS WITH LIMITATIONS (`docs/audits/phase-3.md`) | Phase 2 |
| 4 | Notes, tasks, activity timeline, tags, custom fields, bulk actions | PASS WITH LIMITATIONS (`docs/audits/phase-4.md`; contact detail screen added, settings and edit screens pending: `docs/audits/frontend-consolidation.md`) | Phase 3 |
| 5 | Pipelines and deals (money and currency model) | PASS WITH LIMITATIONS (`docs/audits/phase-5.md`) | Phase 3 |
| 6 | Lead-capture forms (public endpoint, spam protection, rate limits) | PASS WITH LIMITATIONS (`docs/audits/phase-6.md`; proxy-header prerequisite; management screen added) | Phases 3, 5 |
| 7 | Workflow automation foundation (event-triggered, immediate actions) | PASS WITH LIMITATIONS (`docs/audits/phase-7.md`; delays/schedules blocked on D6; simple management screen added) | ADR-0006 |
| 8 | Email, then SMS/WhatsApp adapters, consent and opt-out | Email: PASS WITH LIMITATIONS (`docs/audits/phase-8.md`); SMS/WhatsApp/voice blocked on D13 | Phase 7; SMTP account for real delivery |
| 9 | Appointments and calendars | PASS WITH LIMITATIONS (`docs/audits/phase-9.md`; reminders/booking emails blocked on D6; public booking page and calendar screen added) | Phase 8 |
| 10 | AI agents: summaries, then qualification, then receptionist | Not started | D5; evaluation plan |
| 11 | Landing pages and custom domains | Not started | Hosting/TLS decision |
| 12 | White-label and commercial plans | Not started | D7, D8 |
| 13 | Moroccan launch readiness | BLOCKED on legal verification and native-speaker review; engineering groundwork done (`docs/audits/phase-13-groundwork.md`) | Legal/regulatory verification; D7, D13, D14 |
| 14 | Production hardening and release | Groundwork done (`docs/audits/phase-14-groundwork.md`): deployment files, guard tests, runbooks. Nothing deployed; backend image never built | D14; hosting choice; backups; observability exporter |

## Decisions

| ID | Decision | Status |
|---|---|---|
| D1 | Initialise Git, default branch `main` | Approved 2026-10-09 |
| D2 | Python 3.13 and uv (ADR-0003) | Approved 2026-10-09 |
| D3 | SaaS-OS pin `b126f1ad0c9cf4173ada15e20b14850d9e600c14` (ADR-0001) | Approved 2026-10-09 |
| D4 | Agency → client access: SaaS-OS change (a), direct memberships (b), own resolver (c) (ADR-0002) | Open; (b) for the first slice |
| D5 | AI runtime ownership: SaaS-OS agent-runtime proposal vs. thin LeadStack adapter | Open; AI runtime deferred until decided |
| D6 | Scheduling: SaaS-OS delayed/cron jobs vs. LeadStack scheduler adapter | Open; delayed/scheduled execution deferred until decided |
| D7 | Billing/payment provider for Morocco | Open |
| D8 | Login branding: wait for SaaS-OS ADR-0026 vs. ZITADEL-hosted login for MVP | Decided 2026-10-09: hosted ZITADEL login initially |
| D9 | Production deployment topology | Decided 2026-10-09: LeadStack-owned deployment configuration (not copied from SaaS-OS); topology details open |
| D10 | Database schemas: one `leadstack` schema vs. one schema per domain | Decided 2026-10-09: single `leadstack` schema initially |
| D11 | SaaS-OS licence (`UNLICENSED`) terms for a commercial product | Open |
| D12 | Visibility of the LeadStack GitHub repository (public vs private) | Open |
| D13 | SMS/WhatsApp/voice provider(s) for Morocco and beyond (accounts, sender registration, WhatsApp Business templates, costs) | Open; channels blocked until decided |
| D14 | Encryption between application and data stores in production: managed TLS services, own certificates, or explicit single-host opt-out (ADR-0009) | Open; the deployment files fail closed until decided |
| D15 | Who may send email and when: any member to any contact without prior consent (current), or gated by plan, verified workspace, role or consent; and whether plain members may record unsubscribes and manage all appointments | Open; current defaults are documented in ADR-0007 and ADR-0008 |

## External repository

`https://github.com/aelboum/leadstack` exists, is empty and is **public**
(checked 2026-10-09, read-only). No remote is configured and nothing has
been pushed: configuring remotes and pushing require explicit approval.
Before the first push, decide whether the repository should be public:
the code references the private SaaS-OS dependency and its internal
architecture (decision D12).
