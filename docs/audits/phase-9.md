# Phase 9 audit report: appointments and public booking

Date: 2026-10-10

## 1. Verdict

**PASS WITH LIMITATIONS.**

- Booking types, weekly availability, DST-safe slot computation, public
  booking and member appointment management are implemented, tested and
  reviewed.
- The independent review found no tenant-isolation and no double-booking
  defect. It found 2 medium and 10 low issues; all are fixed or recorded as
  decisions in ADR-0008.
- **Limitations:**
  - reminders are blocked on D6;
  - booking confirmation emails are deferred, to be built with reminders;
  - no external calendar sync;
  - the public booking page exists (`/b/[publicKey]`, fr/ar/en); the
    member calendar and booking-type management views are pending
    (frontend consolidation).

## 2. Scope completed

See ADR-0008 for the full design.

- **Booking types:**
  - fields: host, duration, buffer, minimum notice, horizon, IANA time
    zone and weekly rules in local minutes;
  - an unguessable public key;
  - admin-managed (`leadstack.booking_types:manage`);
  - creation, status changes and deletion are audited.
- **Slot engine:**
  - pure function;
  - a skipped local time yields no slot; a repeated local time yields one
    slot;
  - slots must end inside their window in real local time;
  - availability can run until midnight;
  - Morocco's Ramadan offset changes come from the tz database.
- **Pinned tzdata:**
  - `tzdata==2026.5` is a direct dependency;
  - the OS time zone path is disabled and the cache cleared when
    `leadstack` is imported.
- **Public booking:**
  - `GET` and `POST /v1/public/booking/{key}`;
  - a client-bound token scoped to booking;
  - honeypot and consent checkbox;
  - limits are consumed only after validation: 5 per client per hour, 60
    per page per hour, 100 per page per day, 500 per workspace per day;
  - caps are counted under a workspace lock;
  - an existing contact is linked by email and never modified;
  - contact events are dispatched.
- **No double booking:**
  - a per-host advisory lock with a re-check of overlaps including the
    buffer, on both the public and the manual path;
  - a partial unique index on (tenant, host, starts_at) for booked rows as
    a backstop;
  - only that constraint maps to `slot_unavailable`.
- **Members** (`leadstack.appointments:read` and `:manage`):
  - list appointments by range (at most 62 days and 1,000 results, refused
    beyond that);
  - manual appointments with an overlap check;
  - cancellation, after which the time is free again;
  - booking and cancellation are audited on the contact timeline.

## 3. Files and migrations

- **Migration** `0009_scheduling`:
  - `booking_types` and `appointments` with forced RLS;
  - `booking_keys`, a reviewed non-RLS public-key index containing
    identifiers only, as with `form_keys`;
  - composite same-tenant foreign keys: contact ON DELETE CASCADE (erasure)
    and booking type ON DELETE SET NULL (column);
  - CHECK constraints.
- **Code:**
  - `domains/scheduling/{slots,models,service}.py`
  - `routes/scheduling.py`, `routes/errors.py`
  - permissions, purge and `workspace_profile`
  - forms helpers `check_token` and `match_or_create_contact`
  - `leadstack/__init__.py`
- **Dependency:** `tzdata==2026.5` (`uv.lock` updated; SaaS-OS pin
  unchanged).
- **Tests:**
  - `tests/unit/test_slots.py`
  - `tests/integration/test_scheduling.py`
  - `test_schema_security` and `test_tenant_scope_discipline` allowlists:
    `booking_keys` and `scheduling._resolve`.

## 4. Tests and checks actually run

- **Full gate before remediation:**
  - ruff, format, pyright (0 errors), import contracts (4 kept),
    `uv lock --check` consistent (rerun directly; exit 0);
  - 75 unit tests and 244 integration tests passed;
  - detect-secrets clean.
- **Frontend** (public booking page): tsc, eslint, 25 vitest tests and the
  production build passed; `npm audit --omit=dev` found 0 vulnerabilities.
  The page was not exercised in a browser against a running backend.
- **After remediation:**
  - 78 unit tests passed;
  - scheduling and forms integration tests: 47 passed;
  - final full gate: see section 8.
- **Mutation checks** (each mutation disables one safeguard, then the
  relevant tests are run).

  Killed (15):
  - the "slot must be offered" check;
  - the manual overlap check;
  - the host check at booking;
  - the consent check;
  - token scoping;
  - the busy-interval buffer;
  - the per-host lock: killed only after a dedicated test for concurrent
    overlapping slots was added; the same-start race is also held by the
    unique index;
  - consuming limits before validation (M1);
  - the per-type daily cap;
  - the honeypot;
  - the host check on the public page;
  - event dispatch;
  - the deletion audit;
  - the DST-forward overrun check.

## 5. Independent review and resolution

| ID | Finding | Resolution |
|---|---|---|
| M1 | Per-type quota consumed before the token was validated | `prepare_booking` (token, consent, honeypot, details, host) runs before any limit; `store_booking` follows |
| M2 | One client can take the workspace daily cap; no honeypot; cap not under a workspace lock | 5 per client per hour; 100 per page per day; honeypot; workspace quota lock (order: quota, then host) |
| L1 | DST-forward slot overruns the window | The real local end must lie inside the window |
| L2 | No event dispatch after booking | `dispatch_events` queued as a background task |
| L3 | Booking consent not stored | Documented: never authorizes messaging |
| L4 | A booking can link any existing contact by email | Documented as the accepted forms rule; the contact is never modified |
| L5 | Public page shows slots after the host is removed | Host re-checked; 404 |
| L6 | Members manage any host's appointments | Documented decision (shared team calendar) |
| L7 | Every IntegrityError reported as `slot_unavailable` | Only the booking constraint maps to it; others propagate |
| L8 | Booking-type status and deletion not audited | Audited |
| L9 | ZoneInfo cache not cleared | `ZoneInfo.clear_cache()`; a test asserts the empty TZPATH |
| L10 | Silent truncation at 1,000 | `range_too_large` (422) |

Re-review of the fixes: see section 8.

## 6. Security

- **Tenant isolation:** forced RLS on the tenant tables. The public index
  holds identifiers only, and deletion and purge filter it by tenant.
  Cross-tenant cancel returns 404 (tested).
- **Public endpoints:**
  - a body guard and per-client limit apply before parsing;
  - tokens are scoped to booking, so form tokens are rejected (tested);
  - no information about other bookings or about the host's membership is
    returned.
- **Connections:** no nested connection checkouts. `authorize`,
  `get_tenant`, `workspace_profile` and audit run outside sessions, and the
  static discipline test passes.

## 7. Limitations and external prerequisites

- **Waiting on D6:** reminders and booking confirmation and cancellation
  emails.
- **Not built:**
  - external calendar sync (needs OAuth applications);
  - rescheduling links;
  - resource and round-robin scheduling;
  - member calendar and booking-type management UI.
- **Accepted limitation:** many addresses can still fill a host's calendar,
  up to the caps.
- **tzdata upgrades** must be deliberate: the Morocco tests pin the
  expected offsets for the 2026e/2026.5 database.

## 8. Final gate and re-review

**Re-review of the fixes: PASS WITH LIMITATIONS.** All 12 earlier findings
were confirmed fixed, with no new critical, high or medium finding. Five
new low findings:

- Hourly limits count validated attempts, not only successful bookings:
  documented in ADR-0008.
- A filled honeypot returns an explicit 422: documented.
- The host is checked at validation, not again at storage (a millisecond
  window): documented.
- Public bookings in a workspace are serialized by the quota lock:
  documented as a scaling limit.
- An explicit per-type cap of 0 was replaced by the default: fixed (`is
  None` check).

**Final full gate, run on 2026-10-10 after all fixes. Every check passed:**

- ruff: all checks passed; format: 128 files already formatted;
- pyright: 0 errors;
- import contracts: 4 kept, 0 broken;
- `uv lock --check`: consistent;
- 78 unit tests and 250 integration tests passed;
- detect-secrets: clean (exit 0).

Two earlier attempts at this final gate did not complete, and neither
counts as a result:

- One was stopped by the development machine for lack of memory.
- One errored on setup for all 250 integration tests because the
  disposable test database container had stopped when Docker restarted.
  The local gate script now restarts the test services and aborts clearly
  when they are unavailable.

## 9. Repository state

Untracked only. No commit, push or deployment. SaaS-OS untouched.
