"""Regression tests for the Phase 3 independent-review findings
(docs/audits/phase-3.md)."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from infra.db import select, session_scope

from leadstack.domains.crm.models import Company, Contact
from tests.integration.test_crm import _companies, _contacts
from tests.integration.test_workspaces import _code, _create, _invite_and_accept

pytestmark = pytest.mark.integration


@pytest.fixture
def workspace(api, make_user):
    owner = make_user()
    return owner, _create(api, owner)["tenant_id"]


def test_concurrent_contact_writes_do_not_exhaust_the_pool(api, workspace, make_user) -> None:
    """H1: requests used to hold one pooled connection while waiting for a
    second (workspace country / owner lookups). 40 concurrent writes with a
    phone and an owner must all succeed."""
    owner, tenant = workspace
    colleague = make_user()
    _invite_and_accept(api, owner, tenant, colleague, "member")

    def create(i: int) -> int:
        return api.post(
            _contacts(tenant),
            json={
                "first_name": f"P{i}",
                "phone": f"0612{i:06d}"[:10],
                "owner_user_id": str(colleague.user_id),
            },
            headers=owner.headers,
        ).status_code

    with ThreadPoolExecutor(max_workers=40) as pool:
        statuses = list(pool.map(create, range(40)))

    assert statuses.count(201) + statuses.count(422) == 40, sorted(
        set(statuses)
    )  # 422: generated number invalid
    assert statuses.count(201) >= 30


@pytest.mark.parametrize("value", ["a\x00b", "a\x07b", "\x1b[31m"])
def test_control_characters_are_rejected_not_500(api, workspace, value) -> None:
    owner, tenant = workspace

    body = api.post(_contacts(tenant), json={"first_name": value}, headers=owner.headers)
    note = api.post(_companies(tenant), json={"name": "Ok", "notes": value}, headers=owner.headers)
    search = api.get(_contacts(tenant), params={"q": value}, headers=owner.headers)

    assert body.status_code == note.status_code == search.status_code == 422


def test_multiline_text_is_allowed(api, workspace) -> None:
    owner, tenant = workspace
    response = api.post(
        _companies(tenant), json={"name": "Ok", "notes": "line 1\nline 2\tx"}, headers=owner.headers
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    ("website", "expected"),
    [
        ("example.ma", "https://example.ma"),
        ("http://example.com/a?b=1", "http://example.com/a?b=1"),
        ("javascript:alert(document.cookie)", None),
        ("data:text/html,<script>alert(1)</script>", None),
        ("https://", None),
    ],
)
def test_company_website_only_http_or_https(api, workspace, website, expected) -> None:
    owner, tenant = workspace
    response = api.post(
        _companies(tenant), json={"name": "Site", "website": website}, headers=owner.headers
    )
    if expected is None:
        assert (response.status_code, _code(response)) == (422, "invalid_website")
    else:
        assert response.json()["website"] == expected


def test_company_name_cannot_be_cleared(api, workspace) -> None:
    owner, tenant = workspace
    company = api.post(_companies(tenant), json={"name": "Keep"}, headers=owner.headers).json()

    response = api.patch(
        f"{_companies(tenant)}/{company['company_id']}", json={"name": None}, headers=owner.headers
    )

    assert (response.status_code, _code(response)) == (422, "company_name_required")


def test_company_patch_null_clears_and_omission_keeps(api, workspace) -> None:
    owner, tenant = workspace
    company = api.post(
        _companies(tenant),
        json={"name": "Co", "website": "co.ma", "notes": "n"},
        headers=owner.headers,
    ).json()

    patched = api.patch(
        f"{_companies(tenant)}/{company['company_id']}",
        json={"website": None},
        headers=owner.headers,
    ).json()

    assert patched["website"] is None and patched["notes"] == "n" and patched["name"] == "Co"


def test_cross_tenant_company_and_cursor_cannot_be_used(api, make_user) -> None:
    owner_a, owner_b = make_user(), make_user()
    a = _create(api, owner_a)["tenant_id"]
    b = _create(api, owner_b, display_name="B")["tenant_id"]
    company_a = api.post(_companies(a), json={"name": "A Co"}, headers=owner_a.headers).json()
    contact_b = api.post(_contacts(b), json={"first_name": "Bee"}, headers=owner_b.headers).json()
    for i in range(3):
        api.post(_contacts(a), json={"first_name": f"A{i}"}, headers=owner_a.headers)
    cursor_a = api.get(_contacts(a), params={"limit": 1}, headers=owner_a.headers).json()[
        "next_cursor"
    ]

    link = api.patch(
        f"{_contacts(b)}/{contact_b['contact_id']}",
        json={"company_id": company_a["company_id"]},
        headers=owner_b.headers,
    )
    patch_company = api.patch(
        f"{_companies(b)}/{company_a['company_id']}", json={"name": "x"}, headers=owner_b.headers
    )
    delete_company = api.delete(
        f"{_companies(b)}/{company_a['company_id']}", headers=owner_b.headers
    )
    replay = api.get(_contacts(b), params={"cursor": cursor_a}, headers=owner_b.headers).json()

    assert (link.status_code, _code(link)) == (422, "unknown_company")
    assert patch_company.status_code == delete_company.status_code == 404
    # A cursor only positions within the caller's own workspace.
    assert all(c["first_name"].startswith("Bee") for c in replay["items"])


def test_member_cannot_delete_companies(api, workspace, make_user) -> None:
    owner, tenant = workspace
    member = make_user()
    _invite_and_accept(api, owner, tenant, member, "member")
    company = api.post(_companies(tenant), json={"name": "Co"}, headers=owner.headers).json()

    response = api.delete(f"{_companies(tenant)}/{company['company_id']}", headers=member.headers)

    assert response.status_code == 403


def test_underscore_is_matched_literally(api, workspace) -> None:
    owner, tenant = workspace
    api.post(_contacts(tenant), json={"first_name": "a_b"}, headers=owner.headers)
    api.post(_contacts(tenant), json={"first_name": "axb"}, headers=owner.headers)

    found = api.get(_contacts(tenant), params={"q": "a_b"}, headers=owner.headers).json()

    assert [c["first_name"] for c in found["items"]] == ["a_b"]


def test_phone_search_accepts_national_format(api, workspace) -> None:
    owner, tenant = workspace
    api.post(_contacts(tenant), json={"phone": "+212612345678"}, headers=owner.headers)

    found = api.get(_contacts(tenant), params={"q": "06 12 34 56 78"}, headers=owner.headers)

    assert [c["phone_e164"] for c in found.json()["items"]] == ["+212612345678"]


def test_pagination_with_identical_timestamps(api, workspace) -> None:
    """Rows inserted in one transaction share created_at; the (created_at,
    id) keyset must still return each exactly once."""
    owner, tenant = workspace
    tenant_id = uuid.UUID(tenant)
    from infra.db import tenant_session_scope

    with tenant_session_scope(tenant_id) as session:
        session.add_all(
            Contact(tenant_id=tenant_id, first_name=f"T{i}", created_by_user_id=owner.user_id)
            for i in range(7)
        )

    seen, cursor = [], None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = api.get(_contacts(tenant), params=params, headers=owner.headers).json()
        seen += [c["contact_id"] for c in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 7


def test_crm_tables_return_nothing_without_tenant_context(api, workspace) -> None:
    owner, tenant = workspace
    api.post(_contacts(tenant), json={"first_name": "Hidden"}, headers=owner.headers)
    api.post(_companies(tenant), json={"name": "Hidden"}, headers=owner.headers)

    with session_scope() as session:
        assert session.execute(select(Contact)).scalars().all() == []
        assert session.execute(select(Company)).scalars().all() == []


def test_cross_origin_state_changes_are_rejected(api, workspace) -> None:
    owner, tenant = workspace
    evil = {**owner.headers, "Origin": "https://evil.example"}
    sibling = {**owner.headers, "Origin": "https://pages.leadstack.example"}
    same = {**owner.headers, "Origin": "http://testserver"}

    blocked = api.post(_contacts(tenant), json={"first_name": "x"}, headers=evil)
    blocked_sibling = api.delete(f"{_contacts(tenant)}/{uuid.uuid4()}", headers=sibling)
    null_origin = api.post(
        _contacts(tenant), json={"first_name": "x"}, headers={**owner.headers, "Origin": "null"}
    )
    allowed = api.post(_contacts(tenant), json={"first_name": "x"}, headers=same)
    read = api.get(_contacts(tenant), headers=evil)  # reads are not state-changing

    assert (blocked.status_code, _code(blocked)) == (403, "cross_origin_rejected")
    assert blocked_sibling.status_code == null_origin.status_code == 403
    assert allowed.status_code == 201
    assert read.status_code == 200
