# Phase 7 audit report: workflow automation foundation

Date: 2026-10-09

## 1. Verdict

**PASS WITH LIMITATIONS.**

- Event-triggered workflows with immediate actions are implemented,
  reviewed and tested.
- The independent review found no cross-tenant path. It found 2 high,
  5 medium and 5 low reliability and semantics issues. All were fixed or
  documented.
- Limitations:
  - delays and schedules are blocked on decision D6 (by design);
  - no automation UI yet (API only);
  - no periodic sweeper.

## 2. Scope completed

See ADR-0006. In summary:
- transactional outbox;
- dispatch per workspace with coalescing;
- idempotent runs with a claim token and stale-run takeover;
- execution as the workflow's creator, with authorization re-checked at
  execution time;
- automatic pause on lost authority or broken configuration;
- typed conditions;
- protection of existing contacts from form-triggered changes;
- retention command and recovery command.

API:
- `/v1/workspaces/{id}/workflows`: create, list, pause/resume, delete;
- `/v1/workspaces/{id}/workflows/{id}/runs`.

Permissions: `leadstack.workflows:read` (member) and
`leadstack.workflows:manage` (admin).

## 3. Files and migrations

- **Migration** `0007_automation`:
  - tables: `events`, `workflows`, `workflow_runs` (including
    `claim_token`);
  - forced RLS;
  - unique (workflow, event) constraint.
- **Code:**
  - `domains/automation/*`
  - `routes/automation.py`
  - `jobs.py` (`leadstack_process_events`)
  - `admin.py` (`process-events`, `prune-automation`)
  - event producers in CRM, forms and sales
- **Dependency:** `redis` is now declared directly (it was already
  installed through SaaS-OS).

## 4. Tests

- **Full gate before remediation** (Phases 1–7): ruff, format, pyright,
  import contracts, lock check, 66 unit tests and 178 integration tests,
  all passed. detect-secrets clean.
- **Phase 7 tests:**
  - `test_automation.py`: 15 tests;
  - `test_automation_hardening.py`: 15 tests. They cover:
    - crash mid-run is recorded and not duplicated;
    - a run held by another worker keeps its event pending until stale,
      then is taken over;
    - claim-token fencing;
    - suspension, then reactivation;
    - an existing contact is not mutated through a form;
    - a deny grant on the creator;
    - a deleted tag pauses the workflow;
    - a paused workflow is not claimed;
    - typed and normalized conditions;
    - per-workspace coalescing;
    - pruning.
- **Mutation checks killed:** removing the "pending" guard, and removing
  the suspended-workspace check.

## 5. Independent review and resolution

| ID | Finding | Resolution |
|---|---|---|
| H1 | Crashed run stuck `running`; event marked processed | Exceptions mark the run `failed/internal_error`; events stay pending while any run is held; stale takeover works; claim-token fencing |
| H2 | Suspension auto-paused all workflows | Inaccessible workspaces are skipped entirely |
| M1 | Form submitters could mutate existing contacts | `contact_is_new` attribute; mutating actions skipped for existing contacts |
| M2 | Pause not honoured mid-batch | Claim requires an active workflow (row share-lock) |
| M3 | Unbounded fan-out | Redis lock per workspace, bounded passes |
| M4 | Fragile recovery | Per-workspace error isolation and bounded passes; sweeper deferred (D6) |
| M5 | Public form waited on Redis | Dispatch moved to a background task |
| L1 | Untyped conditions | Validated and normalized |
| L2 | Config errors fail forever | Auto-pause with `configuration_error` |
| L3 | Takeover double-finish | Claim token |
| L4 | No retention | `prune-automation` |
| L5 | Delegation semantics undocumented | Documented in ADR-0006 |

## 6. Limitations and deferred items

- No delayed, scheduled or sending actions (D6, plus Phase 8 consent).
- No periodic sweeper: recovery happens on the next dispatch or through
  `leadstack-admin process-events`.
- The workflow and run UI is pending (frontend consolidation).

## 7. Repository state

Untracked only. No commit, push or deployment. SaaS-OS untouched.
