"""Phase 8: email consent (double opt-in), unsubscribe and one-to-one email.

Email goes through SaaS-OS `send_email()` with its own `FakeEmailProvider`
installed via `leadstack.integrations.email` -- nothing is sent to a real
mail server, and these tests make no claim about real delivery."""

from __future__ import annotations

import re
import time
import uuid

import pytest
from core.email import FakeEmailProvider
from infra.db import select, tenant_session_scope

from leadstack.domains.forms.service import issue_token
from leadstack.domains.messaging import links
from leadstack.domains.messaging.models import ContactConsent, Message
from leadstack.domains.messaging.service import deliver_message
from leadstack.integrations import email as email_adapter
from leadstack.routes.forms import client_bucket
from tests.integration.test_crm import _contacts
from tests.integration.test_workspaces import _code, _create

pytestmark = pytest.mark.integration

LINK = re.compile(r"http://testserver/([cu])/(\S+)")


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


def _messages(tenant: str) -> list[tuple[uuid.UUID, str, str]]:
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        rows = session.execute(select(Message).order_by(Message.created_at)).scalars()
        return [(m.id, m.kind, m.status) for m in rows]


def _deliver_all(tenant: str) -> None:
    for message_id, _, status in _messages(tenant):
        if status == "queued":
            deliver_message(uuid.UUID(tenant), message_id)


def _submit_form(api, owner, tenant, email: str) -> None:
    form = api.post(
        f"/v1/workspaces/{tenant}/forms",
        json={
            "name": f"F{uuid.uuid4().hex[:6]}",
            "consent_text": "ok",
            "fields": [
                {
                    "key": "email",
                    "label": "E",
                    "type": "email",
                    "required": True,
                    "maps_to": "email",
                }
            ],
        },
        headers=owner.headers,
    ).json()
    key = form["public_key"]
    response = api.post(
        f"/v1/public/forms/{key}/submissions",
        json={
            "values": {"email": email},
            "consent": True,
            "form_token": issue_token(key, client_bucket("testclient"), now=time.time() - 10),
        },
    )
    assert response.status_code == 200, response.text


def _links(message) -> dict[str, str]:
    return {kind: token for kind, token in LINK.findall(message.text_body)}


def test_form_lead_gets_double_opt_in_and_confirms(api, ws, outbox) -> None:
    owner, tenant = ws
    _submit_form(api, owner, tenant, "lead@example.ma")
    contact = api.get(_contacts(tenant), headers=owner.headers).json()["items"][0]
    consent = api.get(f"{_contacts(tenant)}/{contact['contact_id']}/consent", headers=owner.headers)
    assert consent.json()["status"] == "pending"
    assert [kind for _, kind, _ in _messages(tenant)] == ["confirmation"]

    _deliver_all(tenant)

    (sent,) = outbox.sent
    assert sent.to == ("lead@example.ma",) and sent.sender == "noreply@leadstack.test"
    tokens = _links(sent)
    assert set(tokens) == {"c", "u"}
    assert "lead@example.ma" not in tokens["c"]  # links carry identifiers only
    assert api.get(f"/v1/public/consent/confirm?token={tokens['c']}").status_code == 405  # no GET
    assert api.post("/v1/public/consent/confirm", json={"token": tokens["c"]}).status_code == 204
    consent = api.get(f"{_contacts(tenant)}/{contact['contact_id']}/consent", headers=owner.headers)
    assert consent.json()["status"] == "granted"


def test_unsubscribe_wins_and_suppresses_everything(api, ws, outbox) -> None:
    owner, tenant = ws
    _submit_form(api, owner, tenant, "u@example.ma")
    _deliver_all(tenant)
    tokens = _links(outbox.sent[0])
    contact_id = api.get(_contacts(tenant), headers=owner.headers).json()["items"][0]["contact_id"]
    queued = api.post(
        f"{_contacts(tenant)}/{contact_id}/messages",
        json={"subject": "Bonjour", "body": "Offre"},
        headers=owner.headers,
    )
    assert queued.status_code == 202

    assert (
        api.post("/v1/public/consent/unsubscribe", json={"token": tokens["u"]}).status_code == 204
    )
    reconfirm = api.post("/v1/public/consent/confirm", json={"token": tokens["c"]})
    blocked = api.post(
        f"{_contacts(tenant)}/{contact_id}/messages",
        json={"subject": "x", "body": "y"},
        headers=owner.headers,
    )
    deliver_message(uuid.UUID(tenant), uuid.UUID(queued.json()["message_id"]))

    assert (reconfirm.status_code, _code(reconfirm)) == (400, "invalid_link")
    assert (blocked.status_code, _code(blocked)) == (409, "consent_withdrawn")
    assert _messages(tenant)[-1][2] == "suppressed"
    assert len(outbox.sent) == 1  # only the original confirmation
    # A new form submission never re-contacts an unsubscribed address.
    _submit_form(api, owner, tenant, "u@example.ma")
    assert [k for _, k, _ in _messages(tenant)].count("confirmation") == 1


@pytest.mark.parametrize("mutation", ["tamper", "purpose", "garbage"])
def test_bad_links_are_rejected(api, ws, outbox, mutation) -> None:
    owner, tenant = ws
    _submit_form(api, owner, tenant, "t@example.ma")
    _deliver_all(tenant)
    tokens = _links(outbox.sent[0])
    token = {
        "tamper": tokens["c"][:-3] + "AAA",
        "purpose": tokens["u"],  # an unsubscribe token cannot confirm
        "garbage": "x" * 40,
    }[mutation]

    response = api.post("/v1/public/consent/confirm", json={"token": token})

    assert (response.status_code, _code(response)) == (400, "invalid_link")


def test_expired_link_is_rejected(api, ws) -> None:
    claims = links.LinkClaims(links.CONFIRM, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "0" * 64)
    old = links.sign(claims, now=time.time() - links.CONFIRM_TTL_SECONDS - 60)
    with pytest.raises(links.InvalidLinkError):
        links.verify(old, links.CONFIRM)


def test_one_to_one_email_is_delivered_with_unsubscribe_link(api, ws, outbox) -> None:
    owner, tenant = ws
    contact = api.post(
        _contacts(tenant), json={"first_name": "A", "email": "a@example.ma"}, headers=owner.headers
    ).json()

    queued = api.post(
        f"{_contacts(tenant)}/{contact['contact_id']}/messages",
        json={"subject": "Votre devis", "body": "Bonjour,\nvoici le devis."},
        headers=owner.headers,
    ).json()
    status = deliver_message(uuid.UUID(tenant), uuid.UUID(queued["message_id"]))
    again = deliver_message(uuid.UUID(tenant), uuid.UUID(queued["message_id"]))

    assert (status, again) == ("sent", "sent")
    (sent,) = outbox.sent  # delivered exactly once
    assert sent.subject == "Votre devis" and "u" in _links(sent)
    listed = api.get(
        f"{_contacts(tenant)}/{contact['contact_id']}/messages", headers=owner.headers
    ).json()
    assert listed[0]["status"] == "sent" and listed[0]["sent_at"]


def test_message_validation_and_isolation(api, ws, make_user) -> None:
    owner, tenant = ws
    no_email = api.post(_contacts(tenant), json={"first_name": "B"}, headers=owner.headers).json()
    with_email = api.post(
        _contacts(tenant), json={"email": "c@example.ma"}, headers=owner.headers
    ).json()
    url = f"{_contacts(tenant)}/{with_email['contact_id']}/messages"

    missing = api.post(
        f"{_contacts(tenant)}/{no_email['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    )
    injected = api.post(
        url, json={"subject": "Hi\r\nBcc: x@evil.example", "body": "b"}, headers=owner.headers
    )
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]
    cross = api.post(
        f"/v1/workspaces/{other}/contacts/{with_email['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=other_owner.headers,
    )

    assert (missing.status_code, _code(missing)) == (422, "no_email_address")
    assert injected.status_code == 422
    assert cross.status_code == 404


def test_delivery_is_at_most_once_and_bounded(api, ws) -> None:
    owner, tenant = ws
    contact = api.post(
        _contacts(tenant), json={"email": "d@example.ma"}, headers=owner.headers
    ).json()
    queued = api.post(
        f"{_contacts(tenant)}/{contact['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    ).json()
    message_id = uuid.UUID(queued["message_id"])
    failing = FakeEmailProvider(fail=True)
    email_adapter.install_provider(failing)
    try:
        from core.email import EmailProviderError

        for _ in range(2):
            with pytest.raises(EmailProviderError):
                deliver_message(uuid.UUID(tenant), message_id)
            assert _messages(tenant)[-1][2] == "queued"  # retried by the job layer
        assert deliver_message(uuid.UUID(tenant), message_id) == "failed"
    finally:
        email_adapter.install_provider(None)

    # A message left "sending" (crash after the provider call) is never re-sent.
    second = api.post(
        f"{_contacts(tenant)}/{contact['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    ).json()
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        row = session.get(Message, uuid.UUID(second["message_id"]))
        assert row is not None
        row.status = "sending"
    provider = FakeEmailProvider()
    email_adapter.install_provider(provider)
    try:
        assert deliver_message(uuid.UUID(tenant), uuid.UUID(second["message_id"])) == "failed"
        assert provider.sent == []
    finally:
        email_adapter.install_provider(None)


def test_changed_address_suppresses_queued_mail(api, ws, outbox) -> None:
    owner, tenant = ws
    contact = api.post(
        _contacts(tenant), json={"email": "old@example.ma"}, headers=owner.headers
    ).json()
    queued = api.post(
        f"{_contacts(tenant)}/{contact['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    ).json()
    api.patch(
        f"{_contacts(tenant)}/{contact['contact_id']}",
        json={"email": "new@example.ma"},
        headers=owner.headers,
    )

    assert deliver_message(uuid.UUID(tenant), uuid.UUID(queued["message_id"])) == "suppressed"
    assert outbox.sent == []


def test_contact_erasure_removes_messages_and_consent(api, ws, outbox) -> None:
    owner, tenant = ws
    _submit_form(api, owner, tenant, "erase@example.ma")
    contact_id = api.get(_contacts(tenant), headers=owner.headers).json()["items"][0]["contact_id"]

    api.delete(f"{_contacts(tenant)}/{contact_id}", headers=owner.headers)

    with tenant_session_scope(uuid.UUID(tenant)) as session:
        assert session.execute(select(Message)).scalars().all() == []
        assert session.execute(select(ContactConsent)).scalars().all() == []


def test_manual_consent_is_audited_and_member_can_record(api, ws, make_user) -> None:
    owner, tenant = ws
    contact = api.post(
        _contacts(tenant), json={"email": "m@example.ma"}, headers=owner.headers
    ).json()

    granted = api.put(
        f"{_contacts(tenant)}/{contact['contact_id']}/consent",
        json={"status": "granted"},
        headers=owner.headers,
    )

    assert granted.json()["status"] == "granted" and granted.json()["source"] == "manual"
    timeline = api.get(
        f"{_contacts(tenant)}/{contact['contact_id']}/timeline", headers=owner.headers
    ).json()
    assert "leadstack.consent.granted" in {
        i["action"] for i in timeline["items"] if i["item_type"] == "event"
    }


def test_missing_signing_secret_fails_closed(api, ws, monkeypatch) -> None:
    owner, tenant = ws
    monkeypatch.delenv("LEADSTACK_LINK_SIGNING_SECRET")
    from infra.secrets import get_secrets_provider

    get_secrets_provider.cache_clear()
    contact = api.post(
        _contacts(tenant), json={"email": "s@example.ma"}, headers=owner.headers
    ).json()

    response = api.post(
        f"{_contacts(tenant)}/{contact['contact_id']}/messages",
        json={"subject": "s", "body": "b"},
        headers=owner.headers,
    )
    well_formed = "x" * 30 + "." + "y" * 30  # reaches the signature check
    confirm = api.post("/v1/public/consent/confirm", json={"token": well_formed})

    assert (response.status_code, _code(response)) == (503, "unavailable")
    assert confirm.status_code == 503
