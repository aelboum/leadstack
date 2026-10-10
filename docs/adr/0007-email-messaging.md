# ADR-0007: Email consent, double opt-in and one-to-one email

Status: Accepted (Phase 8, 2026-10-09). SMS, WhatsApp and voice are
blocked on a provider decision (D13).

## Context

The product must contact leads by email while respecting consent and
opt-out. SaaS-OS provides provider-agnostic transactional email
(`core.email`: `send_email`, an SMTP provider and `FakeEmailProvider`). It
explicitly excludes marketing email, templates and unsubscribe management.
Public forms can be filled in with anyone's address (Phase 6 review M2).

## Decision

### Consent model

Consent is recorded per (contact, channel=email) and is **bound to the
address it covers**, stored as the SHA-256 of the normalized address.
Changing a contact's email address deletes the consent record in the same
transaction; consent for the old address never carries over.

- `pending`: requested, for example through a form, which anyone can fill
  in. Not enough for bulk or marketing email.
- `granted`: confirmed by the address owner through a signed link, or
  recorded as a directly obtained grant by an admin
  (`leadstack.consent:manage`, audited).
- `withdrawn`: unsubscribed. `withdrawn_via` records who withdrew:
  - `link`: the contact unsubscribed. This is **permanent for that address
    in the workspace**. No member, admin or owner can reverse it (409
    `withdrawn_by_contact`), no confirmation email is sent to it again, and
    an old confirmation link cannot undo it. A contact-initiated
    re-subscription flow is not built.
  - `member`: a member recorded a withdrawal received another way. An
    admin may later record a new grant, which lifts that member-recorded
    opt-out.
  - Recording a withdrawal again is a no-op and never changes who withdrew.

### Suppression list

Opt-outs are also stored in `email_suppressions`, keyed by
(workspace, channel, address hash) and **not** by contact. It survives:

- address edits, including changing an address and changing it back;
- contact deletion and re-creation;
- duplicate contacts with the same address.

One-to-one sending, double opt-in, manual grants and delivery all check it.
An unsubscribe link is honoured even after the consent record was reset or
the contact deleted: the opt-out is stored for the address the link was
sent to.

A member-recorded withdrawal applies to every contact in the workspace with
that address.

On contact erasure, the suppression keeps only the SHA-256 hash of the
address, so the opt-out can still be honoured. That retained hash is
pseudonymous personal data. Keeping it is a deliberate choice that must be
confirmed in the Phase 13 privacy review.

Admin grants and member withdrawals:

- An admin grant on one contact lifts a member-recorded suppression only
  when no other contact with the same address is still withdrawn.
- An admin grant is refused when the contact's own consent record, or the
  suppression list, shows a link unsubscribe.

**Locking.** Every consent and suppression write, the delivery-time consent
check, and contact email changes take the same per-workspace advisory lock
(`CONSENT_LOCK`).

- **Lock order:** this lock first, then row locks. The CRM takes it before
  locking the contact row. The opposite order could deadlock.
- **Effect:** an unsubscribe is either fully before or fully after a send
  decision, and concurrent first requests cannot collide.
- **Remaining window:** an email that has already moved to `sending` when
  the contact unsubscribes may still be delivered.

### Double opt-in

A public form lead with an email address gets a pending consent and a
confirmation email that names the workspace. Abuse limits:

- one confirmation per consent record per 24 hours, and at most 3 in
  total;
- at most 3 confirmation emails per address per day across **all**
  workspaces, enforced in the send job and keyed by the address hash. Each
  message counts once, on its first attempt, not once per retry;
- nothing is ever sent to a withdrawn address because of a later form
  submission.

### Signed links

- **Format:** HMAC-SHA256 over a JSON payload containing purpose,
  workspace, contact, consent record and address hash. The key is
  `LEADSTACK_LINK_SIGNING_SECRET`.
- **Expiry:** confirmation links expire after 7 days. Unsubscribe links do
  not expire, so an old email can always be used to unsubscribe.
- **Signed at delivery, never stored.** Stored and listed message bodies
  contain placeholders only, so no member can read and use a link meant
  for the contact.
- **Scanner-safe:** confirm and unsubscribe are **POST** endpoints,
  triggered by a click on the app's `/c/{token}` and `/u/{token}` pages,
  so mail scanners that prefetch links cannot act on them.
- **Suspended workspaces:** unsubscribing always works, even when the
  workspace is suspended. Confirming does not.

### One-to-one email

- Members with `leadstack.messages:send` can write to a contact that has
  an address, unless that contact has withdrawn consent. This is treated
  as business correspondence, not marketing, so `pending` and absent
  consent are allowed.
- Limits: 60 per member per hour and 200 per workspace per hour.
- Subjects cannot contain line breaks; SaaS-OS also validates headers and
  addresses.

### Delivery

- **At most once.** An asynchronous job moves the message from `queued` to
  `sending` under a row lock before the provider call. A message found in
  `sending` is never re-sent; it becomes `failed/outcome_unknown`.
- **Retries:** provider errors are retried up to 3 attempts, then the
  message is `failed`. Configuration errors (no sender, SMTP settings,
  link secret or origin) mark it `failed/email_not_configured` straight
  away.
- **Checks at send time:** workspace status, consent, the current address
  and the address the consent is bound to. A failed check means
  `suppressed` with a reason code.
- **Recovery:** messages whose enqueue failed are recovered by
  `leadstack-admin send-queued` (run manually or by an operator schedule;
  automatic scheduling waits for D6).

### Provider and privacy

- **Provider:** SaaS-OS `send_email()` with its default SMTP provider. The
  sender is `EMAIL_DEFAULT_SENDER`. `PUBLIC_APP_ORIGIN` must be `https://`
  outside development and test.
- **Test hook:** `leadstack.integrations.email.install_provider()` exists
  for tests only. No fake path runs in production.
- **Privacy:** the message log keeps the address used and the content
  sent (without links), so the CRM shows what was sent. Deleting a contact
  erases its messages and consent records.

## Not decided here / limitations

- **Not built:** bulk or marketing campaigns, templates, per-workspace
  sender domains (DKIM/SPF) and per-workspace `Reply-To`. Bulk sending must
  require `granted` consent.
- **No `List-Unsubscribe` headers:** SaaS-OS `EmailMessage` exposes no
  custom headers (upstream proposal P11).
- **Address hash is unsalted SHA-256.** It is retained in
  `email_suppressions` after erasure, so a known address can be matched
  against it. A keyed HMAC would be stronger; this is deferred to the
  Phase 13 privacy review.
- **Per-address confirmation quota is approximate.** It can be charged
  twice when `send-queued` re-enqueues a message whose first job is still
  pending, and it is charged for confirmations that are later suppressed.
  It errs on the side of sending fewer emails.
- **No retention period** for message bodies beyond contact erasure.
  Retention is a product and legal decision for Phase 13.
- **No compliance claim:** whether these rules satisfy Moroccan law
  09-08 / CNDP requirements, or any other jurisdiction's, has **not** been
  verified.

## Alternatives considered

- **GET links that act immediately:** rejected, because scanners prefetch
  links.
- **Storing signed links in the message body:** rejected, because any
  reader could use them (Phase 8 review H2).
- **Consent per contact only:** rejected, because consent would carry over
  to a new address (Phase 8 review H4), and because an opt-out could be
  erased by editing or re-creating the contact (Phase 8 re-review H2).

## Security implications

- **Token binding:** tokens are bound to purpose, workspace, contact,
  consent record and address hash, and are compared in constant time.
- **Fail closed:** a missing secret or origin returns 503 at request time
  and marks the message failed at delivery time.
- **Public endpoints:** they use the public body guard (64 KiB cap) with a
  separate per-client bucket for consent actions (120/h), so heavy form
  traffic from a shared address cannot block unsubscribing. CORS is
  without credentials.
