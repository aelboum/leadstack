"""Phase 3: contacts and companies through the real HTTP stack, including
tenant isolation, authorization, validation, search and pagination."""

from __future__ import annotations

import uuid

import pytest
from core import audit_log

from tests.integration.test_workspaces import _code, _create, _invite_and_accept

pytestmark = pytest.mark.integration


def _contacts(tenant_id: str) -> str:
    return f"/v1/workspaces/{tenant_id}/contacts"


def _companies(tenant_id: str) -> str:
    return f"/v1/workspaces/{tenant_id}/companies"


@pytest.fixture
def workspace(api, make_user):
    owner = make_user()
    tenant_id = _create(api, owner)["tenant_id"]  # country MA, currency MAD
    return owner, tenant_id


def test_create_read_update_delete_contact(api, workspace) -> None:
    owner, tenant = workspace
    company = api.post(_companies(tenant), json={"name": "Riad Atlas"}, headers=owner.headers)
    assert company.status_code == 201
    company_id = company.json()["company_id"]

    created = api.post(
        _contacts(tenant),
        json={
            "first_name": "Salma",
            "last_name": "Benali",
            "email": "Salma.Benali@Example.MA",
            "phone": "06 12 34 56 78",  # national format, interpreted for the workspace (MA)
            "company_id": company_id,
            "source": "referral",
        },
        headers=owner.headers,
    )
    assert created.status_code == 201, created.text
    contact = created.json()
    assert contact["email"] == "Salma.Benali@example.ma"
    assert contact["phone_e164"] == "+212612345678"
    assert contact["company_id"] == company_id
    contact_id = contact["contact_id"]

    assert api.get(f"{_contacts(tenant)}/{contact_id}", headers=owner.headers).json() == contact

    updated = api.patch(
        f"{_contacts(tenant)}/{contact_id}",
        json={"job_title": "Gérante", "company_id": None},
        headers=owner.headers,
    )
    assert updated.status_code == 200
    assert (updated.json()["job_title"], updated.json()["company_id"]) == ("Gérante", None)
    assert updated.json()["first_name"] == "Salma"  # omitted fields unchanged

    assert api.delete(f"{_contacts(tenant)}/{contact_id}", headers=owner.headers).status_code == 204
    assert api.get(f"{_contacts(tenant)}/{contact_id}", headers=owner.headers).status_code == 404
    actions = [e.action for e in audit_log.list(uuid.UUID(tenant), limit=100)]
    for action in ("created", "updated", "deleted"):
        assert f"leadstack.contact.{action}" in actions


def test_audit_entries_contain_no_personal_data(api, workspace) -> None:
    owner, tenant = workspace
    contact = api.post(
        _contacts(tenant),
        json={"first_name": "Youssef", "email": "youssef@example.com", "phone": "+33612345678"},
        headers=owner.headers,
    ).json()
    api.patch(
        f"{_contacts(tenant)}/{contact['contact_id']}",
        json={"email": "y2@example.com"},
        headers=owner.headers,
    )

    for entry in audit_log.list(uuid.UUID(tenant), limit=100):
        blob = repr((entry.entry_metadata, entry.resource_id))
        for personal in ("Youssef", "youssef@example.com", "y2@example.com", "+33612345678"):
            assert personal not in blob


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, "empty_contact"),
        ({"first_name": "   "}, "empty_contact"),
        ({"first_name": "A", "email": "not-an-email"}, "invalid_email"),
        ({"first_name": "A", "phone": "12"}, "invalid_phone"),
        ({"first_name": "A", "phone": "+999 123"}, "invalid_phone"),
        ({"first_name": "A", "company_id": str(uuid.uuid4())}, "unknown_company"),
        ({"first_name": "A", "owner_user_id": str(uuid.uuid4())}, "invalid_owner"),
    ],
)
def test_contact_validation(api, workspace, body, code) -> None:
    owner, tenant = workspace

    response = api.post(_contacts(tenant), json=body, headers=owner.headers)

    assert response.status_code == 422
    assert _code(response) == code


def test_unknown_and_overlong_fields_are_rejected(api, workspace) -> None:
    owner, tenant = workspace
    for body in (
        {"first_name": "A", "tenant_id": str(uuid.uuid4())},
        {"first_name": "A", "created_by_user_id": str(uuid.uuid4())},
        {"first_name": "A" * 101},
    ):
        assert api.post(_contacts(tenant), json=body, headers=owner.headers).status_code == 422


def test_update_cannot_strip_every_identifier(api, workspace) -> None:
    owner, tenant = workspace
    contact = api.post(_contacts(tenant), json={"email": "a@example.com"}, headers=owner.headers)

    response = api.patch(
        f"{_contacts(tenant)}/{contact.json()['contact_id']}",
        json={"email": None},
        headers=owner.headers,
    )

    assert (response.status_code, _code(response)) == (422, "empty_contact")


def test_contacts_are_isolated_between_workspaces(api, make_user) -> None:
    owner_a, owner_b = make_user(), make_user()
    a = _create(api, owner_a)["tenant_id"]
    b = _create(api, owner_b, display_name="B")["tenant_id"]
    contact_a = api.post(_contacts(a), json={"first_name": "Secret"}, headers=owner_a.headers)
    company_a = api.post(_companies(a), json={"name": "Hidden Co"}, headers=owner_a.headers)
    cid, coid = contact_a.json()["contact_id"], company_a.json()["company_id"]

    # Another workspace's owner, through their own workspace path:
    assert api.get(f"{_contacts(b)}/{cid}", headers=owner_b.headers).status_code == 404
    assert (
        api.patch(
            f"{_contacts(b)}/{cid}", json={"first_name": "x"}, headers=owner_b.headers
        ).status_code
        == 404
    )
    assert api.delete(f"{_contacts(b)}/{cid}", headers=owner_b.headers).status_code == 404
    assert api.get(f"{_companies(b)}/{coid}", headers=owner_b.headers).status_code == 404
    linked = api.post(
        _contacts(b), json={"first_name": "x", "company_id": coid}, headers=owner_b.headers
    )
    assert (linked.status_code, _code(linked)) == (422, "unknown_company")
    assert (
        api.get(_contacts(b), params={"q": "Secret"}, headers=owner_b.headers).json()["items"] == []
    )
    # ...and through the other workspace's path:
    assert api.get(f"{_contacts(a)}/{cid}", headers=owner_b.headers).status_code == 404
    assert api.get(_contacts(a), headers=owner_b.headers).status_code == 404
    # Untouched:
    assert (
        api.get(f"{_contacts(a)}/{cid}", headers=owner_a.headers).json()["first_name"] == "Secret"
    )


def test_member_can_edit_but_not_delete(api, workspace, make_user) -> None:
    owner, tenant = workspace
    member = make_user()
    _invite_and_accept(api, owner, tenant, member, "member")
    contact = api.post(_contacts(tenant), json={"first_name": "Nadia"}, headers=member.headers)
    assert contact.status_code == 201
    cid = contact.json()["contact_id"]

    assert (
        api.patch(
            f"{_contacts(tenant)}/{cid}", json={"last_name": "K"}, headers=member.headers
        ).status_code
        == 200
    )
    assert api.delete(f"{_contacts(tenant)}/{cid}", headers=member.headers).status_code == 403
    assert api.delete(f"{_contacts(tenant)}/{cid}", headers=owner.headers).status_code == 204


def test_contact_owner_must_be_an_active_member(api, workspace, make_user) -> None:
    owner, tenant = workspace
    colleague = make_user()
    _invite_and_accept(api, owner, tenant, colleague, "member")

    ok = api.post(
        _contacts(tenant),
        json={"first_name": "A", "owner_user_id": str(colleague.user_id)},
        headers=owner.headers,
    )
    outsider = api.post(
        _contacts(tenant),
        json={"first_name": "B", "owner_user_id": str(make_user().user_id)},
        headers=owner.headers,
    )

    assert ok.status_code == 201
    assert (outsider.status_code, _code(outsider)) == (422, "invalid_owner")


def test_search_filters_and_paginates(api, workspace) -> None:
    owner, tenant = workspace
    company = api.post(_companies(tenant), json={"name": "Café Hafa"}, headers=owner.headers)
    company_id = company.json()["company_id"]
    names = [f"Karim{i}" for i in range(5)] + ["Amina", "محمد"]
    for name in names:
        api.post(
            _contacts(tenant),
            json={"first_name": name, "company_id": company_id if name == "Amina" else None},
            headers=owner.headers,
        )

    karims = api.get(_contacts(tenant), params={"q": "karim"}, headers=owner.headers).json()
    arabic = api.get(_contacts(tenant), params={"q": "محمد"}, headers=owner.headers).json()
    by_company = api.get(
        _contacts(tenant), params={"company_id": company_id}, headers=owner.headers
    )
    wildcard = api.get(_contacts(tenant), params={"q": "%"}, headers=owner.headers).json()

    assert len(karims["items"]) == 5
    assert [c["first_name"] for c in arabic["items"]] == ["محمد"]
    assert [c["first_name"] for c in by_company.json()["items"]] == ["Amina"]
    assert wildcard["items"] == []  # LIKE wildcards are matched literally

    seen, cursor = [], None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = api.get(_contacts(tenant), params=params, headers=owner.headers).json()
        seen.extend(c["first_name"] for c in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(names)  # newest first, every contact exactly once
    assert seen[0] == "محمد"
    bad = api.get(_contacts(tenant), params={"cursor": "zzz"}, headers=owner.headers)
    assert (bad.status_code, _code(bad)) == (422, "invalid_cursor")


def test_deleting_a_company_unlinks_its_contacts(api, workspace) -> None:
    owner, tenant = workspace
    company_id = api.post(_companies(tenant), json={"name": "Gone"}, headers=owner.headers).json()[
        "company_id"
    ]
    contact_id = api.post(
        _contacts(tenant),
        json={"first_name": "Kept", "company_id": company_id},
        headers=owner.headers,
    ).json()["contact_id"]

    assert (
        api.delete(f"{_companies(tenant)}/{company_id}", headers=owner.headers).status_code == 204
    )

    contact = api.get(f"{_contacts(tenant)}/{contact_id}", headers=owner.headers).json()
    assert contact["company_id"] is None and contact["first_name"] == "Kept"


def test_international_phone_without_workspace_country(api, make_user) -> None:
    owner = make_user()
    tenant = _create(api, owner, country=None)["tenant_id"]

    national = api.post(_contacts(tenant), json={"phone": "0612345678"}, headers=owner.headers)
    international = api.post(
        _contacts(tenant), json={"phone": "+212 6 12 34 56 78"}, headers=owner.headers
    )

    assert (national.status_code, _code(national)) == (422, "invalid_phone")
    assert international.json()["phone_e164"] == "+212612345678"
