# Phase 6 audit report: lead-capture forms

Date: 2026-10-09

## 1. Verdict

**PASS WITH LIMITATIONS.**

- Forms, an unauthenticated submission endpoint, spam controls, consent
  records and a hosted form page are implemented and tested.
- An independent security review of the public path found 2 high,
  5 medium and 6 low issues. All were fixed or documented.
- **Production prerequisite:** configure trusted forwarded headers (M4).
  Without it, every visitor shares the proxy's rate limit. See §6.

## 2. Scope completed

**Management** (workspace routes)
- Create, list, enable/disable and delete forms. Managing requires
  `leadstack.forms:manage` (admin); reading requires `leadstack.forms:read`
  (member).
- Field definitions: text, email, phone and textarea, each with an
  optional mapping to a contact field. Every form must collect an email or
  a phone number.
- The redirect URL must be https.
- Limit of 50 forms per workspace (advisory lock). Each form and its
  public key are written in one transaction.
- Submissions are listed with the `contacts:read` permission.

**Public path** (`/v1/public/forms/{public_key}`), see ADR-0005
- Key format is validated first. Unknown keys, disabled forms, suspended
  workspaces and wrong-format keys all return the same 404.
- `PublicBodyGuardMiddleware` runs before any parsing: bodies over 64 KiB
  get a 413, and the per-client limit (20 POSTs per hour, IPv6 bucketed by
  /64, address hashed) also applies to malformed requests.
- HMAC form token bound to the form key **and** the client bucket:
  - at least 2 s of fill time;
  - at most 2 h of age;
  - checked only after the form is known to be active.
- Honeypot field: the request looks successful, but nothing is stored.
- Strict values:
  - only defined fields are accepted;
  - per-type length limits;
  - control characters are rejected;
  - total size is capped below the database constraint;
  - email and phone are normalized using the workspace's country.
- Consent is mandatory, and its text is snapshotted. **Form consent is
  unverified.** Messaging (Phase 8) must require a verified opt-in, because
  anyone can type someone else's address.
- Per-form quota of 500 accepted submissions per hour, counted only after
  every check passes, so junk cannot exhaust it.
- Per-workspace cap on stored submissions: 2,000 per 24 h by default
  (`LEADSTACK_FORM_DAILY_SUBMISSIONS`).
- Existing contacts are matched by `lower(email)`, which uses the index.
  They are linked, never overwritten.
- Data minimization: no IP address or user agent is stored. Audit entries
  record identifiers only, with the SYSTEM actor.
- CORS: `/v1/public/*` only, wildcard origin without credentials. The
  middleware is outermost, so it also covers 413/429 answers.
- The same-origin guard skips `/v1/public/*`, which uses no session.

**Frontend:** hosted form page `/f/{publicKey}`:
- public calls send no credentials;
- the honeypot is hidden from assistive technology;
- only https redirects are followed;
- fr/ar/en translations.

## 3. Files and migrations

- **Migration** `0006_forms`:
  - `forms` and `form_submissions` with forced RLS;
  - `form_keys`, an identifier-only index, as a reviewed exception;
  - composite foreign keys;
  - a 32 KiB data-size CHECK.
- **Code:**
  - `domains/forms/*`
  - `routes/forms.py` (routes, CORS, body guard)
  - `ADR-0005`
- **Frontend:** `components/PublicForm.tsx`, `app/f/[publicKey]/page.tsx`.
- **New configuration:**
  - `LEADSTACK_FORM_TOKEN_SECRET` (required for forms; endpoints answer 503
    without it);
  - `LEADSTACK_FORM_DAILY_SUBMISSIONS` (optional).

## 4. Tests

`test_forms.py` has 23 integration tests:
- the complete flow, with cross-origin headers;
- existing contacts are never overwritten;
- invalid submissions;
- the token's fill time, age, integrity, form binding and client binding;
- the honeypot;
- unknown, disabled and suspended forms are indistinguishable, including a
  disabled form with a bad token;
- the per-client rate limit;
- CORS preflight only on public paths;
- definition validation and admin-only management;
- isolation;
- key revocation on delete;
- oversized and malformed bodies cut off before parsing (413, then 429);
- control characters;
- junk not consuming the per-form quota;
- the daily cap;
- IPv6 /64 bucketing.

Also:
- `test_schema_security.py` now accepts `form_keys` as a reviewed
  exception.
- The purge test covers all CRM and sales tables. The form tables were
  added to the purge participant.

## 5. Independent review and resolution

| ID | Finding | Resolution |
|---|---|---|
| H1 | No body cap; rate limit only after parsing | `PublicBodyGuardMiddleware`: 64 KiB cap and per-client limit before parsing; value length caps |
| H2 | Junk exhausts the per-form bucket; IPv6 rotation | Quota counted only for accepted submissions; IPv6 /64 buckets |
| M1 | Token replay and spam scale | Token bound to the client bucket; per-workspace daily cap. Single-use tokens and CAPTCHA remain optional future adapters |
| M2 | Third-party consent on existing contacts | Form consent defined as unverified; messaging must require verified opt-in (recorded as a Phase 8 requirement) |
| M3 | 500s: size, NUL, missing secret | Size and control-character checks return 422; missing secret returns 503 `unavailable` |
| M4 | Proxy-dependent client identity | Documented production prerequisite (uvicorn `--proxy-headers --forwarded-allow-ips=<edge only>`). To be verified in Phase 14 |
| M5 | Email match could not use the index | `lower(email) =` equality |
| L1 | Disabled form distinguishable by token error | Token checked after the active-form check; tested |
| L2 | Form and key in separate transactions; count race | Single transaction; advisory lock |
| L3 | Contact creator of record | Kept as the form's creator; `source` names the form (documented) |
| L4 | Unvalidated keys in Redis keys | Key format validated first |
| L5 | No secret versioning; clock skew | Documented: rotating the secret invalidates open forms for at most 2 h; nodes must be NTP-synchronized |
| L6 | Rendering and exports of stored values | Requirement recorded: every renderer escapes values (React does), and future CSV exports must neutralize formula prefixes |

## 6. Limitations and prerequisites

- **Proxy configuration (M4):** must be verified in production
  hardening.
- **CAPTCHA:** none. Add an adapter if abuse appears.
- **Embedding:** no embed snippet or third-party embedding guidance yet
  (landing pages, Phase 11). Published pages must use a separate
  registrable domain.
- **Visitor confirmation:** no confirmation email or double opt-in yet.
  Both depend on email delivery (Phase 8).

## 7. Repository state

Untracked only. No commit, push or deployment. SaaS-OS untouched.
