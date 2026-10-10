# Phase 4 audit report: CRM productivity

Date: 2026-10-09

## 1. Verdict

**PASS WITH LIMITATIONS.**

- Tags, custom fields, notes, tasks, the contact timeline and bulk actions
  are implemented and tested through the HTTP stack.
- An independent review found 0 critical, 0 high, 5 medium and 5 low
  issues. All were fixed, or documented where they are a product decision.
- The main limitation: there is no frontend for these features yet (API
  only; see §9).

## 2. Scope completed

**Tags**
- Per-workspace tags, unique case-insensitively, at most 200 per workspace.
  The limit is serialized with an advisory lock.
- Tagging and untagging are idempotent, including under concurrent
  identical requests.
- Contacts can be filtered by tag.
- Deleting a tag removes its links.

**Custom fields**
- Definitions of type text, number, date, boolean and select (at most 50).
- Values are stored in `contacts.custom_fields` and validated against the
  definitions.
- Numbers are stored as exact decimal strings. Floats are refused, and
  excess precision is refused rather than rounded.
- Deleting a definition erases its stored values. Stale or invalid values
  are never shown.

**Notes**
- Only the author edits a note.
- The author, or an admin (`contacts:delete`), deletes it.
- Notes are deleted together with their contact.

**Tasks**
- Due date must be an aware instant. Tasks have an assignee (who must be an
  active member), a status, and `completed_at`.
- Listing: "my tasks", by contact, or by status.
- Only the creator, the assignee or an admin may change a task (product
  decision recorded here).
- Deleting the contact unlinks its tasks rather than deleting them.

**Timeline**
- Notes, tasks and audited contact events, merged newest first.
- Constant-size keyset cursor on (time, id), safe when many items share a
  timestamp.

**Bulk actions**
- Tag, untag or delete up to 500 contacts in one transaction.
- Identifiers from other workspaces are ignored, never acted upon.
- One audit record per affected contact, plus a summary record.

**Permission:** `leadstack.crm_settings:manage` (admin) governs tags and
custom-field definitions. Existing workspaces receive it through
`leadstack-admin sync-roles`.

## 3. Files and migrations

- **Migration** `0004_crm_productivity`:
  - tables: `tags`, `contact_tags`, `notes`, `tasks`,
    `custom_field_definitions`;
  - the `contacts.custom_fields` JSONB column;
  - a `(tenant_id, id)` unique constraint on contacts;
  - forced RLS on every new table;
  - composite same-tenant foreign keys.
  - Downgrading drops these tables **and their data**, including
    custom-field values. Use it only on disposable databases.
- **Code:**
  - `domains/crm/{activity,activity_models,custom_fields}.py`
  - `routes/crm_activity.py`
- **Static check:**
  - `tests/unit/test_tenant_scope_discipline.py` now computes
    connection-opening helpers transitively.
  - A mutation that moves a wrapped helper into an open session is caught.

## 4. Tests

- **Integration:**
  - `test_crm_activity.py`: 10 tests;
  - `test_crm_activity_hardening.py`: 15 tests. They cover:
    - stale-value resurrection;
    - removed select options;
    - precision;
    - per-contact bulk audit;
    - concurrent tagging;
    - task participants;
    - member permissions on tags;
    - foreign timeline and notes;
    - tampered cursors;
    - 15 timeline items sharing one timestamp;
    - database-level rejection of cross-tenant links (as the schema owner,
      with RLS bypassed);
    - purge across every CRM and sales table.
- **Test-quality fix:** audit "no personal data" assertions used to read the
  ORM class's `metadata` attribute instead of `entry_metadata`, so they were
  vacuous. They are now real in every test file.

## 5. Independent review and resolution

| ID | Finding | Resolution |
|---|---|---|
| M1 | Recreated field key resurfaces old values | Values erased on definition delete; reads re-validate against the current definition |
| M2 | Bulk delete untraceable per contact | One audit record per affected contact |
| M3 | Link races return 500 | PK conflicts are idempotent; FK races map to 404; bulk overlaps return 409 `concurrent_change` |
| M4 | Any member may change any task | Restricted to creator, assignee or admin (decision recorded) |
| M5 | Nested-checkout detector not transitive | Fixpoint computation; mutation test |
| L6 | Timeline skip set grows | Constant (time, id) keyset |
| L7 | Silent rounding of numbers | Rejected beyond 28 significant digits or 64 characters |
| L8 | User FKs RESTRICT | `ON DELETE SET NULL` for owner and assignee references (migrations 0003–0005, unreleased) |
| L9 | Limit checks race | Advisory lock per workspace |
| L10 | Helpers without their own authorization | `tags_of` and `visible_custom_fields` now authorize |

## 6. Limitations

- No frontend for notes, tasks, tags or custom fields yet. The API is
  complete; UI work is scheduled with the frontend consolidation (roadmap).
- Duplicate detection and merge are not implemented (deferred CRM backlog).
- The timeline's audit source is filtered in Python. A boundary instant
  shared by more than 50 audit events for one contact could hide events
  past that point. This is unrealistic per contact, and documented.

## 7. Repository state

Untracked only. No commit, push or deployment. SaaS-OS untouched.
