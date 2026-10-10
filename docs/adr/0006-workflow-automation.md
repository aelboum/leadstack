# ADR-0006: Event-triggered workflow automation (no scheduling yet)

Status: Accepted (Phase 7, 2026-10-09). Delayed and scheduled steps stay
blocked on decision D6.

## Context

Product workflows react to CRM events (a contact created, a form
submitted, a deal changing stage). SaaS-OS provides an at-least-once job
queue (arq) but no delayed or scheduled execution (upstream proposal P3).
CLAUDE.md forbids simulating reliable scheduling with in-memory timers.

## Decision

- **Transactional outbox.** Domain operations call
  `automation.events.emit(session, ...)` in the same transaction as the
  change.
  - Events carry identifiers and non-personal attributes only.
  - Event types: `contact.created`, `form.submitted`, `deal.stage_changed`.
- **Dispatch.** After the request, a background task enqueues
  `leadstack_process_events` for the workspace.
  - It is best effort: if Redis is down, events stay pending.
  - `leadstack-admin process-events` recovers them; so does the next
    dispatch for that workspace.
- **Processing.**
  - One processor per workspace at a time, enforced by a Redis lock;
    concurrent dispatches coalesce.
  - Bounded passes of up to 100 events.
  - Workflows only react to events that happened after the workflow was
    created.
  - A suspended or closed workspace is skipped. Its events stay pending
    and its workflows are not paused.
- **Idempotency.**
  - One run per (workflow, event), enforced by a unique key and claimed in
    its own short transaction.
  - A claim token fences off slow workers after a stale-run takeover
    (10 minutes).
  - An event is marked processed only when every matching workflow has a
    terminal run.
  - Unexpected errors record the run as `failed/internal_error` and never
    leave it `running`.
- **Actions** (allowlist, at most 10 per workflow): `add_tag`,
  `create_task`, `set_owner`.
  - Each runs through the ordinary domain service **as the workflow's
    creator**, so it is authorized at execution time (roles, deny grants,
    delegation, tenant lifecycle).
  - If the creator loses authority, the run fails with `not_authorized`
    and the workflow is paused.
  - Configuration errors (a deleted tag, an assignee who is no longer a
    member) pause the workflow.
- **Conditions.** Typed equality on event attributes, validated and
  normalized when the workflow is saved.
- **No loops.** No action emits an event.
- **Public forms.** When a submission matches an existing contact
  (`contact_is_new = false`), mutating actions (`add_tag`, `set_owner`) are
  skipped. Anonymous visitors cannot change existing CRM records.
- **Retention.** `leadstack-admin prune-automation --days N` deletes
  processed events and finished runs.

## Consequences

- Anyone who can trigger an event (members creating contacts, anonymous
  visitors via forms) causes actions to run with the creator's authority.
  This is intended delegation, bounded by the action allowlist and the
  existing-contact rule.
- `create_task` is at-least-once after a crash and takeover. The other
  actions are idempotent.
- No periodic sweeper exists until scheduling is available (D6). Pending
  events wait for the next dispatch or the admin command.
- **Delays** (send after N days), **schedules** and **sending actions**
  (email, SMS) are out of scope. Sending requires Phase 8 consent rules.

## Security implications

- The job payload's tenant only scopes RLS reads of that workspace's own
  outbox. A forged payload can at most make that workspace's pending
  events run early.
- Execution-time authorization and the claim protocol were reviewed and
  tested, including deny grants, suspension, and crash-and-takeover
  scenarios.
