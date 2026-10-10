"""Regression tests for the Phase 4 independent-review findings
(docs/audits/phase-4.md)."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import psycopg
import pytest
from core import audit_log
from core.tenancy import TenantStatus, purge_tenant, transition_tenant_status
from infra.db import select, tenant_session_scope

from leadstack.domains.crm.activity_models import ContactTag, CustomFieldDefinition, Note, Tag, Task
from leadstack.domains.crm.models import Contact
from leadstack.domains.sales.models import Deal, Pipeline
from tests.integration.test_crm import _contacts
from tests.integration.test_workspaces import _code, _create, _invite_and_accept

pytestmark = pytest.mark.integration


def _w(tenant: str) -> str:
    return f"/v1/workspaces/{tenant}"


@pytest.fixture
def ws(api, make_user):
    owner, member = make_user(), make_user()
    tenant = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, tenant, member, "member")
    contact = api.post(_contacts(tenant), json={"first_name": "Lina"}, headers=owner.headers).json()
    return owner, member, tenant, contact["contact_id"]


def test_recreated_custom_field_never_resurfaces_old_values(api, ws) -> None:
    owner, _, tenant, contact = ws
    definition = api.post(
        f"{_w(tenant)}/custom-fields",
        json={"key": "budget", "label": "B", "field_type": "text"},
        headers=owner.headers,
    ).json()
    api.patch(
        f"{_contacts(tenant)}/{contact}/custom-fields",
        json={"values": {"budget": "call me"}},
        headers=owner.headers,
    )
    api.delete(f"{_w(tenant)}/custom-fields/{definition['field_id']}", headers=owner.headers)

    api.post(
        f"{_w(tenant)}/custom-fields",
        json={"key": "budget", "label": "B", "field_type": "text"},
        headers=owner.headers,
    )
    detail = api.get(f"{_contacts(tenant)}/{contact}/detail", headers=owner.headers).json()

    assert "budget" not in detail["custom_fields"]
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        row = session.get(Contact, uuid.UUID(contact))
        assert row is not None
        stored = row.custom_fields
    assert "budget" not in stored  # erased, not merely hidden


def test_removed_select_option_is_hidden(api, ws) -> None:
    owner, _, tenant, contact = ws
    api.post(
        f"{_w(tenant)}/custom-fields",
        json={"key": "seg", "label": "S", "field_type": "select", "options": ["A", "B"]},
        headers=owner.headers,
    )
    api.patch(
        f"{_contacts(tenant)}/{contact}/custom-fields",
        json={"values": {"seg": "B"}},
        headers=owner.headers,
    )
    with tenant_session_scope(uuid.UUID(tenant)) as session:  # option "B" removed out of band
        row = session.execute(
            select(CustomFieldDefinition).where(CustomFieldDefinition.key == "seg")
        ).scalar_one()
        row.options = ["A"]

    detail = api.get(f"{_contacts(tenant)}/{contact}/detail", headers=owner.headers).json()

    assert "seg" not in detail["custom_fields"]


def test_number_precision_is_rejected_not_rounded(api, ws) -> None:
    owner, _, tenant, contact = ws
    api.post(
        f"{_w(tenant)}/custom-fields",
        json={"key": "n", "label": "N", "field_type": "number"},
        headers=owner.headers,
    )
    url = f"{_contacts(tenant)}/{contact}/custom-fields"

    too_precise = api.patch(
        url, json={"values": {"n": "0.12345678901234567890123456789012"}}, headers=owner.headers
    )
    too_long = api.patch(url, json={"values": {"n": "1" * 65}}, headers=owner.headers)

    assert too_precise.status_code == too_long.status_code == 422


def test_bulk_delete_audits_every_contact(api, ws) -> None:
    owner, _, tenant, first = ws
    second = api.post(_contacts(tenant), json={"first_name": "B"}, headers=owner.headers).json()[
        "contact_id"
    ]

    api.post(
        f"{_contacts(tenant)}/bulk",
        json={"action": "delete", "contact_ids": [first, second]},
        headers=owner.headers,
    )

    deleted = {
        e.resource_id
        for e in audit_log.list(uuid.UUID(tenant), limit=200)
        if e.action == "leadstack.contact.deleted"
    }
    assert {first, second} <= deleted


def test_concurrent_identical_tagging_is_idempotent(api, ws) -> None:
    owner, _, tenant, contact = ws
    tag = api.post(f"{_w(tenant)}/tags", json={"name": "Hot"}, headers=owner.headers).json()[
        "tag_id"
    ]

    def tag_it(_: int) -> int:
        return api.put(
            f"{_contacts(tenant)}/{contact}/tags/{tag}", headers=owner.headers
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(tag_it, range(8)))

    assert statuses == [200] * 8


def test_task_changes_limited_to_participants(api, ws, make_user) -> None:
    owner, member, tenant, _ = ws
    other = make_user()
    _invite_and_accept(api, owner, tenant, other, "member")
    task = api.post(f"{_w(tenant)}/tasks", json={"title": "Mine"}, headers=member.headers).json()

    stranger_edit = api.patch(
        f"{_w(tenant)}/tasks/{task['task_id']}", json={"title": "x"}, headers=other.headers
    )
    stranger_delete = api.delete(f"{_w(tenant)}/tasks/{task['task_id']}", headers=other.headers)
    assert (stranger_edit.status_code, _code(stranger_edit)) == (403, "not_task_participant")
    assert stranger_delete.status_code == 403

    # Assigning it to `other` makes them a participant.
    api.patch(
        f"{_w(tenant)}/tasks/{task['task_id']}",
        json={"assignee_user_id": str(other.user_id)},
        headers=member.headers,
    )
    assert (
        api.patch(
            f"{_w(tenant)}/tasks/{task['task_id']}", json={"status": "done"}, headers=other.headers
        ).status_code
        == 200
    )
    # Admins (contacts:delete) may manage any task.
    assert (
        api.delete(f"{_w(tenant)}/tasks/{task['task_id']}", headers=owner.headers).status_code
        == 204
    )


def test_member_cannot_manage_tags(api, ws) -> None:
    owner, member, tenant, _ = ws
    tag = api.post(f"{_w(tenant)}/tags", json={"name": "T"}, headers=owner.headers).json()["tag_id"]

    assert (
        api.post(f"{_w(tenant)}/tags", json={"name": "U"}, headers=member.headers).status_code
        == 403
    )
    assert api.delete(f"{_w(tenant)}/tags/{tag}", headers=member.headers).status_code == 403


def test_foreign_contact_timeline_and_notes_are_not_found(api, ws, make_user) -> None:
    owner, _, tenant, contact = ws
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]

    timeline = api.get(f"{_contacts(other)}/{contact}/timeline", headers=other_owner.headers)
    note = api.post(
        f"{_contacts(other)}/{contact}/notes", json={"body": "x"}, headers=other_owner.headers
    )
    foreign_tag_filter = api.get(
        _contacts(tenant), params={"tag_id": str(uuid.uuid4())}, headers=owner.headers
    )

    assert timeline.status_code == note.status_code == 404
    assert foreign_tag_filter.json()["items"] == []


@pytest.mark.parametrize(
    "cursor",
    ["garbage", "eyJhIjoiMjAyNi0wMS0wMSJ9", "eyJhIjoiMjAyNi0wMS0wMVQwMDowMDowMCIsImkiOiJ4In0"],
)
def test_tampered_timeline_cursor_is_rejected(api, ws, cursor) -> None:
    owner, _, tenant, contact = ws
    response = api.get(
        f"{_contacts(tenant)}/{contact}/timeline", params={"cursor": cursor}, headers=owner.headers
    )
    assert (response.status_code, _code(response)) == (422, "invalid_cursor")


def test_timeline_with_many_identical_timestamps(api, ws) -> None:
    """More items than one page share one instant: every item appears once."""
    owner, _, tenant, contact = ws
    tenant_id, contact_id = uuid.UUID(tenant), uuid.UUID(contact)
    at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    with tenant_session_scope(tenant_id) as session:
        session.add_all(
            Note(
                tenant_id=tenant_id,
                contact_id=contact_id,
                body=f"n{i}",
                author_user_id=owner.user_id,
                created_at=at,
                updated_at=at,
            )
            for i in range(9)
        )
        session.add_all(
            Task(tenant_id=tenant_id, contact_id=contact_id, title=f"t{i}", status="open",
                 created_by_user_id=owner.user_id, created_at=at, updated_at=at)
            for i in range(6)
        )  # fmt: skip

    seen, cursor = [], None
    while True:
        params = {"limit": 4} | ({"cursor": cursor} if cursor else {})
        page = api.get(
            f"{_contacts(tenant)}/{contact}/timeline", params=params, headers=owner.headers
        ).json()
        seen += [i["item_id"] for i in page["items"] if i["item_type"] != "event"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 15


def test_database_refuses_cross_tenant_links(api, ws, make_user, services) -> None:
    """Composite (tenant_id, id) foreign keys: even with RLS bypassed (as the
    schema owner), a row cannot point at another workspace's contact."""
    owner, _, _, contact = ws
    other = _create(api, make_user(), display_name="Other")["tenant_id"]
    statements = [
        ("INSERT INTO leadstack.notes (id, tenant_id, contact_id, body, author_user_id) "
         "VALUES (%s, %s, %s, 'x', %s)", (uuid.uuid4(), other, contact, owner.user_id)),
        ("INSERT INTO leadstack.tasks (id, tenant_id, contact_id, title, created_by_user_id) "
         "VALUES (%s, %s, %s, 'x', %s)", (uuid.uuid4(), other, contact, owner.user_id)),
    ]  # fmt: skip
    for sql, params in statements:
        with (
            psycopg.connect(services.admin_url) as conn,
            pytest.raises(psycopg.errors.ForeignKeyViolation),
        ):
            conn.execute(sql, params)  # type: ignore[arg-type]


def test_purge_erases_every_crm_and_sales_table(api, ws) -> None:
    owner, _, tenant, contact = ws
    tag = api.post(f"{_w(tenant)}/tags", json={"name": "P"}, headers=owner.headers).json()["tag_id"]
    api.put(f"{_contacts(tenant)}/{contact}/tags/{tag}", headers=owner.headers)
    api.post(f"{_contacts(tenant)}/{contact}/notes", json={"body": "n"}, headers=owner.headers)
    api.post(
        f"{_w(tenant)}/tasks", json={"title": "t", "contact_id": contact}, headers=owner.headers
    )
    api.post(
        f"{_w(tenant)}/custom-fields",
        json={"key": "k", "label": "K", "field_type": "text"},
        headers=owner.headers,
    )
    pipeline = api.post(
        f"{_w(tenant)}/pipelines", json={"name": "Sales"}, headers=owner.headers
    ).json()
    api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "D", "contact_id": contact, "amount": "100", "currency": "MAD"},
        headers=owner.headers,
    )
    tenant_id = uuid.UUID(tenant)

    transition_tenant_status(tenant_id, TenantStatus.DELETED, actor_user_id=owner.user_id)
    purge_tenant(tenant_id, actor_user_id=owner.user_id)

    with tenant_session_scope(tenant_id) as session:
        for model in (ContactTag, Tag, Note, Task, CustomFieldDefinition, Deal, Pipeline, Contact):
            assert session.execute(select(model)).scalars().all() == [], model.__name__
