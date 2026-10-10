"""Phase 2: workspaces, clients, members, invitations and audit, through the
real HTTP stack (SaaS-OS ingress chain, RBAC, RLS) on disposable services.

Every security property is asserted as a negative test as well: what must
be refused is exercised and shown to be refused."""

from __future__ import annotations

import uuid

import pytest
from infra.db import select, session_scope, tenant_session_scope

from leadstack.domains.workspaces.models import Workspace
from tests.integration.conftest import ApiUser

pytestmark = pytest.mark.integration

AGENCY = {
    "kind": "agency",
    "display_name": "Atlas Digital",
    "locale": "fr",
    "time_zone": "Africa/Casablanca",
    "currency": "MAD",
    "country": "MA",
}


def _create(api, user: ApiUser, **overrides) -> dict:
    response = api.post("/v1/workspaces", json={**AGENCY, **overrides}, headers=user.headers)
    assert response.status_code == 201, response.text
    return response.json()


def _invite_and_accept(api, owner: ApiUser, tenant_id: str, invitee: ApiUser, role: str) -> dict:
    created = api.post(
        f"/v1/workspaces/{tenant_id}/invitations",
        json={"email": f"{uuid.uuid4().hex[:8]}@example.com", "role": role},
        headers=owner.headers,
    )
    assert created.status_code == 201, created.text
    accepted = api.post(
        "/v1/invitations/accept",
        json={"tenant_id": tenant_id, "token": created.json()["token"]},
        headers=invitee.headers,
    )
    assert accepted.status_code == 200, accepted.text
    return {"invitation": created.json(), "accepted": accepted.json()}


def _code(response) -> str:
    return response.json()["detail"]["code"]


def _member_id(api, owner: ApiUser, tenant_id: str, user: ApiUser) -> str:
    members = api.get(f"/v1/workspaces/{tenant_id}/members", headers=owner.headers).json()
    (match,) = [m for m in members["items"] if m["user_id"] == str(user.user_id)]
    return match["membership_id"]


# --- Creation and reading -------------------------------------------------------


def test_create_workspace_makes_creator_owner(api, make_user) -> None:
    owner = make_user()

    workspace = _create(api, owner)

    assert workspace["kind"] == "agency"
    assert workspace["status"] == "active"
    assert workspace["parent_tenant_id"] is None
    tenant_id = workspace["tenant_id"]
    assert api.get(f"/v1/workspaces/{tenant_id}", headers=owner.headers).json() == workspace
    mine = api.get("/v1/workspaces", headers=owner.headers).json()["items"]
    assert [w["tenant_id"] for w in mine] == [tenant_id]
    members = api.get(f"/v1/workspaces/{tenant_id}/members", headers=owner.headers).json()
    assert [(m["user_id"], m["roles"]) for m in members["items"]] == [
        (str(owner.user_id), ["owner"])
    ]


def test_unauthenticated_requests_are_rejected(api) -> None:
    assert api.post("/v1/workspaces", json=AGENCY).status_code == 401
    assert api.get("/v1/workspaces").status_code == 401
    assert api.get(f"/v1/workspaces/{uuid.uuid4()}").status_code == 401
    assert api.get("/v1/workspaces", headers={"Authorization": "Bearer forged"}).status_code == 401


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("locale", "de", "unsupported_locale"),
        ("time_zone", "Mars/Olympus", "unsupported_time_zone"),
        ("currency", "XXX", "unsupported_currency"),
        ("country", "ma", "unsupported_country"),
    ],
)
def test_regional_values_are_validated(api, make_user, field, value, code) -> None:
    response = api.post(
        "/v1/workspaces", json={**AGENCY, field: value}, headers=make_user().headers
    )

    assert response.status_code == 422
    assert _code(response) == code


def test_clients_cannot_be_created_as_root_and_unknown_fields_are_rejected(api, make_user) -> None:
    user = make_user()
    assert (
        api.post(
            "/v1/workspaces", json={**AGENCY, "kind": "client"}, headers=user.headers
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/v1/workspaces", json={**AGENCY, "tenant_id": str(uuid.uuid4())}, headers=user.headers
        ).status_code
        == 422
    )


def test_update_workspace_validates_and_audits(api, make_user) -> None:
    owner = make_user()
    tenant_id = _create(api, owner)["tenant_id"]

    updated = api.patch(
        f"/v1/workspaces/{tenant_id}",
        json={"display_name": "Atlas", "locale": "ar", "country": None},
        headers=owner.headers,
    )

    assert updated.status_code == 200
    assert (
        updated.json()["display_name"],
        updated.json()["locale"],
        updated.json()["country"],
    ) == (
        "Atlas",
        "ar",
        None,
    )
    bad = api.patch(f"/v1/workspaces/{tenant_id}", json={"currency": "ABC"}, headers=owner.headers)
    assert bad.status_code == 422
    actions = [
        e["action"]
        for e in api.get(f"/v1/workspaces/{tenant_id}/audit", headers=owner.headers).json()["items"]
    ]
    assert "leadstack.workspace.updated" in actions


# --- Tenant isolation --------------------------------------------------------------


def test_other_tenants_cannot_read_or_modify_a_workspace(api, make_user) -> None:
    owner_a, owner_b = make_user(), make_user()
    a = _create(api, owner_a)["tenant_id"]
    _create(api, owner_b, display_name="Other agency")

    for method, path, body in [
        ("get", f"/v1/workspaces/{a}", None),
        ("patch", f"/v1/workspaces/{a}", {"display_name": "pwned"}),
        ("get", f"/v1/workspaces/{a}/members", None),
        ("get", f"/v1/workspaces/{a}/invitations", None),
        ("post", f"/v1/workspaces/{a}/invitations", {"email": "x@example.com", "role": "owner"}),
        ("get", f"/v1/workspaces/{a}/audit", None),
        ("get", f"/v1/workspaces/{a}/clients", None),
        ("post", f"/v1/workspaces/{a}/clients", {"display_name": "Sneaky"}),
    ]:
        response = api.request(method, path, json=body, headers=owner_b.headers)
        assert response.status_code == 404, (method, path, response.text)

    # Indistinguishable from a workspace that does not exist at all.
    missing = api.get(f"/v1/workspaces/{uuid.uuid4()}", headers=owner_b.headers)
    assert missing.status_code == 404
    assert (
        api.get(f"/v1/workspaces/{a}", headers=owner_a.headers).json()["display_name"]
        == "Atlas Digital"
    )
    assert [
        w["tenant_id"] for w in api.get("/v1/workspaces", headers=owner_b.headers).json()["items"]
    ] != [a]


def test_row_level_security_hides_workspace_rows_outside_their_tenant(api, make_user) -> None:
    a = uuid.UUID(_create(api, make_user())["tenant_id"])
    b = uuid.UUID(_create(api, make_user())["tenant_id"])

    with session_scope() as session:  # no tenant context at all
        assert session.execute(select(Workspace)).scalars().all() == []
    with tenant_session_scope(b) as session:
        rows = session.execute(select(Workspace)).scalars().all()
        assert [row.tenant_id for row in rows] == [b]
        assert session.get(Workspace, a) is None


# --- Agency clients ---------------------------------------------------------------


def test_agency_creates_and_lists_clients_with_direct_membership(api, make_user) -> None:
    owner = make_user()
    agency = _create(api, owner)["tenant_id"]

    client = api.post(
        f"/v1/workspaces/{agency}/clients",
        json={"display_name": "Riad Café", "currency": "EUR"},
        headers=owner.headers,
    )

    assert client.status_code == 201, client.text
    body = client.json()
    assert (body["kind"], body["parent_tenant_id"], body["currency"], body["locale"]) == (
        "client",
        agency,
        "EUR",
        "fr",  # inherited from the agency
    )
    listed = api.get(f"/v1/workspaces/{agency}/clients", headers=owner.headers).json()["items"]
    assert [c["tenant_id"] for c in listed] == [body["tenant_id"]]
    # The creator works in the client through a direct membership.
    assert api.get(f"/v1/workspaces/{body['tenant_id']}", headers=owner.headers).status_code == 200
    mine = {
        w["tenant_id"] for w in api.get("/v1/workspaces", headers=owner.headers).json()["items"]
    }
    assert mine == {agency, body["tenant_id"]}


def test_direct_customer_cannot_create_clients(api, make_user) -> None:
    owner = make_user()
    direct = _create(api, owner, kind="direct")["tenant_id"]

    response = api.post(
        f"/v1/workspaces/{direct}/clients", json={"display_name": "x"}, headers=owner.headers
    )

    assert response.status_code == 409
    assert _code(response) == "not_an_agency"


def test_agency_staff_without_membership_cannot_enter_client(api, make_user) -> None:
    """ADR-0002 option b: hierarchy alone grants nothing over HTTP."""
    owner, staff = make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, agency, staff, "admin")
    client = api.post(
        f"/v1/workspaces/{agency}/clients", json={"display_name": "C"}, headers=owner.headers
    ).json()["tenant_id"]

    assert api.get(f"/v1/workspaces/{client}", headers=staff.headers).status_code == 404
    # ...but the agency admin can see the client in the agency's list.
    listed = api.get(f"/v1/workspaces/{agency}/clients", headers=staff.headers).json()["items"]
    assert [c["tenant_id"] for c in listed] == [client]


def test_client_suspension_blocks_client_principals_until_reactivated(api, make_user) -> None:
    owner = make_user()
    agency = _create(api, owner)["tenant_id"]
    client = api.post(
        f"/v1/workspaces/{agency}/clients", json={"display_name": "C"}, headers=owner.headers
    ).json()["tenant_id"]

    suspended = api.post(
        f"/v1/workspaces/{agency}/clients/{client}/status",
        json={"status": "suspended"},
        headers=owner.headers,
    )

    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"
    assert api.get(f"/v1/workspaces/{client}", headers=owner.headers).status_code == 404
    assert client not in {
        w["tenant_id"] for w in api.get("/v1/workspaces", headers=owner.headers).json()["items"]
    }
    reactivated = api.post(
        f"/v1/workspaces/{agency}/clients/{client}/status",
        json={"status": "active"},
        headers=owner.headers,
    )
    assert reactivated.status_code == 200
    assert api.get(f"/v1/workspaces/{client}", headers=owner.headers).status_code == 200


def test_agency_cannot_manage_another_agencys_client(api, make_user) -> None:
    owner_a, owner_b = make_user(), make_user()
    agency_a = _create(api, owner_a)["tenant_id"]
    agency_b = _create(api, owner_b)["tenant_id"]
    client_a = api.post(
        f"/v1/workspaces/{agency_a}/clients", json={"display_name": "C"}, headers=owner_a.headers
    ).json()["tenant_id"]

    response = api.post(
        f"/v1/workspaces/{agency_b}/clients/{client_a}/status",
        json={"status": "suspended"},
        headers=owner_b.headers,
    )

    assert response.status_code == 404
    assert _code(response) == "client_not_found"


# --- Roles and permissions -------------------------------------------------------


def test_member_role_is_limited(api, make_user) -> None:
    owner, member = make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, agency, member, "member")

    assert api.get(f"/v1/workspaces/{agency}", headers=member.headers).status_code == 200
    assert api.get(f"/v1/workspaces/{agency}/members", headers=member.headers).status_code == 200
    for method, path, body in [
        ("patch", f"/v1/workspaces/{agency}", {"display_name": "x"}),
        ("post", f"/v1/workspaces/{agency}/clients", {"display_name": "x"}),
        (
            "post",
            f"/v1/workspaces/{agency}/invitations",
            {"email": "a@example.com", "role": "member"},
        ),
        ("get", f"/v1/workspaces/{agency}/audit", None),
    ]:
        assert api.request(method, path, json=body, headers=member.headers).status_code == 403
    denials = [
        e
        for e in api.get(f"/v1/workspaces/{agency}/audit", headers=owner.headers).json()["items"]
        if e["action"] == "api.access_denied"
    ]
    assert denials and all(e["actor_user_id"] == str(member.user_id) for e in denials)


def test_admin_cannot_escalate_to_or_act_on_owner(api, make_user) -> None:
    owner, admin, other = make_user(), make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, agency, admin, "admin")
    _invite_and_accept(api, owner, agency, other, "member")
    owner_membership = _member_id(api, owner, agency, owner)
    other_membership = _member_id(api, owner, agency, other)

    invite_owner = api.post(
        f"/v1/workspaces/{agency}/invitations",
        json={"email": "boss@example.com", "role": "owner"},
        headers=admin.headers,
    )
    promote = api.put(
        f"/v1/workspaces/{agency}/members/{other_membership}/role",
        json={"role": "owner"},
        headers=admin.headers,
    )
    demote_owner = api.put(
        f"/v1/workspaces/{agency}/members/{owner_membership}/role",
        json={"role": "member"},
        headers=admin.headers,
    )
    suspend_owner = api.post(
        f"/v1/workspaces/{agency}/members/{owner_membership}/status",
        json={"action": "suspend"},
        headers=admin.headers,
    )
    self_promote = api.put(
        f"/v1/workspaces/{agency}/members/{_member_id(api, owner, agency, admin)}/role",
        json={"role": "owner"},
        headers=admin.headers,
    )

    assert (invite_owner.status_code, _code(invite_owner)) == (403, "role_grant_not_authorized")
    assert (promote.status_code, _code(promote)) == (403, "owner_protected")
    assert (demote_owner.status_code, _code(demote_owner)) == (403, "owner_protected")
    assert (suspend_owner.status_code, _code(suspend_owner)) == (403, "owner_protected")
    assert (self_promote.status_code, _code(self_promote)) == (403, "cannot_manage_self")
    roles = {
        m["user_id"]: m["roles"]
        for m in api.get(f"/v1/workspaces/{agency}/members", headers=owner.headers).json()["items"]
    }
    assert roles[str(owner.user_id)] == ["owner"]
    assert roles[str(other.user_id)] == ["member"]
    assert roles[str(admin.user_id)] == ["admin"]


def test_admin_cannot_act_on_an_owner_even_when_other_owners_remain(api, make_user) -> None:
    """Isolates the owner guard from the last-owner rule: with two owners,
    removing one would not orphan the workspace, yet an admin must still be
    refused."""
    owner, co_owner, admin = make_user(), make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, agency, co_owner, "owner")
    _invite_and_accept(api, owner, agency, admin, "admin")
    co_owner_membership = _member_id(api, owner, agency, co_owner)

    demote = api.put(
        f"/v1/workspaces/{agency}/members/{co_owner_membership}/role",
        json={"role": "member"},
        headers=admin.headers,
    )
    suspend = api.post(
        f"/v1/workspaces/{agency}/members/{co_owner_membership}/status",
        json={"action": "suspend"},
        headers=admin.headers,
    )

    assert (demote.status_code, _code(demote)) == (403, "owner_protected")
    assert (suspend.status_code, _code(suspend)) == (403, "owner_protected")
    co_owner_row = [
        m
        for m in api.get(f"/v1/workspaces/{agency}/members", headers=owner.headers).json()["items"]
        if m["membership_id"] == co_owner_membership
    ]
    assert co_owner_row[0]["roles"] == ["owner"] and co_owner_row[0]["status"] == "active"


def test_owner_changes_roles_and_member_status_takes_effect(api, make_user) -> None:
    owner, colleague = make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, agency, colleague, "member")
    membership = _member_id(api, owner, agency, colleague)

    promoted = api.put(
        f"/v1/workspaces/{agency}/members/{membership}/role",
        json={"role": "admin"},
        headers=owner.headers,
    )
    assert promoted.status_code == 200 and promoted.json()["roles"] == ["admin"]
    assert api.get(f"/v1/workspaces/{agency}/audit", headers=colleague.headers).status_code == 200

    suspended = api.post(
        f"/v1/workspaces/{agency}/members/{membership}/status",
        json={"action": "suspend"},
        headers=owner.headers,
    )
    assert suspended.json()["status"] == "suspended"
    assert api.get(f"/v1/workspaces/{agency}", headers=colleague.headers).status_code == 403
    assert agency not in {
        w["tenant_id"] for w in api.get("/v1/workspaces", headers=colleague.headers).json()["items"]
    }

    api.post(
        f"/v1/workspaces/{agency}/members/{membership}/status",
        json={"action": "reactivate"},
        headers=owner.headers,
    )
    assert api.get(f"/v1/workspaces/{agency}", headers=colleague.headers).status_code == 200

    revoked = api.post(
        f"/v1/workspaces/{agency}/members/{membership}/status",
        json={"action": "revoke"},
        headers=owner.headers,
    )
    assert revoked.json()["status"] == "revoked"
    assert api.get(f"/v1/workspaces/{agency}", headers=colleague.headers).status_code == 403
    again = api.post(
        f"/v1/workspaces/{agency}/members/{membership}/status",
        json={"action": "reactivate"},
        headers=owner.headers,
    )
    assert again.status_code == 409


def test_second_owner_can_demote_first_and_owner_cannot_manage_self(api, make_user) -> None:
    first, second = make_user(), make_user()
    agency = _create(api, first)["tenant_id"]
    _invite_and_accept(api, first, agency, second, "owner")
    first_membership = _member_id(api, first, agency, first)

    self_change = api.post(
        f"/v1/workspaces/{agency}/members/{first_membership}/status",
        json={"action": "revoke"},
        headers=first.headers,
    )
    demote = api.put(
        f"/v1/workspaces/{agency}/members/{first_membership}/role",
        json={"role": "member"},
        headers=second.headers,
    )

    assert (self_change.status_code, _code(self_change)) == (403, "cannot_manage_self")
    assert demote.status_code == 200 and demote.json()["roles"] == ["member"]


# --- Invitations -------------------------------------------------------------------


def test_invitation_is_single_use_and_tenant_bound(api, make_user) -> None:
    owner, invitee, intruder = make_user(), make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    other = _create(api, make_user(), display_name="Other")["tenant_id"]
    created = api.post(
        f"/v1/workspaces/{agency}/invitations",
        json={"email": "new.hire@example.com", "role": "admin"},
        headers=owner.headers,
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    token = created.json()["token"]

    wrong_tenant = api.post(
        "/v1/invitations/accept", json={"tenant_id": other, "token": token}, headers=invitee.headers
    )
    forged = api.post(
        "/v1/invitations/accept",
        json={"tenant_id": agency, "token": "x" * 43},
        headers=invitee.headers,
    )
    accepted = api.post(
        "/v1/invitations/accept",
        json={"tenant_id": agency, "token": token},
        headers=invitee.headers,
    )
    replay = api.post(
        "/v1/invitations/accept",
        json={"tenant_id": agency, "token": token},
        headers=intruder.headers,
    )

    assert (wrong_tenant.status_code, _code(wrong_tenant)) == (400, "invitation_invalid")
    assert (forged.status_code, _code(forged)) == (400, "invitation_invalid")
    assert accepted.status_code == 200 and accepted.json()["role"] == "admin"
    assert (replay.status_code, _code(replay)) == (400, "invitation_invalid")
    assert api.get(f"/v1/workspaces/{agency}", headers=intruder.headers).status_code == 404
    listed = api.get(f"/v1/workspaces/{agency}/invitations", headers=owner.headers).json()["items"]
    assert [(i["email"], i["status"], i["role"]) for i in listed] == [
        ("new.hire@example.com", "accepted", "admin")
    ]
    assert "token" not in listed[0]


def test_revoked_invitation_cannot_be_accepted(api, make_user) -> None:
    owner, invitee = make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    created = api.post(
        f"/v1/workspaces/{agency}/invitations",
        json={"email": "late@example.com", "role": "member"},
        headers=owner.headers,
    ).json()

    cancelled = api.delete(
        f"/v1/workspaces/{agency}/invitations/{created['invitation']['invitation_id']}",
        headers=owner.headers,
    )
    accept = api.post(
        "/v1/invitations/accept",
        json={"tenant_id": agency, "token": created["token"]},
        headers=invitee.headers,
    )

    assert cancelled.status_code == 204
    assert accept.status_code == 400
    assert api.get(f"/v1/workspaces/{agency}", headers=invitee.headers).status_code == 404


def test_invitation_tokens_never_reach_the_audit_trail(api, make_user) -> None:
    owner = make_user()
    agency = _create(api, owner)["tenant_id"]
    token = api.post(
        f"/v1/workspaces/{agency}/invitations",
        json={"email": "a@example.com", "role": "member"},
        headers=owner.headers,
    ).json()["token"]

    from core import audit_log

    entries = audit_log.list(uuid.UUID(agency), limit=100)
    assert entries
    assert all(token not in repr(entry.entry_metadata) for entry in entries)
    assert all(token not in (entry.resource_id or "") for entry in entries)


# --- Audit trail -------------------------------------------------------------------


def test_audit_pagination_is_complete_and_stable(api, make_user) -> None:
    owner = make_user()
    agency = _create(api, owner)["tenant_id"]
    for i in range(3):
        api.patch(f"/v1/workspaces/{agency}", json={"display_name": f"n{i}"}, headers=owner.headers)
    everything = api.get(f"/v1/workspaces/{agency}/audit?limit=100", headers=owner.headers).json()

    seen, cursor = [], None
    while True:
        url = f"/v1/workspaces/{agency}/audit?limit=1" + (f"&cursor={cursor}" if cursor else "")
        page = api.get(url, headers=owner.headers).json()
        seen.extend(e["entry_id"] for e in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == [e["entry_id"] for e in everything["items"]]
    assert len(seen) == len(set(seen)) >= 4
    bad = api.get(f"/v1/workspaces/{agency}/audit?cursor=not-a-cursor", headers=owner.headers)
    assert (bad.status_code, _code(bad)) == (422, "invalid_cursor")


# --- Abuse limits ----------------------------------------------------------------------


def test_workspace_creation_is_rate_limited_per_user(api, make_user) -> None:
    user = make_user()
    statuses = [
        api.post(
            "/v1/workspaces", json={**AGENCY, "display_name": f"w{i}"}, headers=user.headers
        ).status_code
        for i in range(11)
    ]

    assert statuses[:10] == [201] * 10
    assert statuses[10] == 429
    # A different user is unaffected.
    assert api.post("/v1/workspaces", json=AGENCY, headers=make_user().headers).status_code == 201
