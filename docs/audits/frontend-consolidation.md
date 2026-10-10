# Frontend consolidation report (screens for Phases 4 and 6–9)

Date: 2026-10-10

## 1. Verdict

**PASS WITH LIMITATIONS.**

The API-only capabilities from Phases 4 and 6–9 now have member or public
screens in French, Arabic (RTL) and English. All frontend checks pass.

**Main limitation:** no screen was exercised in a browser against a running
backend with a real login. There is no end-to-end browser test yet (hosted
ZITADEL login is required, decision D8), and no accessibility audit with
assistive technology.

## 2. Screens added

| Route | Purpose | Backend phase |
|---|---|---|
| `/w/[tenantId]/contacts/[contactId]` | Contact detail, tags, activity timeline, notes, tasks, email consent, send email, record an unsubscribe | 4, 8 |
| `/w/[tenantId]/forms` | List, create (preset fields), enable/disable and delete lead forms; public link | 6 |
| `/w/[tenantId]/automations` | List, create (one trigger, one action), pause/resume and delete workflows | 7 |
| `/w/[tenantId]/calendar` | Upcoming appointments, cancel; booking pages: list, create, enable/disable; public link | 9 |
| `/b/[publicKey]` | Public booking page | 9 |
| `/c/[token]`, `/u/[token]` | Email confirmation and unsubscribe (click to act); built in Phase 8 | 8 |

The contacts list now links to the contact detail screen, and the workspace
tabs link to forms, automations and appointments.

## 3. Design rules applied

- **Destructive actions** (delete form, delete automation, cancel
  appointment, record an unsubscribe) use an inline two-step confirmation.
  No browser dialogs.
- **No raw internal codes.** Audit actions, triggers and statuses are
  always translated; an unknown audit action shows a neutral label.
- **Unsubscribed contacts.** The send-email form and the unsubscribe
  control are hidden once a contact has unsubscribed. The backend enforces
  the rule regardless.
- **Times.**
  - The public booking page shows the host's time zone, never the
    visitor's.
  - Appointments are shown in their own time zone, which is named.
  - Task due dates entered as local time are sent as explicit instants.
- **RTL and mixed text.** Layout uses only logical CSS properties (tested).
  Emails, phone numbers, links, times and time zone names are isolated
  left-to-right. Names and free text use `<bdi>`.
- **Public pages** send no credentials and include the honeypot field.
- **Permissions** are enforced by the API. A member without permission
  sees the translated "no permission" error.

## 4. Checks actually run

- `tsc --noEmit`: no errors.
- `eslint . --max-warnings 0`: clean.
- `vitest run`: 35 tests passed. The new tests cover:
  - slot grouping by host time zone, Arabic digits, the repeated hour and
    month ends;
  - timeline labels never showing raw codes;
  - the email form hidden for unsubscribed contacts;
  - unsubscribe requiring confirmation;
  - booking rule building;
  - the required email field on forms;
  - workflow action building;
  - trigger translations in all locales;
  - dictionary key parity and no empty strings (existing tests).
- `next build`: compiled successfully.
- `npm audit --omit=dev`: 0 vulnerabilities.

## 5. Limitations and remaining work

- **No browser run** against a live backend and no Playwright end-to-end
  tests. These need a test identity provider or the hosted ZITADEL login.
- **Simplified builders.**
  - Forms offer a fixed set of fields.
  - Automations offer one action and no conditions.
  - Booking pages offer one daily window, with the signed-in user as host.

  The API supports more than these screens expose.
- **Not built:**
  - settings screens for tags and custom fields;
  - a contact edit form;
  - a submissions list;
  - a workflow run history;
  - manual appointment creation;
  - member and invitation management screens.
- **Arabic and French translations** were written without review by a
  native speaker. They must be reviewed before launch (Phase 13).
- **No accessibility audit** beyond semantic markup, labels and keyboard
  operable controls.

## 6. Repository state

Untracked only. No commit, push or deployment. SaaS-OS untouched.
