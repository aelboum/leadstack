"""Phase 8 review regressions: confirmation flooding, link exposure,
withdrawal ownership, address-bound consent, suspended workspaces and
configuration failures. Email goes through SaaS-OS `FakeEmailProvider`."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from core.email import FakeEmailProvider
from infra.db import tenant_session_scope
from infra.jobs import TenantJobPayload

from leadstack.domains.messaging.models import Message
from leadstack.domains.messaging.service import deliver_message
from leadstack.integrations import email as email_adapter
from leadstack.jobs import CONFIRMATIONS_PER_ADDRESS_PER_DAY, leadstack_send_message
from tests.integration.test_crm import _contacts
from tests.integration.test_messaging import _deliver_all, _links, _messages, _submit_form
from tests.integration.test_workspaces import _code, _create, _invite_and_accept

pytestmark = pytest.mark.integration


@pytest.fixture
def outbox():
    provider = FakeEmailProvider()
    email_adapter.install_provider(provider)
    yield provider
    email_adapter.install_provider(None)


@pytest.fixture
def ws(api, make_user):
    owner = make_user()
    tenant = _create(api, owner)["tenant_id"]
    return owner, tenant


def _contact_id(api, owner, tenant) -> str:
    return api.get(_contacts(tenant), headers=owner.headers).json()["items"][0]["contact_id"]


def test_repeated_submissions_queue_one_confirmation(api, ws, outbox) -> None:
    owner, tenant = ws
    for _ in range(4):
        _submit_form(api, owner, tenant, "victim@example.ma")

    assert [k for _, k, _ in _messages(tenant)] == ["confirmation"]


def test_per_address_limit_across_workspaces(api, make_user, outbox) -> None:
    address = f"v{uuid.uuid4().hex[:8]}@example.ma"
    statuses = []
    for _ in range(CONFIRMATIONS_PER_ADDRESS_PER_DAY + 1):
        owner = make_user()
        tenant = _create(api, owner)["tenant_id"]
        _submit_form(api, owner, tenant, address)
        ((message_id, _, _),) = _messages(tenant)
        payload = TenantJobPayload(tenant_id=tenant, data={"message_id": str(message_id)})
        statuses.append(asyncio.run(leadstack_send_message(payload))["status"])

    assert statuses == ["sent"] * CONFIRMATIONS_PER_ADDRESS_PER_DAY + ["suppressed"]
    assert len(outbox.sent) == CONFIRMATIONS_PER_ADDRESS_PER_DAY


def test_stored_and_listed_bodies_never_contain_links(api, ws, outbox) -> None:
    owner, tenant = ws
    _submit_form(api, owner, tenant, "l@example.ma")
    _deliver_all(tenant)
    contact_id = _contact_id(api, owner, tenant)

    listed = api.get(f"{_contacts(tenant)}/{contact_id}/messages", headers=owner.headers).json()

    assert set(_links(outbox.sent[0])) == {"c", "u"}  # the contact received working links
    for item in listed:
        assert "/c/" not in item["body"] and "/u/" not in item["body"]
        assert "[confirmation link]" in item["body"]


def test_member_cannot_override_an_unsubscribe_and_cannot_grant(api, ws, make_user, outbox) -> None:
    owner, tenant = ws
    member = make_user()
    _invite_and_accept(api, owner, tenant, member, "member")
    _submit_form(api, owner, tenant, "o@example.ma")
    _deliver_all(tenant)
    contact_id = _contact_id(api, owner, tenant)
    url = f"{_contacts(tenant)}/{contact_id}/consent"

    member_grant = api.put(url, json={"status": "granted"}, headers=member.headers)
    api.post("/v1/public/consent/unsubscribe", json={"token": _links(outbox.sent[0])["u"]})
    owner_grant = api.put(url, json={"status": "granted"}, headers=owner.headers)

    assert member_grant.status_code == 403
    assert (owner_grant.status_code, _code(owner_grant)) == (409, "withdrawn_by_contact")
    assert api.get(url, headers=owner.headers).json()["withdrawn_via"] == "link"


def test_member_withdrawal_can_be_reversed_by_an_admin(api, ws) -> None:
    owner, tenant = ws
    contact = api.post(
        _contacts(tenant), json={"email": "r@example.ma"}, headers=owner.headers
    ).json()
    url = f"{_contacts(tenant)}/{contact['contact_id']}/consent"

    api.put(url, json={"status": "withdrawn"}, headers=owner.headers)
    regranted = api.put(url, json={"status": "granted"}, headers=owner.headers)

    assert regranted.status_code == 200 and regranted.json()["status"] == "granted"


def test_address_change_resets_consent_and_voids_old_links(api, ws, outbox) -> None:
    owner, tenant = ws
    _submit_form(api, owner, tenant, "before@example.ma")
    _deliver_all(tenant)
    tokens = _links(outbox.sent[0])
    contact_id = _contact_id(api, owner, tenant)

    api.patch(
        f"{_contacts(tenant)}/{contact_id}",
        json={"email": "after@example.ma"},
        headers=owner.headers,
    )
    consent = api.get(f"{_contacts(tenant)}/{contact_id}/consent", headers=owner.headers)
    old_confirm = api.post("/v1/public/consent/confirm", json={"token": tokens["c"]})

    assert consent.status_code == 200 and consent.json() is None
    assert (old_confirm.status_code, _code(old_confirm)) == (400, "invalid_link")


def test_same_address_case_change_keeps_consent(api, ws) -> None:
    owner, tenant = ws
    contact = api.post(
        _contacts(tenant), json={"email": "k@example.ma"}, headers=owner.headers
    ).json()
    url = f"{_contacts(tenant)}/{contact['contact_id']}"
    api.put(f"{url}/consent", json={"status": "granted"}, headers=owner.headers)

    api.patch(url, json={"first_name": "Karim"}, headers=owner.headers)

    assert api.get(f"{url}/consent", headers=owner.headers).json()["status"] == "granted"


def test_unsubscribe_works_for_a_suspended_workspace(api, make_user, outbox) -> None:
    owner = make_user()
    agency = _create(api, owner)["tenant_id"]
    client = api.post(
        f"/v1/workspaces/{agency}/clients", json={"display_name": "C"}, headers=owner.headers
    ).json()["tenant_id"]
    _submit_form(api, owner, client, "s@example.ma")
    _deliver_all(client)
    tokens = _links(outbox.sent[0])
    api.post(
        f"/v1/workspaces/{agency}/clients/{client}/status",
        json={"status": "suspended"},
        headers=owner.headers,
    )

    unsubscribed = api.post("/v1/public/consent/unsubscribe", json={"token": tokens["u"]})
    confirmed = api.post("/v1/public/consent/confirm", json={"token": tokens["c"]})

    assert unsubscribed.status_code == 204
    assert confirmed.status_code == 400


def test_missing_sender_fails_message_instead_of_stranding_it(api, ws, monkeypatch) -> None:
    owner, tenant = ws
    contact = api.post(
        _contacts(tenant), json={"email": "n@example.ma"}, headers=owner.headers
    ).json()
    queued = api.post(
        f"{_contacts(tenant)}/{contact['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    ).json()
    monkeypatch.delenv("EMAIL_DEFAULT_SENDER")
    from core.email import get_email_config

    get_email_config.cache_clear()

    assert deliver_message(uuid.UUID(tenant), uuid.UUID(queued["message_id"])) == "failed"
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        row = session.get(Message, uuid.UUID(queued["message_id"]))
        assert row is not None and row.error_code == "email_not_configured"


def test_consent_actions_do_not_use_the_form_bucket(api, ws, outbox, monkeypatch) -> None:
    """Unsubscribing behind a busy NAT must not be blocked by form traffic."""
    import leadstack.routes.forms as forms_routes

    monkeypatch.setattr(forms_routes, "SUBMISSIONS_PER_CLIENT_PER_HOUR", 1)
    owner, tenant = ws
    _submit_form(api, owner, tenant, "nat@example.ma")  # uses the one form slot
    _deliver_all(tenant)

    response = api.post(
        "/v1/public/consent/unsubscribe", json={"token": _links(outbox.sent[0])["u"]}
    )

    assert response.status_code == 204


def test_link_is_bound_to_the_consented_address(api, ws, outbox) -> None:
    """Defence in depth: even if the consent row survived an address change
    (e.g. a future write path forgets to reset it), an old link no longer
    matches the address the record now covers."""
    from infra.db import select

    from leadstack.domains.messaging.models import ContactConsent
    from leadstack.domains.messaging.service import address_hash

    owner, tenant = ws
    _submit_form(api, owner, tenant, "bound@example.ma")
    _deliver_all(tenant)
    tokens = _links(outbox.sent[0])
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        row = session.execute(select(ContactConsent)).scalar_one()
        row.address_hash = address_hash("someone-else@example.ma")

    response = api.post("/v1/public/consent/confirm", json={"token": tokens["c"]})

    assert (response.status_code, _code(response)) == (400, "invalid_link")


def _unsubscribe_via_link(api, owner, tenant, outbox, address: str) -> str:
    _submit_form(api, owner, tenant, address)
    _deliver_all(tenant)
    response = api.post(
        "/v1/public/consent/unsubscribe", json={"token": _links(outbox.sent[-1])["u"]}
    )
    assert response.status_code == 204
    return _contact_id(api, owner, tenant)


def test_repeat_withdrawal_cannot_launder_a_contact_unsubscribe(api, ws, outbox) -> None:
    owner, tenant = ws
    contact_id = _unsubscribe_via_link(api, owner, tenant, outbox, "launder@example.ma")
    url = f"{_contacts(tenant)}/{contact_id}/consent"

    again = api.put(url, json={"status": "withdrawn"}, headers=owner.headers)
    grant = api.put(url, json={"status": "granted"}, headers=owner.headers)

    assert again.status_code == 200 and again.json()["withdrawn_via"] == "link"
    assert (grant.status_code, _code(grant)) == (409, "withdrawn_by_contact")


def test_address_round_trip_does_not_erase_an_unsubscribe(api, ws, outbox) -> None:
    owner, tenant = ws
    contact_id = _unsubscribe_via_link(api, owner, tenant, outbox, "round@example.ma")
    contact_url = f"{_contacts(tenant)}/{contact_id}"
    api.patch(contact_url, json={"email": "other@example.ma"}, headers=owner.headers)
    api.patch(contact_url, json={"email": "round@example.ma"}, headers=owner.headers)

    send = api.post(
        f"{contact_url}/messages", json={"subject": "s", "body": "b"}, headers=owner.headers
    )
    grant = api.put(f"{contact_url}/consent", json={"status": "granted"}, headers=owner.headers)
    before = len(_messages(tenant))
    _submit_form(api, owner, tenant, "round@example.ma")

    assert (send.status_code, _code(send)) == (409, "consent_withdrawn")
    assert (grant.status_code, _code(grant)) == (409, "withdrawn_by_contact")
    assert len(_messages(tenant)) == before  # no new confirmation


def test_unsubscribe_survives_contact_deletion_and_duplicates(api, ws, outbox) -> None:
    owner, tenant = ws
    contact_id = _unsubscribe_via_link(api, owner, tenant, outbox, "dup@example.ma")
    api.delete(f"{_contacts(tenant)}/{contact_id}", headers=owner.headers)
    recreated = api.post(
        _contacts(tenant), json={"email": "DUP@example.ma"}, headers=owner.headers
    ).json()

    send = api.post(
        f"{_contacts(tenant)}/{recreated['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    )

    assert (send.status_code, _code(send)) == (409, "consent_withdrawn")


def test_old_unsubscribe_link_still_works_after_a_reset(api, ws, outbox) -> None:
    owner, tenant = ws
    _submit_form(api, owner, tenant, "keep@example.ma")
    _deliver_all(tenant)
    old = _links(outbox.sent[0])["u"]
    contact_id = _contact_id(api, owner, tenant)
    contact_url = f"{_contacts(tenant)}/{contact_id}"
    api.patch(contact_url, json={"email": "elsewhere@example.ma"}, headers=owner.headers)
    api.patch(contact_url, json={"email": "keep@example.ma"}, headers=owner.headers)

    response = api.post("/v1/public/consent/unsubscribe", json={"token": old})
    send = api.post(
        f"{contact_url}/messages", json={"subject": "s", "body": "b"}, headers=owner.headers
    )

    assert response.status_code == 204
    assert (send.status_code, _code(send)) == (409, "consent_withdrawn")


def test_unsubscribe_between_queue_and_delivery_suppresses(api, ws, outbox) -> None:
    owner, tenant = ws
    _submit_form(api, owner, tenant, "race@example.ma")
    _deliver_all(tenant)
    token = _links(outbox.sent[0])["u"]
    contact_id = _contact_id(api, owner, tenant)
    queued = api.post(
        f"{_contacts(tenant)}/{contact_id}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    ).json()

    api.post("/v1/public/consent/unsubscribe", json={"token": token})

    assert deliver_message(uuid.UUID(tenant), uuid.UUID(queued["message_id"])) == "suppressed"
    assert len(outbox.sent) == 1


def test_retries_do_not_consume_the_per_address_quota(api, ws, make_user) -> None:
    from core.email import EmailProviderError

    owner, tenant = ws
    address = f"q{uuid.uuid4().hex[:8]}@example.ma"
    _submit_form(api, owner, tenant, address)
    ((message_id, _, _),) = _messages(tenant)
    payload = TenantJobPayload(tenant_id=tenant, data={"message_id": str(message_id)})
    email_adapter.install_provider(FakeEmailProvider(fail=True))
    try:
        for _ in range(2):
            with pytest.raises(EmailProviderError):
                asyncio.run(leadstack_send_message(payload))
    finally:
        email_adapter.install_provider(None)
    working = FakeEmailProvider()
    email_adapter.install_provider(working)
    try:
        assert asyncio.run(leadstack_send_message(payload))["status"] == "sent"
        # Three attempts charged the address once: two more workspaces can
        # still send it a confirmation today.
        for _ in range(CONFIRMATIONS_PER_ADDRESS_PER_DAY - 1):
            other_owner = make_user()
            other = _create(api, other_owner)["tenant_id"]
            _submit_form(api, other_owner, other, address)
            ((other_id, _, _),) = _messages(other)
            other_payload = TenantJobPayload(tenant_id=other, data={"message_id": str(other_id)})
            assert asyncio.run(leadstack_send_message(other_payload))["status"] == "sent"
    finally:
        email_adapter.install_provider(None)


def test_workspace_name_cannot_break_the_subject_header(api, ws, outbox, monkeypatch) -> None:
    import leadstack.domains.messaging.service as messaging_service

    monkeypatch.setattr(
        messaging_service, "workspace_display_name", lambda _t: "Agence\r\nBcc: x@evil.example"
    )
    owner, tenant = ws
    _submit_form(api, owner, tenant, "hdr@example.ma")
    _deliver_all(tenant)

    (sent,) = outbox.sent
    assert "\r" not in sent.subject and "\n" not in sent.subject
    assert sent.subject.startswith("Merci de confirmer - Agence Bcc:")  # French workspace


def test_admin_grant_keeps_suppression_while_a_duplicate_is_withdrawn(api, ws) -> None:
    owner, tenant = ws
    first = api.post(
        _contacts(tenant), json={"email": "twin@example.ma"}, headers=owner.headers
    ).json()
    second = api.post(
        _contacts(tenant), json={"email": "twin@example.ma"}, headers=owner.headers
    ).json()
    api.put(
        f"{_contacts(tenant)}/{first['contact_id']}/consent",
        json={"status": "withdrawn"},
        headers=owner.headers,
    )

    granted = api.put(
        f"{_contacts(tenant)}/{second['contact_id']}/consent",
        json={"status": "granted"},
        headers=owner.headers,
    )
    send_first = api.post(
        f"{_contacts(tenant)}/{first['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    )
    send_second = api.post(
        f"{_contacts(tenant)}/{second['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    )

    assert granted.status_code == 200
    assert (send_first.status_code, _code(send_first)) == (409, "consent_withdrawn")
    # The address-level opt-out stays while the first contact is withdrawn.
    assert (send_second.status_code, _code(send_second)) == (409, "consent_withdrawn")


def test_grant_refused_from_consent_record_alone(api, ws, outbox) -> None:
    """Defence in depth: even if the suppression row were lost, the consent
    record's own `withdrawn_via=link` still blocks an admin grant."""
    from infra.db import delete

    from leadstack.domains.messaging.models import EmailSuppression

    owner, tenant = ws
    contact_id = _unsubscribe_via_link(api, owner, tenant, outbox, "depth@example.ma")
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        session.execute(delete(EmailSuppression))

    grant = api.put(
        f"{_contacts(tenant)}/{contact_id}/consent",
        json={"status": "granted"},
        headers=owner.headers,
    )

    assert (grant.status_code, _code(grant)) == (409, "withdrawn_by_contact")


@pytest.mark.parametrize(
    ("locale", "subject", "footer"),
    [
        ("fr", "Merci de confirmer - ", "Se désabonner :"),
        ("ar", "يرجى التأكيد - ", "لإلغاء الاشتراك:"),
        ("en", "Please confirm - ", "Unsubscribe:"),
    ],
)
def test_emails_use_the_workspace_language(api, make_user, outbox, locale, subject, footer) -> None:
    owner = make_user()
    tenant = _create(api, owner, locale=locale, display_name="Atlas")["tenant_id"]
    _submit_form(api, owner, tenant, f"{locale}@example.ma")
    contact_id = _contact_id(api, owner, tenant)
    api.post(
        f"{_contacts(tenant)}/{contact_id}/messages",
        json={"subject": "Devis", "body": "Bonjour"},
        headers=owner.headers,
    )
    _deliver_all(tenant)

    confirmation, one_to_one = outbox.sent
    assert confirmation.subject == f"{subject}Atlas"
    assert set(_links(confirmation)) == {"c", "u"}  # links survive every language
    assert footer in one_to_one.text_body and "u" in _links(one_to_one)


def test_sender_who_lost_access_no_longer_sends(api, ws, make_user, outbox) -> None:
    """Authorization is re-checked at delivery, not only when queuing."""
    from tests.integration.test_workspaces import _member_id

    owner, tenant = ws
    member = make_user()
    _invite_and_accept(api, owner, tenant, member, "member")
    contact = api.post(
        _contacts(tenant), json={"email": "late@example.ma"}, headers=owner.headers
    ).json()
    queued = api.post(
        f"{_contacts(tenant)}/{contact['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=member.headers,
    ).json()
    api.post(
        f"/v1/workspaces/{tenant}/members/{_member_id(api, owner, tenant, member)}/status",
        json={"action": "suspend"},
        headers=owner.headers,
    )

    status = deliver_message(uuid.UUID(tenant), uuid.UUID(queued["message_id"]))

    assert status == "suppressed" and outbox.sent == []
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        row = session.get(Message, uuid.UUID(queued["message_id"]))
        assert row is not None and row.error_code == "sender_not_authorized"
