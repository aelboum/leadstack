# Phase 13 groundwork report: internationalization and Moroccan market readiness

Date: 2026-10-10

## 1. Verdict

**BLOCKED** for Phase 13 as a whole; **PASS WITH LIMITATIONS** for the
engineering groundwork.

Launch readiness for Morocco cannot be declared from engineering work
alone. It depends on legal and regulatory verification, native-speaker
review of the French and Arabic text, and provider decisions. None of
these can be done or decided inside this repository. This report separates
what is implemented and tested from what remains.

## 2. Implemented and covered by tests

| Requirement (CLAUDE.md §5) | State | Evidence |
|---|---|---|
| French, Arabic and English interfaces from translation keys | Done | Every key exists in all three dictionaries with no empty strings (`i18n.test.ts`); new screens added with full parity |
| Arabic right-to-left layout, structural not cosmetic | Done | `dir` on `<html>`; a test asserts no stylesheet or component uses physical left/right |
| Mixed Arabic and Latin text, phone numbers, emails, amounts, times | Done | Left-to-right isolation component and `<bdi>`, with tests; Latin digits in Arabic for dates, times and amounts |
| Locale is never inferred from IP address or country | Done | Explicit choice, then browser preference, then a configurable default (tested) |
| Emails written by LeadStack use the workspace's language | **Done in this phase** | Confirmation email and unsubscribe footer in fr, ar and en; unit and integration tests in all three languages |
| Moroccan phone numbers, national and international format | Done | E.164 normalization with the workspace country as default region; national-format search (CRM tests) |
| Phone numbers without a workspace country | Done | International format required; tested |
| Moroccan dirham among supported currencies; no MAD assumption | Done | Integer minor units with an explicit ISO 4217 code; per-currency precision (0, 2 or 3 digits); totals per currency (money tests) |
| Morocco's time zone, including the Ramadan offset change | Done | Slot engine tests pin UTC+1 and UTC+0 around Ramadan 2026 from the pinned tz database; the app never uses a fixed offset |
| Daylight-saving transitions | Done | Skipped and repeated hours (Europe/Paris) tested in the engine, the API and the frontend |
| Time zone data independent of the host | Done | `tzdata==2026.5` pinned and used on every platform |
| Country-specific defaults in configuration, not in shared models | Done | Each workspace carries its own locale, time zone, currency and country |

## 3. Gaps found by this audit

| # | Gap | Impact | Action |
|---|---|---|---|
| G1 | French and Arabic texts (interface and emails) were written without a native-speaker review | Wording, register and Moroccan usage may be wrong | Review before launch; not an engineering task |
| G2 | Booking splits the visitor's full name at the first space into first and last name | Wrong for many names, including Arabic multi-part names; contradicts "no assumption about name order" | Store a single display name on contacts, or ask for given and family name separately (needs a small model decision) |
| G3 | API error `message` strings are English only | None in the product UI, which translates by error code; visible to integrators and in unknown-code fallbacks | Acceptable; document for the future public API |
| G4 | One email language per workspace; a contact has no preferred language | A French-configured workspace cannot write its own consent email to an Arabic-speaking contact in Arabic | Add an optional contact language when bulk messaging is built |
| G5 | No Darija support | Darija matters for AI conversations (CLAUDE.md §5.2); no AI feature exists (D5) | Evaluate separately in Phase 10; no claim is made |
| G6 | No address model | Invoices and quotes will need Moroccan and international address formats | Design with the invoicing module; do not assume one national format |
| G7 | The calendar week starts on Monday in the booking-page builder | Correct for Morocco and Europe; other markets differ | Make it locale-aware when a full calendar view is built |
| G8 | Date and time pickers are the browser's native controls | Their language follows the browser, not the chosen interface language | Acceptable for now; revisit with a design system decision |

## 4. Not verifiable in this repository (blockers)

These require authoritative sources, professional advice or accounts. No
compliance claim is made anywhere in the product or its documentation.

- **Data protection (Morocco, law 09-08, CNDP).** Open questions:
  - notification or authorization of processing;
  - cross-border transfer rules if hosting is outside Morocco;
  - consent requirements for electronic marketing;
  - retention periods;
  - data subject rights procedures.

  Engineering support exists (consent records, unsubscribe, suppression
  list, contact erasure, audit log). Whether it is sufficient is a legal
  question.
- **Other jurisdictions** where customers or their contacts are located,
  for example GDPR for contacts in the European Union.
- **Hosting location and data residency** (linked to D14 and the hosting
  choice).
- **Electronic communications rules** for SMS and WhatsApp, sender
  registration, and WhatsApp Business template approval (D13).
- **Payments, invoicing and tax** (D7): which providers operate for
  Moroccan merchants, invoice legal mentions, VAT handling.
- **Retention choices** made in earlier phases that need confirmation:
  - the hashed address kept in the suppression list after contact erasure
    (ADR-0007);
  - message bodies kept until contact erasure;
  - audit log retention.
- **Terms of service, privacy notice and data processing agreement** for
  LeadStack's own customers.
- **SaaS-OS licence terms** for a commercial product (D11).

## 5. Checks actually run for the work done in this phase

- ruff, format, pyright (0 errors) and import contracts (4 kept): passed.
- Unit tests: 97 passed, including the new email-text tests.
- Messaging integration tests: 36 passed, including emails in the
  workspace's language for fr, ar and en.
- The full gate was rerun after this change; the result is in section 8.

## 6. Security and privacy

- **Email language** comes from the workspace's configuration only. It is
  never taken from request data or from the recipient.
- **Header safety.** Workspace names are still collapsed to one line
  before they reach an email subject (tested in French).
- **No new personal data** is stored.

## 7. Repository state

Untracked only. No commit, push or deployment. SaaS-OS untouched.

## 8. Full gate after the email change

Run on 2026-10-10 with the localized emails in place. Every check passed:

- ruff: all checks passed; format: 138 files already formatted;
- pyright: 0 errors;
- import contracts: 4 kept, 0 broken;
- `uv lock --check`: consistent;
- 97 unit tests and 253 integration tests passed;
- detect-secrets: clean (exit 0).
