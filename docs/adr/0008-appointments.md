# ADR-0008: Appointments, availability and public booking

Status: Accepted (Phase 9, 2026-10-10). Reminders are blocked on D6
(scheduling). External calendar sync (Google, Microsoft) is not in scope.

## Context

Agencies and service businesses need prospects to book a meeting slot,
and members need to see and manage those appointments. Bookings are
public (no login), so the endpoint has the same abuse profile as lead
forms (ADR-0005). Time handling must be correct across time zones and
daylight-saving changes, including Morocco's Ramadan offset changes
(CLAUDE.md §5.4). SaaS-OS provides no scheduling or calendar capability at
the pinned commit, and delayed jobs are not decided (D6).

## Decision

### Model

- **Booking type:**
  - fields: name, duration (5–480 min), buffer after (0–240 min), minimum
    notice, booking horizon (at most 90 days), IANA time zone and status;
  - one **host**, who must be an active workspace member;
  - an unguessable public key.
- **Availability:** weekly rules (weekday, start and end minute) in the
  booking type's **local wall-clock time**.
- **Appointment:**
  - `starts_at` and `ends_at` in UTC (`timestamptz`), plus the host, the
    booking type and the contact;
  - status `booked` or `cancelled`;
  - source `public` or `manual`.
- **Slot computation** (`domains/scheduling/slots.py`) is pure:
  - local rules become UTC instants through `zoneinfo`, never a fixed
    offset;
  - a skipped local time (spring forward) yields no slot;
  - a repeated local time (fall back) yields one slot;
  - a slot must also *end* inside its window in real local time, so a
    slot cannot run past the window when the clocks jump forward;
  - the time zone database is the lock-pinned `tzdata` package. This is a
    process-wide setting that also applies to SaaS-OS code in the same
    process. `ZoneInfo` objects created before `leadstack` is imported keep
    their earlier data; arithmetic on aware datetimes is unaffected. The
    search path is emptied and the zone cache cleared when `leadstack` is
    imported, so results do not depend on the host operating system.

### No double booking

- A booking takes a per-host advisory lock (`leadstack.booking:{host}`),
  re-reads the host's overlapping `booked` appointments in the same
  transaction, and refuses on overlap, buffer included.
- A partial unique index on (tenant, host, starts_at) for `booked` rows is
  a backstop.
- A PostgreSQL exclusion constraint was rejected: it needs the
  `btree_gist` extension, and LeadStack cannot assume extension rights on
  the SaaS-OS database.
- Overlap is checked per host across **all** of the host's booking types.

### Public booking

- `GET /v1/public/booking/{key}?from=&to=`:
  - returns the type, its time zone and free slots;
  - the range is capped at 31 days and the horizon;
  - returns a client-bound anti-bot token (the forms mechanism).
- `POST /v1/public/booking/{key}`:
  - body: start, name, email, optional phone, consent and token;
  - the slot must be one the engine offers at request time;
  - goes through the public body guard and per-client, per-type and
    per-workspace daily limits.
- **Contact:** a booking links an existing contact by email without
  modifying it, or creates a new one (the same rule as forms).
- **Response:** a booking reference only. No other appointment or host
  details are exposed.
- **Abuse limits.** All booking-specific limits are consumed only after
  the anti-bot token, consent, honeypot and contact details pass. Junk
  requests therefore cannot lock real bookers out of a page. The limits:
  - 5 accepted bookings per client address per hour;
  - 60 per booking page per hour;
  - 100 per booking page per rolling 24 hours;
  - 500 per workspace per rolling 24 hours
    (`LEADSTACK_BOOKING_DAILY_PUBLIC`).

  The daily caps are counted under a workspace-wide advisory lock
  (`leadstack.booking_quota`). Lock order: the quota lock, then the host
  lock.
- **Accepted limitation, limits count attempts:** the per-client and
  per-page hourly limits count validated *attempts*, not only successful
  bookings. A visitor who keeps choosing slots that were just taken can be
  slowed down for an hour, and many clients holding valid tokens can use up
  a page's hourly allowance without creating appointments. The daily caps
  count successful bookings only.
- **Honeypot response:** a filled honeypot gets an explicit 422. This
  reveals detection, but a booking cannot convincingly fake an appointment
  reference, unlike the silent success that forms return.
- **Host removal race:** the host's authority is checked when the request
  is validated, not again when the booking is stored. A host removed in
  the milliseconds between the two steps can receive one last booking.
- **Throughput:** public bookings in a workspace are serialized by the
  quota lock. The transaction is short, which is acceptable at expected
  volumes, but it is a scaling limit to revisit.
- **Accepted limitation, calendar filling:** a determined actor with many
  addresses can still fill a host's free slots, at most to the caps
  above. Each booking creates a contact.
- **Booking consent** is a required checkbox. It is **not** stored as
  consent evidence and never authorizes marketing or messaging, which
  require the Phase 8 double opt-in.
- **Linking by email:** a booking links the existing contact with the same
  email without verifying that the booker owns that address. The contact
  is never modified, so nothing is changed or revealed, but the
  appointment appears on that contact's timeline. This is the same
  accepted rule as forms.

### Members

- **Permissions:**
  - `leadstack.appointments:read` (member): list appointments by range.
  - `leadstack.appointments:manage` (member): create manual appointments
    and cancel.
  - `leadstack.booking_types:manage` (admin): booking types and
    availability.
- **Team calendar:** any member with `appointments:manage` can create
  manual appointments for any host in the workspace and cancel any
  appointment. This matches the shared-CRM model, where members edit any
  contact. Restricting it to the actor's own calendar is a later option.
- **Authorization at booking time:** the public page and public booking
  both answer 404 when the host is no longer an active member or the
  workspace is inactive.
- **Audit:**
  - booking and cancellation events are recorded on the contact timeline;
  - booking-type creation, status changes and deletion are audited.
- **Listing:** listing appointments returns at most 1,000. A range with
  more is refused (`range_too_large`) rather than silently truncated.

## Not decided here / limitations

- Reminder emails and SMS are blocked until D6 (delayed jobs) is decided.
- Booking confirmation and cancellation emails would reuse Phase 8
  messaging as transactional messages. They are deferred to the phase
  after D6, so that confirmation and reminder templates are designed
  together.
- Public self-service rescheduling and cancellation links are not built.
- External calendar sync is not built: it needs OAuth provider
  applications and is an external dependency.
- Resource and room scheduling, team round-robin and payments for
  bookings are not built.

## Security implications

- Public keys are unguessable.
- Public responses never reveal other bookings, only which slots are free.
- Free-slot listing is rate limited, because it reveals the host's busy
  pattern; this is documented as inherent to public scheduling.
- Tenant isolation uses forced RLS on all new tables, with composite
  same-tenant foreign keys.
