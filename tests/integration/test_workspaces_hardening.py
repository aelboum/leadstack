"""Regression tests for the Phase 2 independent-review findings
(docs/audits/phase-2.md): purge, invitation edge cases, owner races,
cross-tenant identifiers and secret handling."""

from __future__ import annotations

import threading
import uuid

import pytest
from core.tenancy import TenantStatus, purge_tenant, transition_tenant_status
from infra.db import select, session_scope, tenant_session_scope

from leadstack.domains.crm.models import Company, Contact
from leadstack.domains.workspaces import service
from leadstack.domains.workspaces.errors import OwnerProtectionError
from leadstack.domains.workspaces.models import InvitationGrant, UserWorkspace, Workspace
from tests.integration.test_workspaces import AGENCY, _code, _create, _invite_and_accept, _member_id

pytestmark = pytest.mark.integration


def _invite(api, owner, tenant_id: str, role: str) -> dict:
    response = api.post(
        f"/v1/workspaces/{tenant_id}/invitations",
        json={"email": f"{uuid.uuid4().hex[:10]}@example.com", "role": role},
        headers=owner.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _accept(api, user, tenant_id: str, token: str):
    return api.post(
        "/v1/invitations/accept",
        json={"tenant_id": tenant_id, "token": token},
        headers=user.headers,
    )


def test_inviter_who_loses_authority_cannot_confer_the_role(api, make_user) -> None:
    owner, admin, invitee = make_user(), make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, agency, admin, "admin")
    pending = _invite(api, admin, agency, "admin")

    # The admin is demoted before the invitation is redeemed.
    api.put(
        f"/v1/workspaces/{agency}/members/{_member_id(api, owner, agency, admin)}/role",
        json={"role": "member"},
        headers=owner.headers,
    )
    accepted = _accept(api, invitee, agency, pending["token"])

    assert accepted.status_code == 200
    assert accepted.json()["role"] is None
    members = api.get(f"/v1/workspaces/{agency}/members", headers=owner.headers).json()["items"]
    assert [m["roles"] for m in members if m["user_id"] == str(invitee.user_id)] == [[]]
    assert api.get(f"/v1/workspaces/{agency}", headers=invitee.headers).status_code == 403
    actions = [
        e["action"]
        for e in api.get(f"/v1/workspaces/{agency}/audit?limit=100", headers=owner.headers).json()[
            "items"
        ]
    ]
    assert "leadstack.invitation.role_grant_refused" in actions


def test_cancelling_an_accepted_invitation_is_a_conflict(api, make_user) -> None:
    owner = make_user()
    agency = _create(api, owner)["tenant_id"]
    done = _invite_and_accept(api, owner, agency, make_user(), "member")

    response = api.delete(
        f"/v1/workspaces/{agency}/invitations/{done['invitation']['invitation']['invitation_id']}",
        headers=owner.headers,
    )

    assert (response.status_code, _code(response)) == (409, "invitation_already_accepted")


def test_cannot_join_a_suspended_client(api, make_user) -> None:
    owner, invitee = make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    client = api.post(
        f"/v1/workspaces/{agency}/clients", json={"display_name": "C"}, headers=owner.headers
    ).json()["tenant_id"]
    pending = _invite(api, owner, client, "member")
    api.post(
        f"/v1/workspaces/{agency}/clients/{client}/status",
        json={"status": "suspended"},
        headers=owner.headers,
    )

    response = _accept(api, invitee, client, pending["token"])

    assert (response.status_code, _code(response)) == (400, "invitation_invalid")


def test_revoked_member_cannot_rejoin_with_a_new_invitation(api, make_user) -> None:
    owner, former = make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, agency, former, "member")
    api.post(
        f"/v1/workspaces/{agency}/members/{_member_id(api, owner, agency, former)}/status",
        json={"action": "revoke"},
        headers=owner.headers,
    )
    pending = _invite(api, owner, agency, "member")

    response = _accept(api, former, agency, pending["token"])

    assert response.status_code == 400
    assert api.get(f"/v1/workspaces/{agency}", headers=former.headers).status_code == 403


def test_existing_member_keeps_their_role_when_accepting_another_invitation(api, make_user) -> None:
    owner = make_user()
    agency = _create(api, owner)["tenant_id"]
    pending = _invite(api, owner, agency, "member")

    response = _accept(api, owner, agency, pending["token"])

    assert response.status_code == 200 and response.json()["role"] == "owner"
    members = api.get(f"/v1/workspaces/{agency}/members", headers=owner.headers).json()["items"]
    assert [m["roles"] for m in members if m["user_id"] == str(owner.user_id)] == [["owner"]]


def test_identifiers_from_another_workspace_are_not_found(api, make_user) -> None:
    owner_a, owner_b = make_user(), make_user()
    a = _create(api, owner_a)["tenant_id"]
    b = _create(api, owner_b)["tenant_id"]
    foreign_member = _member_id(api, owner_a, a, owner_a)
    foreign_invitation = _invite(api, owner_a, a, "member")["invitation"]["invitation_id"]

    role = api.put(
        f"/v1/workspaces/{b}/members/{foreign_member}/role",
        json={"role": "member"},
        headers=owner_b.headers,
    )
    status = api.post(
        f"/v1/workspaces/{b}/members/{foreign_member}/status",
        json={"action": "suspend"},
        headers=owner_b.headers,
    )
    cancel = api.delete(
        f"/v1/workspaces/{b}/invitations/{foreign_invitation}", headers=owner_b.headers
    )

    assert role.status_code == status.status_code == cancel.status_code == 404
    assert api.get(f"/v1/workspaces/{a}", headers=owner_a.headers).status_code == 200


def test_plain_members_cannot_see_invitee_emails(api, make_user) -> None:
    owner, member = make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, agency, member, "member")

    assert (
        api.get(f"/v1/workspaces/{agency}/invitations", headers=member.headers).status_code == 403
    )


def test_concurrent_owner_demotions_never_leave_a_workspace_ownerless(api, make_user) -> None:
    first, second = make_user(), make_user()
    agency = _create(api, first)["tenant_id"]
    _invite_and_accept(api, first, agency, second, "owner")
    tenant = uuid.UUID(agency)
    first_m = uuid.UUID(_member_id(api, first, agency, first))
    second_m = uuid.UUID(_member_id(api, first, agency, second))
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def demote(actor: uuid.UUID, target: uuid.UUID) -> None:
        barrier.wait()
        try:
            service.change_member_role(actor, tenant, target, "member")
            outcomes.append("ok")
        except OwnerProtectionError:
            outcomes.append("refused")

    threads = [
        threading.Thread(target=demote, args=(first.user_id, second_m)),
        threading.Thread(target=demote, args=(second.user_id, first_m)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert sorted(outcomes) == ["ok", "refused"]
    members = api.get(f"/v1/workspaces/{agency}/members", headers=first.headers)
    if members.status_code != 200:  # first was demoted; ask the remaining owner
        members = api.get(f"/v1/workspaces/{agency}/members", headers=second.headers)
    owners = [m for m in members.json()["items"] if "owner" in m["roles"]]
    assert len(owners) == 1


def test_tenant_purge_erases_every_leadstack_row(api, make_user) -> None:
    owner = make_user()
    tenant_id = _create(api, owner)["tenant_id"]
    company = api.post(
        f"/v1/workspaces/{tenant_id}/companies", json={"name": "Acme"}, headers=owner.headers
    ).json()["company_id"]
    api.post(
        f"/v1/workspaces/{tenant_id}/contacts",
        json={"first_name": "Salma", "company_id": company},
        headers=owner.headers,
    )
    _invite(api, owner, tenant_id, "member")
    tenant = uuid.UUID(tenant_id)

    transition_tenant_status(tenant, TenantStatus.DELETED, actor_user_id=owner.user_id)
    result = purge_tenant(tenant, actor_user_id=owner.user_id)

    assert "leadstack" in result.participant_names
    with tenant_session_scope(tenant) as session:
        for model in (Workspace, InvitationGrant, Company, Contact):
            assert session.execute(select(model)).scalars().all() == [], model.__name__
    with session_scope() as session:
        rows = session.execute(select(UserWorkspace).where(UserWorkspace.tenant_id == tenant))
        assert rows.scalars().all() == []
    # Purge is idempotent: re-running is a no-op, not an error.
    purge_tenant(tenant, actor_user_id=owner.user_id)


def test_tokens_never_appear_in_logs_or_later_responses(
    api, make_user, capfd: pytest.CaptureFixture[str]
) -> None:
    owner, invitee = make_user(), make_user()
    agency = _create(api, owner)["tenant_id"]
    pending = _invite(api, owner, agency, "member")
    token = pending["token"]

    accepted = _accept(api, invitee, agency, token)
    replay = _accept(api, invitee, agency, token)
    listing = api.get(f"/v1/workspaces/{agency}/invitations", headers=owner.headers)
    audit = api.get(f"/v1/workspaces/{agency}/audit?limit=100", headers=owner.headers)

    captured = capfd.readouterr()
    for text in (captured.out, captured.err, accepted.text, replay.text, listing.text, audit.text):
        assert token not in text
    assert owner.headers["Authorization"].split()[1] not in captured.out + captured.err


def test_workspace_list_drops_hints_for_closed_workspaces(api, make_user) -> None:
    owner = make_user()
    live = _create(api, owner)["tenant_id"]
    closed = _create(api, owner, display_name="Closed")["tenant_id"]
    transition_tenant_status(uuid.UUID(closed), TenantStatus.DELETED, actor_user_id=owner.user_id)

    listed = [
        w["tenant_id"] for w in api.get("/v1/workspaces", headers=owner.headers).json()["items"]
    ]

    assert listed == [live]
    with session_scope() as session:
        hints = session.execute(
            select(UserWorkspace.tenant_id).where(UserWorkspace.user_id == owner.user_id)
        ).scalars()
        assert set(map(str, hints)) == {live}


def test_unauthenticated_accept_is_rejected(api) -> None:
    response = api.post(
        "/v1/invitations/accept", json={"tenant_id": str(uuid.uuid4()), "token": "x" * 40}
    )
    assert response.status_code == 401
    assert AGENCY  # shared fixture data stays importable
