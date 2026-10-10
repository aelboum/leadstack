# Phase 8 audit report: email consent, double opt-in and one-to-one email

Date: 2026-10-10

## 1. Verdict

**PASS WITH LIMITATIONS.**

- Email consent (double opt-in, unsubscribe, suppression list) and
  one-to-one email are implemented, tested and reviewed. The review ran
  three times:
  - First review: FAIL, with 4 high, 6 medium and 5 low findings.
  - Re-review: FAIL, with 2 high, 1 medium and 4 low findings. These were
    gaps in the first fixes.
  - Third review: PASS WITH LIMITATIONS, with 1 medium and 4 low
    findings, all fixed or documented.
- No cross-tenant path was found in any review.
- **Limitations:**
  - SMS, WhatsApp and voice are blocked on a provider decision (D13).
  - No bulk or marketing email.
  - No `List-Unsubscribe` header (SaaS-OS gap, P11).
  - No messaging UI beyond the consent pages.
  - No legal compliance verification.
  - No real email was sent: all tests use SaaS-OS `FakeEmailProvider`.

## 2. Scope completed

See ADR-0007 for the full design.

- **Consent per (contact, email):**
  - states `pending`, `granted` and `withdrawn`;
  - bound to an address hash and reset on address change;
  - `withdrawn_via` records `link` or `member`.
- **Suppression list** per workspace, keyed by address hash. It survives
  address edits, contact deletion and duplicate contacts. A contact's own
  unsubscribe is permanent in the workspace.
- **Double opt-in for form leads:**
  - the confirmation email names the workspace;
  - at most 1 per consent record per 24 h and 3 in total;
  - at most 3 per address per day across all workspaces, charged once per
    message.
- **Signed links:**
  - HMAC-SHA256, bound to purpose, workspace, contact, consent record and
    address hash;
  - confirmation links expire after 7 days; unsubscribe links do not
    expire;
  - signed at delivery time and never stored;
  - confirm and unsubscribe are POST-only and act only after a click on
    the app's pages, so mail scanners cannot trigger them.
- **One-to-one email:**
  - permission `leadstack.messages:send` (member);
  - limits of 60 per member and 200 per workspace per hour;
  - refused for withdrawn or suppressed addresses;
  - an unsubscribe link is added to every email.
- **Delivery job `leadstack_send_message`:**
  - at most once;
  - re-checks workspace, consent, suppression and current address at send
    time;
  - bounded retries;
  - configuration errors mark the message `failed`;
  - recovery with `leadstack-admin send-queued`.
- **Consent recording:**
  - admin-only grant (`leadstack.consent:manage`, a new permission);
  - members can record a withdrawal;
  - recording a withdrawal again is idempotent;
  - every change is audited on the contact timeline.
- **Locking:** one per-workspace consent advisory lock, also taken by CRM
  email changes. Lock order: consent lock, then row locks.
- **Frontend:** pages `/c/[token]` and `/u/[token]` in fr, ar and en (RTL).

## 3. Files and migrations

- **Migration** `0008_messaging`:
  - tables `contact_consents`, `messages` and `email_suppressions`;
  - forced RLS on all three;
  - composite same-tenant foreign keys to contacts with ON DELETE CASCADE
    (consents and messages only; suppressions deliberately have no
    contact link);
  - CHECK constraints on states.
  - This migration was edited in place during remediation. It has only
    ever been applied to disposable local test databases.
- **Code:**
  - `domains/messaging/{links,models,service}.py`
  - `integrations/email.py`
  - `routes/messaging.py`
  - `jobs.py`, `admin.py`
  - `crm/service.py` (consent reset and lock on email change)
  - `forms` (`SubmissionResult` ids, double opt-in hook, separate rate
    bucket for consent actions)
  - `workspaces/{permissions,service,purge}.py`
  - `routes/errors.py`
- **Frontend:**
  - `components/ConsentAction.tsx`
  - `app/c/[token]`, `app/u/[token]`
  - dictionary entries
- **Docs:**
  - ADR-0007
  - `docs/upstream/saas-os-proposals.md` P11
  - `.env.example`: `LEADSTACK_LINK_SIGNING_SECRET` and the SMTP and
    sender variables
- **Tests:**
  - `tests/integration/test_messaging.py`
  - `tests/integration/test_messaging_hardening.py`
  - `tests/integration/conftest.py`: the rate-limit reset fixture is now
    shared, which fixes test pollution across runs.

## 4. Tests and checks actually run

- **Full gate after the first remediation, before the re-review fixes**
  (2026-10-09). The first attempt was killed for lack of memory and rerun.
  - ruff, format, pyright (0 errors) and import contracts (4 kept);
  - lock check;
  - 75 unit tests and 224 integration tests passed;
  - detect-secrets clean.
- **Targeted runs after the third-review fixes:**
  - 75 unit tests passed;
  - messaging, messaging hardening, CRM and CRM hardening: 68 integration
    tests passed;
  - plus the defence-in-depth test.
- **Final full gate after all fixes:** passed, see section 8.
- **Frontend:** tsc, eslint, 21 vitest tests and the production build all
  passed.
- **Mutation checks.** Each mutation disables one safeguard, then the
  relevant tests are run.
  - Killed (13):
    - link withdrawal override;
    - confirmation throttle;
    - address-bound link (after a dedicated test was added);
    - CRM consent reset;
    - per-address limit;
    - suspended-workspace unsubscribe;
    - consent rate bucket;
    - idempotent withdrawal;
    - queue-time suppression;
    - link suppression;
    - quota charged once per message;
    - subject sanitizing;
    - duplicate-contact guard and the consent-record grant check.
  - Survived (1): the delivery-time consent lock. That race needs real
    concurrency; the sequential test covers only the ordering. It is
    documented, not tested.

## 5. Independent reviews and resolution

| ID | Finding | Resolution |
|---|---|---|
| R1-H1 | Email bombing through repeated double opt-in | Per-consent throttle (24 h, 3 total); per-address limit across workspaces in the job |
| R1-H2 | Confirm and unsubscribe links readable by any member (forged evidence) | Links signed at delivery, never stored; placeholders in stored bodies |
| R1-H3 | Member grant overrides a contact's unsubscribe | Admin-only grant; link withdrawals cannot be reversed |
| R1-H4 | Consent tied to the contact, not the address | Address hash on consent and links; reset on email change |
| R1-M5 | Unsubscribe fails for suspended workspaces | Only confirm is gated on workspace status |
| R1-M6/M7 | Stranded queued messages; config errors reported as outcome unknown | `failed/email_not_configured`; `send-queued` recovery |
| R1-M8 | No send quotas | 60 per member and 200 per workspace per hour |
| R1-M9 | No `List-Unsubscribe` header | Platform gap, upstream P11, documented |
| R1-M10 | Docs and code disagree on pending consent | Rule documented: one-to-one is correspondence; bulk needs `granted` |
| R1-L11–L15 | Unsubscribe link expiry; workspace not named; shared rate bucket; http origin allowed; retention | Unsubscribe links never expire; workspace named; separate bucket; https required outside dev/test; retention deferred to Phase 13 |
| R2-H1 | Admin launders an unsubscribe by withdrawing again, then granting | Repeat withdrawal is idempotent and keeps who withdrew |
| R2-H2 | Unsubscribe erased by an email round trip, contact re-creation or a duplicate | `email_suppressions` keyed by address hash; old links still honoured |
| R2-M3 | ADR claimed re-grant by confirmation | ADR: link unsubscribe is permanent; re-subscription flow not built |
| R2-L4 | Unsubscribe races delivery | Shared advisory lock held across the delivery check |
| R2-L5 | Retries consume the address quota | Charged on first attempt only |
| R2-L6 | CR/LF in workspace name strands the confirmation | Name collapsed to one line |
| R2-L7 | Concurrent first consent creation conflicts | Advisory lock on every consent insert |
| R3-M1 | CRM email change bypasses the consent lock (stale consent row) | CRM takes the consent lock before locking the contact row |
| R3-L1 | Quota can still be double-charged (`send-queued` duplicates; suppressed sends) | Documented; errs towards sending fewer emails |
| R3-L2 | Admin grant on contact B lifts an opt-out still held by contact A | Suppression kept while another contact with the address is withdrawn |
| R3-L3 | Unsalted SHA-256 hash retained after erasure | Documented; keyed HMAC deferred to the Phase 13 privacy review; extra grant check on the consent record |
| R3-L4 | Migration edited in place | Confirmed applied only to disposable test databases |

## 6. Security

- **Tenant isolation:**
  - forced RLS on all three new tables;
  - tenant ids come only from a verified token or the request context;
  - cross-tenant send returns 404 (tested).
- **No nested connection checkouts:** the static discipline test passes.
  `workspace_display_name` and `get_tenant` run before sessions open.
- **Header injection:** blocked by a subject CR/LF check, by collapsing
  the workspace name to one line, and by SaaS-OS validation.
- **Fail closed:** a missing signing secret or origin returns 503 at
  request time and marks the message `failed` at delivery.
- **Secrets:**
  - none committed;
  - `LEADSTACK_LINK_SIGNING_SECRET` is read through the SaaS-OS secrets
    provider;
  - the SMTP credentials stay with SaaS-OS `core.email`.

## 7. Limitations and external prerequisites

- **Real delivery is untested.** It needs SMTP credentials, plus
  SPF/DKIM/DMARC for the sender domain. Nothing was sent to a real mail
  server.
- **Blocked or not built:**
  - SMS, WhatsApp and voice: blocked on D13 (provider choice and
    accounts);
  - bulk and marketing campaigns, templates, per-workspace sender domains,
    `Reply-To` and `List-Unsubscribe` (P11): not built;
  - a contact-initiated re-subscription flow: not built.
- **Operations:**
  - automatic `send-queued` scheduling waits for D6;
  - existing workspaces need `leadstack-admin sync-roles` to receive the
    new permissions.
- **Not verified:**
  - legal compliance (law 09-08 / CNDP, other jurisdictions);
  - message retention policy;
  - the hashed-address retention choice.

## 8. Final gate

Run on 2026-10-10 after all three remediation rounds. Every check passed:

- ruff: all checks passed; format: 120 files already formatted;
- pyright: 0 errors;
- import contracts: 4 kept, 0 broken;
- `uv lock --check`: consistent;
- 75 unit tests and 226 integration tests passed;
- detect-secrets: clean (exit 0).

## 9. Repository state

Untracked only. No commit, push or deployment. SaaS-OS untouched.

## 10. Next step

Phase 9, appointments.

- **Scope:** booking types, weekly availability in the workspace time
  zone, public slot listing and booking with conflict-free reservation,
  and cancellation.
- **Blocked:** reminders, until D6 is decided.
- **Already done:** the DST-safe slot engine and its unit tests exist,
  including Morocco's Ramadan offset changes.
- **Acceptance:**
  - no double booking under concurrency (database constraint);
  - correct local and UTC handling across DST;
  - tenant isolation;
  - public endpoint abuse limits;
  - a booking creates or links a contact without mutating existing ones.
