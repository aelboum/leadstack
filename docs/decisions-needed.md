# Decisions needed from the owner

Date: 2026-10-10

These decisions block the remaining roadmap. Each entry gives what it
blocks, the options, and a recommendation. Recommendations are engineering
judgements based on what is in the repository. None of them replaces legal
or commercial advice. The decision log is in `docs/roadmap.md`.

## Summary

| ID | Decision | Blocks | Recommendation |
|---|---|---|---|
| D6 | Scheduling (delayed and recurring jobs) | Reminders, booking emails, delayed workflow steps, automatic recovery jobs | Ask SaaS-OS for delayed/cron jobs (proposal filed); until then run the three recovery commands from the host's cron |
| D14 | Encryption between app and data stores | First deployment | Managed PostgreSQL and Redis with TLS for production |
| Hosting | Provider, region, data residency | First deployment; Phase 13 legal questions | Decide together with the legal review |
| D12 | Repository visibility | First push to GitHub | Make the repository private before pushing |
| D7 | Billing and payment provider | Phase 12 (plans, entitlements) | Verify which providers serve Moroccan merchants before any build |
| D13 | SMS, WhatsApp and voice provider | Those channels | Start with one provider and WhatsApp only after template approval |
| D5 | AI runtime ownership | Phase 10 (AI features) | Thin LeadStack adapter governed by SaaS-OS's control plane; summaries first |
| D11 | SaaS-OS licence for a commercial product | Commercial launch | Obtain written terms |
| D4 | Agency access to client workspaces | Agency experience at scale | Keep direct memberships for now; propose a platform change |
| D15 | Who may send email, and what plain members may do | Safe launch of email | Gate sending by plan or verified workspace before opening sign-up |
| Legal | Data protection and communications rules | Phase 13 (launch readiness) | Commission a review before onboarding real customers |

## D6. Scheduling

- **Blocks:**
  - appointment reminders;
  - booking confirmation emails;
  - delayed and scheduled workflow steps;
  - automatic retry of queued emails and missed automation events.
- **Fact:** the pinned SaaS-OS job layer runs jobs immediately. It has no
  supported delayed or recurring execution. LeadStack must not simulate
  reliable scheduling with an in-memory timer (project rule).
- **Options:**
  1. SaaS-OS adds delayed and cron jobs (proposal in
     `docs/upstream/saas-os-proposals.md`). Cleanest; depends on the
     platform's release schedule.
  2. A small LeadStack scheduler adapter: a database table of due work,
     polled by a dedicated process. More code to own, but independent of
     the platform.
  3. Host cron calling `leadstack-admin` commands. This already works for
     recovery jobs. It is not suitable for per-appointment reminders at
     scale.
- **Recommendation:** option 1, with option 3 in the meantime. Choose
  option 2 only if the platform change is more than a few weeks away.

## D14. Encryption between the application and the data stores

- **Blocks:** the first deployment. The stack refuses to start until this
  is chosen.
- **Fact:** SaaS-OS requires TLS to PostgreSQL and Redis in production. The
  bundled containers are plaintext on a private network.
- **Options:**
  1. Managed PostgreSQL and Redis with TLS.
  2. Own certificates on the bundled containers (not built).
  3. Explicit opt-out for a single host.
- **Recommendation:** option 1 for production, because it also provides
  backups and failover. Option 3 is acceptable for a first private pilot
  on one host.

## Hosting provider, region and data residency

- **Blocks:** deployment, and several legal questions (cross-border
  transfer of personal data).
- **Fact:** nothing in the code depends on a provider. The deployment files
  need a Linux host with Docker.
- **Recommendation:** decide after the legal review states whether data may
  be hosted outside Morocco, and under which formalities.

## D12. Repository visibility

- **Blocks:** the first push.
- **Fact:** `https://github.com/aelboum/leadstack` is public and empty. The
  code documents the private SaaS-OS platform's internal interfaces and
  defects.
- **Recommendation:** make the repository private before the first push.
  The CI workflow also needs the `SAAS_OS_READ_TOKEN` repository secret.

## D7. Billing and payment provider

- **Blocks:** commercial plans, entitlement enforcement, invoices.
- **Fact:** SaaS-OS has billing and entitlement interfaces. LeadStack has
  not integrated them. No provider has been verified for Moroccan
  merchants, MAD settlement, or local card schemes.
- **Recommendation:** investigate provider availability and terms first;
  build nothing before that. Entitlements can be built against SaaS-OS's
  fake billing provider in the meantime, if wanted.

## D13. SMS, WhatsApp and voice

- **Blocks:** those channels. Email is built.
- **Prerequisites outside the code:**
  - provider accounts;
  - sender registration;
  - WhatsApp Business verification and template approval;
  - per-message costs;
  - opt-in rules per channel.
- **Recommendation:** choose one provider with documented Moroccan
  coverage. Build SMS behind the same consent and suppression model as
  email. Add WhatsApp once templates are approved.

## D5. AI runtime

- **Blocks:** AI receptionist, lead qualification, conversation summaries.
- **Fact:** the bootstrap audit found that SaaS-OS's AI control plane
  governs tools (registry, permissions, autonomy tiers, approvals, audit).
  It has no model calls, no agent loop and no conversation memory, and
  its approvals do not check the approver's permission
  (`docs/architecture/saas-os-integration.md`). So a model runtime must
  come from somewhere. No Darija evaluation exists.
- **Options:**
  1. Propose a model runtime to SaaS-OS and wait for it.
  2. A thin LeadStack adapter that calls a model provider and registers
     its tools with the control plane for governance.
- **Recommendation:**
  1. Option 2, the thin adapter.
  2. Build conversation summaries first: low risk, no autonomous actions.
  3. Create an evaluation set in French, Arabic and Darija before any
     customer-facing use.
  4. Re-verify the control plane's approval behaviour before any tool
     that changes data.

  No reliability claim for Darija can be made without evaluation
  evidence.

## D15. Who may send email, and what plain members may do

- **Fact (current defaults).**
  - Any signed-in user can create a workspace (10 per hour).
  - Any member can email any contact that has an address and has not
    unsubscribed, without prior consent. This is treated as one-to-one
    correspondence (ADR-0007).
  - Limits are 60 emails per member per hour and 200 per workspace per
    hour.
  - Everything goes out through one shared platform sender.
  - There is no plan or billing gate.
  - Plain members can also record an unsubscribe for a contact (only an
    admin can reverse it) and can create or cancel any appointment.
- **Risk:** with open sign-up, this is a path to unsolicited email from the
  platform's sender address. The legal basis for consent-free one-to-one
  email is unverified.
- **Options:**
  1. Keep the defaults, but do not open public sign-up.
  2. Allow sending only for workspaces on a paid plan or verified by an
     administrator.
  3. Require granted consent for every email.
  4. Move sending, and the other broad actions, to a separate role.
- **Recommendation:** option 1 for a private pilot and option 2 before
  public sign-up (needs D7), plus a per-workspace sender domain. Confirm
  the member permissions explicitly.

## D11. SaaS-OS licence

- **Fact:** the package metadata says `UNLICENSED`. LeadStack depends on it
  for every request.
- **Recommendation:** obtain written terms covering commercial use,
  redistribution inside container images, and continuity.

## D4. Agency access to client workspaces

- **Fact:** agency staff reach a client workspace only through a direct
  membership in it (option b). The platform does not honour inherited
  roles over HTTP.
- **Recommendation:** keep this for now. Propose the platform change when
  agencies with many clients make per-client invitations impractical.

## Legal and regulatory review

See `docs/audits/phase-13-groundwork.md`, section 4. The product makes no
compliance claim. Before onboarding real customers and their contacts:

- data protection obligations in Morocco (law 09-08, CNDP) and in any
  other jurisdiction of customers or contacts;
- rules for electronic marketing consent and opt-out;
- retention periods, including the hashed address kept for unsubscribed
  contacts;
- terms of service, privacy notice and data processing agreement.
