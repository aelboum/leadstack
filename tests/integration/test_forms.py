"""Phase 6: lead-capture forms, public submissions and spam controls."""

from __future__ import annotations

import time
import uuid

import pytest
from core import audit_log
from core.tenancy import TenantStatus, transition_tenant_status

from leadstack.domains.forms.service import issue_token
from leadstack.routes.forms import client_bucket
from tests.integration.test_crm import _contacts
from tests.integration.test_workspaces import _code, _create, _invite_and_accept

pytestmark = pytest.mark.integration

FIELDS = [
    {"key": "name", "label": "Nom", "type": "text", "required": True, "maps_to": "first_name"},
    {"key": "email", "label": "E-mail", "type": "email", "required": True, "maps_to": "email"},
    {"key": "phone", "label": "Téléphone", "type": "phone", "maps_to": "phone"},
    {"key": "msg", "label": "Message", "type": "textarea", "maps_to": "message"},
]


def _w(tenant: str) -> str:
    return f"/v1/workspaces/{tenant}"


@pytest.fixture
def form(api, make_user):
    owner = make_user()
    tenant = _create(api, owner)["tenant_id"]  # country MA
    created = api.post(
        f"{_w(tenant)}/forms",
        json={
            "name": "Contact",
            "fields": FIELDS,
            "consent_text": "J'accepte d'être recontacté.",
            "success_message": "Merci !",
            "redirect_url": "https://example.ma/merci",
        },
        headers=owner.headers,
    )
    assert created.status_code == 201, created.text
    return owner, tenant, created.json()


# Starlette's TestClient reports this client address.
CLIENT = client_bucket("testclient")


def _token(public_key: str, age: float = 10, client: str = CLIENT) -> str:
    return issue_token(public_key, client, now=time.time() - age)


def _submit(api, key: str, values: dict, *, consent=True, token=None, headers=None, **extra):
    return api.post(
        f"/v1/public/forms/{key}/submissions",
        json={"values": values, "consent": consent, "form_token": token or _token(key)} | extra,
        headers=headers or {},
    )


def test_public_submission_creates_a_contact_with_consent(api, form) -> None:
    owner, tenant, created = form
    key = created["public_key"]

    public = api.get(f"/v1/public/forms/{key}")
    assert public.status_code == 200
    assert public.headers["cache-control"] == "no-store"
    assert public.headers["access-control-allow-origin"] == "*"
    assert [f["key"] for f in public.json()["fields"]] == ["name", "email", "phone", "msg"]
    assert "public_key" not in public.json() and "source_label" not in public.json()

    response = _submit(
        api,
        key,
        {"name": "Salma", "email": "Salma@Example.MA", "phone": "06 12 34 56 78", "msg": "Bonjour"},
        headers={"Origin": "https://landing.example.ma"},  # cross-origin is the point
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "success_message": "Merci !",
        "redirect_url": "https://example.ma/merci",
    }
    contacts = api.get(_contacts(tenant), headers=owner.headers).json()["items"]
    assert [(c["first_name"], c["email"], c["phone_e164"]) for c in contacts] == [
        ("Salma", "Salma@example.ma", "+212612345678")
    ]
    submissions = api.get(
        f"{_w(tenant)}/forms/{created['form_id']}/submissions", headers=owner.headers
    ).json()
    assert submissions[0]["contact_id"] == contacts[0]["contact_id"]
    assert submissions[0]["data"]["msg"] == "Bonjour"
    entry = next(
        e
        for e in audit_log.list(uuid.UUID(tenant), limit=50)
        if e.action == "leadstack.form.submitted"
    )
    assert "Salma" not in repr(entry.entry_metadata)


def test_existing_contact_is_linked_never_overwritten(api, form) -> None:
    owner, tenant, created = form
    existing = api.post(
        _contacts(tenant),
        json={"first_name": "Real", "email": "lead@example.ma"},
        headers=owner.headers,
    ).json()

    _submit(api, created["public_key"], {"name": "Attacker", "email": "LEAD@example.ma"})

    contact = api.get(f"{_contacts(tenant)}/{existing['contact_id']}", headers=owner.headers).json()
    assert contact["first_name"] == "Real"
    assert len(api.get(_contacts(tenant), headers=owner.headers).json()["items"]) == 1


@pytest.mark.parametrize(
    ("values", "kwargs"),
    [
        ({"name": "A", "email": "a@example.ma"}, {"consent": False}),  # consent required
        ({"name": "A"}, {}),  # required field missing
        ({"name": "A", "email": "not-an-email"}, {}),
        ({"name": "A", "email": "a@example.ma", "unknown": "x"}, {}),  # undefined field
        ({"name": "A" * 201, "email": "a@example.ma"}, {}),  # over the field limit
        ({"name": "A", "email": "a@example.ma", "phone": "12"}, {}),
    ],
)
def test_invalid_submissions_are_rejected(api, form, values, kwargs) -> None:
    _, _, created = form
    response = _submit(api, created["public_key"], values, **kwargs)
    assert response.status_code == 422


def test_token_controls_fill_time_age_and_integrity(api, form) -> None:
    _, _, created = form
    key = created["public_key"]
    values = {"name": "A", "email": "a@example.ma"}

    too_fast = _submit(api, key, values, token=_token(key, age=0))
    too_old = _submit(api, key, values, token=_token(key, age=3 * 3600))
    forged = _submit(api, key, values, token=f"{int(time.time()) - 10}.forgedsignature")
    other_form = _submit(api, key, values, token=_token("another-public-key-0000000"))
    other_client = _submit(api, key, values, token=_token(key, client=client_bucket("203.0.113.9")))
    ok = _submit(api, key, values)

    assert too_fast.status_code == too_old.status_code == forged.status_code == 422
    assert other_form.status_code == other_client.status_code == 422
    assert ok.status_code == 200


def test_honeypot_looks_successful_but_stores_nothing(api, form) -> None:
    owner, tenant, created = form

    response = _submit(
        api,
        created["public_key"],
        {"name": "Bot", "email": "bot@spam.example"},
        website="http://spam",
    )

    assert response.status_code == 200
    assert api.get(_contacts(tenant), headers=owner.headers).json()["items"] == []


def test_unknown_disabled_and_suspended_forms_are_indistinguishable(api, form, make_user) -> None:
    owner, tenant, created = form
    key = created["public_key"]
    values = {"name": "A", "email": "a@example.ma"}

    assert api.get(f"/v1/public/forms/{'x' * 32}").status_code == 404
    assert api.get("/v1/public/forms/short").status_code == 404
    api.post(
        f"{_w(tenant)}/forms/{created['form_id']}/status",
        json={"status": "disabled"},
        headers=owner.headers,
    )
    assert api.get(f"/v1/public/forms/{key}").status_code == 404
    assert _submit(api, key, values).status_code == 404
    api.post(
        f"{_w(tenant)}/forms/{created['form_id']}/status",
        json={"status": "active"},
        headers=owner.headers,
    )
    assert _submit(api, key, values).status_code == 200

    transition_tenant_status(uuid.UUID(tenant), TenantStatus.SUSPENDED, actor_user_id=owner.user_id)
    suspended = api.get(f"/v1/public/forms/{key}")
    assert (suspended.status_code, _code(suspended)) == (404, "form_not_found")


def test_submissions_are_rate_limited_per_client(api, form) -> None:
    _, _, created = form
    key = created["public_key"]
    statuses = [
        _submit(api, key, {"name": "A", "email": f"a{i}@example.ma"}).status_code for i in range(21)
    ]
    assert statuses[:20] == [200] * 20
    assert statuses[20] == 429


def test_cors_preflight_only_on_public_paths(api, form) -> None:
    owner, tenant, created = form
    preflight = api.options(
        f"/v1/public/forms/{created['public_key']}/submissions",
        headers={"Origin": "https://landing.example.ma", "Access-Control-Request-Method": "POST"},
    )
    private = api.get(
        _contacts(tenant), headers={**owner.headers, "Origin": "https://evil.example"}
    )

    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in preflight.headers
    assert "access-control-allow-origin" not in private.headers


def test_form_definitions_are_validated_and_managed_by_admins(api, form, make_user) -> None:
    owner, tenant, _ = form
    member = make_user()
    _invite_and_accept(api, owner, tenant, member, "member")
    base = {"name": "F", "consent_text": "ok"}
    for bad in (
        {"fields": [{"key": "msg", "label": "M", "type": "textarea"}]},  # no email/phone
        {"fields": FIELDS, "redirect_url": "javascript:alert(1)"},
        {"fields": FIELDS, "redirect_url": "http://insecure.example"},
        {"fields": [*FIELDS, {"key": "website", "label": "W", "type": "text"}]},  # honeypot name
        {"fields": [FIELDS[1], {**FIELDS[1], "key": "email2"}]},  # same target twice
    ):
        assert (
            api.post(f"{_w(tenant)}/forms", json=base | bad, headers=owner.headers).status_code
            == 422
        )
    assert (
        api.post(
            f"{_w(tenant)}/forms", json=base | {"fields": FIELDS}, headers=member.headers
        ).status_code
        == 403
    )
    assert api.get(f"{_w(tenant)}/forms", headers=member.headers).status_code == 200


def test_forms_are_isolated(api, form, make_user) -> None:
    _, _, created = form
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]

    assert (
        api.get(
            f"{_w(other)}/forms/{created['form_id']}/submissions", headers=other_owner.headers
        ).json()
        == []
    )
    assert (
        api.post(
            f"{_w(other)}/forms/{created['form_id']}/status",
            json={"status": "disabled"},
            headers=other_owner.headers,
        ).status_code
        == 404
    )
    assert (
        api.delete(
            f"{_w(other)}/forms/{created['form_id']}", headers=other_owner.headers
        ).status_code
        == 404
    )
    assert api.get(f"/v1/public/forms/{created['public_key']}").status_code == 200


def test_deleting_a_form_revokes_its_public_key(api, form) -> None:
    owner, tenant, created = form
    assert (
        api.delete(f"{_w(tenant)}/forms/{created['form_id']}", headers=owner.headers).status_code
        == 204
    )
    assert api.get(f"/v1/public/forms/{created['public_key']}").status_code == 404


def test_oversized_and_malformed_bodies_are_cut_off_before_parsing(api, form) -> None:
    _, _, created = form
    url = f"/v1/public/forms/{created['public_key']}/submissions"

    huge = api.post(
        url, content=b"{" + b" " * (70 * 1024) + b"}", headers={"Content-Type": "application/json"}
    )
    malformed = [
        api.post(url, content=b"{not json", headers={"Content-Type": "application/json"})
        for _ in range(25)
    ]

    assert (huge.status_code, huge.json()["detail"]["code"]) == (413, "payload_too_large")
    assert huge.headers["access-control-allow-origin"] == "*"
    statuses = [r.status_code for r in malformed]
    assert 429 in statuses  # malformed requests are rate limited too


@pytest.mark.parametrize("value", ["a\x00b", "x\x07y"])
def test_control_characters_are_rejected_not_500(api, form, value) -> None:
    _, _, created = form
    response = _submit(api, created["public_key"], {"name": value, "email": "a@example.ma"})
    assert response.status_code == 422


def test_junk_does_not_consume_the_form_quota(api, form) -> None:
    """Rejected submissions are not counted against the per-form quota."""
    _, _, created = form
    import leadstack.routes.forms as routes

    original = routes.SUBMISSIONS_PER_FORM_PER_HOUR
    routes.SUBMISSIONS_PER_FORM_PER_HOUR = 2
    try:
        for _ in range(5):
            _submit(api, created["public_key"], {"name": "A", "email": "bad"})
        ok = _submit(api, created["public_key"], {"name": "A", "email": "ok@example.ma"})
    finally:
        routes.SUBMISSIONS_PER_FORM_PER_HOUR = original
    assert ok.status_code == 200


def test_daily_cap_per_workspace(api, form, monkeypatch) -> None:
    _, _, created = form
    monkeypatch.setenv("LEADSTACK_FORM_DAILY_SUBMISSIONS", "2")
    statuses = [
        _submit(api, created["public_key"], {"name": "A", "email": f"d{i}@example.ma"}).status_code
        for i in range(3)
    ]
    assert statuses == [200, 200, 429]


def test_disabled_form_with_bad_token_is_still_not_found(api, form) -> None:
    owner, tenant, created = form
    api.post(
        f"{_w(tenant)}/forms/{created['form_id']}/status",
        json={"status": "disabled"},
        headers=owner.headers,
    )
    response = _submit(
        api,
        created["public_key"],
        {"name": "A", "email": "a@example.ma"},
        token="1.badsignature-00000000",
    )
    assert response.status_code == 404


def test_ipv6_clients_share_a_slash_64_bucket() -> None:
    assert client_bucket("2001:db8:1:2::1") == client_bucket("2001:db8:1:2:ffff::9")
    assert client_bucket("2001:db8:1:3::1") != client_bucket("2001:db8:1:2::1")
    assert client_bucket("198.51.100.7") != client_bucket("198.51.100.8")
