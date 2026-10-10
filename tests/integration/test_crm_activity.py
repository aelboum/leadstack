"""Phase 4: tags, custom fields, notes, tasks, timeline and bulk actions."""

from __future__ import annotations

import uuid

import pytest
from core import audit_log

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


# --- Tags ---------------------------------------------------------------------------


def test_tags_lifecycle_and_filtering(api, ws) -> None:
    owner, member, tenant, contact = ws
    vip = api.post(f"{_w(tenant)}/tags", json={"name": "VIP"}, headers=owner.headers)
    assert vip.status_code == 201
    tag = vip.json()["tag_id"]
    dup = api.post(f"{_w(tenant)}/tags", json={"name": "vip"}, headers=owner.headers)
    assert (dup.status_code, _code(dup)) == (409, "tag_exists")
    assert (
        api.post(f"{_w(tenant)}/tags", json={"name": "x"}, headers=member.headers).status_code
        == 403
    )

    tagged = api.put(f"{_contacts(tenant)}/{contact}/tags/{tag}", headers=member.headers)
    again = api.put(f"{_contacts(tenant)}/{contact}/tags/{tag}", headers=member.headers)
    assert tagged.json() == again.json() == [{"tag_id": tag, "name": "VIP"}]  # idempotent
    other = api.post(_contacts(tenant), json={"first_name": "Other"}, headers=owner.headers)
    filtered = api.get(_contacts(tenant), params={"tag_id": tag}, headers=owner.headers).json()
    assert [c["contact_id"] for c in filtered["items"]] == [contact]
    assert other.status_code == 201

    detail = api.get(f"{_contacts(tenant)}/{contact}/detail", headers=member.headers).json()
    assert detail["tags"] == [{"tag_id": tag, "name": "VIP"}]

    assert api.delete(f"{_w(tenant)}/tags/{tag}", headers=owner.headers).status_code == 204
    detail = api.get(f"{_contacts(tenant)}/{contact}/detail", headers=member.headers).json()
    assert detail["tags"] == []  # links cascade with the tag


def test_tags_are_isolated_between_workspaces(api, ws, make_user) -> None:
    owner, _, tenant, contact = ws
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]
    foreign_tag = api.post(
        f"{_w(other)}/tags", json={"name": "Theirs"}, headers=other_owner.headers
    ).json()

    response = api.put(
        f"{_contacts(tenant)}/{contact}/tags/{foreign_tag['tag_id']}", headers=owner.headers
    )
    bulk = api.post(
        f"{_contacts(tenant)}/bulk",
        json={"action": "tag", "contact_ids": [contact], "tag_id": foreign_tag["tag_id"]},
        headers=owner.headers,
    )

    assert (response.status_code, _code(response)) == (404, "tag_not_found")
    assert bulk.status_code == 404
    assert api.get(f"{_w(other)}/tags", headers=owner.headers).status_code == 404


# --- Custom fields ------------------------------------------------------------------


def test_custom_fields_validate_and_store_safely(api, ws) -> None:
    owner, member, tenant, contact = ws
    definitions = [
        {"key": "budget", "label": "Budget", "field_type": "number"},
        {"key": "birthday", "label": "Anniversaire", "field_type": "date"},
        {"key": "newsletter", "label": "Newsletter", "field_type": "boolean"},
        {"key": "segment", "label": "Segment", "field_type": "select", "options": ["A", "B"]},
        {"key": "notes_ar", "label": "ملاحظات", "field_type": "text"},
    ]
    for d in definitions:
        assert (
            api.post(f"{_w(tenant)}/custom-fields", json=d, headers=owner.headers).status_code
            == 201
        )
    assert (
        api.post(
            f"{_w(tenant)}/custom-fields", json=definitions[0], headers=owner.headers
        ).status_code
        == 409
    )
    url = f"{_contacts(tenant)}/{contact}/custom-fields"

    stored = api.patch(
        url,
        json={"values": {"budget": "1500.50", "birthday": "1990-04-02", "newsletter": True,
                         "segment": "B", "notes_ar": "عميل مهم"}},
        headers=member.headers,
    )  # fmt: skip

    assert stored.status_code == 200, stored.text
    assert stored.json() == {
        "budget": "1500.5",
        "birthday": "1990-04-02",
        "newsletter": True,
        "segment": "B",
        "notes_ar": "عميل مهم",
    }
    for bad in (
        {"budget": 1.5},  # floats are refused: numbers travel as strings/integers
        {"budget": "abc"},
        {"birthday": "02/04/1990"},
        {"segment": "C"},
        {"newsletter": "yes"},
        {"unknown_key": "x"},
    ):
        response = api.patch(url, json={"values": bad}, headers=member.headers)
        assert response.status_code == 422, bad
    cleared = api.patch(url, json={"values": {"segment": None}}, headers=member.headers).json()
    assert "segment" not in cleared

    # Deleting a definition hides its stored values immediately.
    field_id = next(
        d["field_id"]
        for d in api.get(f"{_w(tenant)}/custom-fields", headers=member.headers).json()
        if d["key"] == "budget"
    )
    assert (
        api.delete(f"{_w(tenant)}/custom-fields/{field_id}", headers=owner.headers).status_code
        == 204
    )
    detail = api.get(f"{_contacts(tenant)}/{contact}/detail", headers=owner.headers).json()
    assert "budget" not in detail["custom_fields"]
    # Field values never reach the audit trail.
    for entry in audit_log.list(uuid.UUID(tenant), limit=200):
        assert "عميل مهم" not in repr(entry.entry_metadata) and "1500" not in repr(
            entry.entry_metadata
        )


def test_invalid_definitions_and_member_cannot_define(api, ws) -> None:
    owner, member, tenant, _ = ws
    for bad in (
        {"key": "Bad Key", "label": "x", "field_type": "text"},
        {"key": "s", "label": "x", "field_type": "select"},
        {"key": "t", "label": "x", "field_type": "text", "options": ["a"]},
        {"key": "s2", "label": "x", "field_type": "select", "options": ["a", "a"]},
    ):
        assert (
            api.post(f"{_w(tenant)}/custom-fields", json=bad, headers=owner.headers).status_code
            == 422
        )
    ok = {"key": "k", "label": "K", "field_type": "text"}
    assert (
        api.post(f"{_w(tenant)}/custom-fields", json=ok, headers=member.headers).status_code == 403
    )


# --- Notes --------------------------------------------------------------------------


def test_notes_authorship_rules(api, ws, make_user) -> None:
    owner, member, tenant, contact = ws
    notes = f"{_contacts(tenant)}/{contact}/notes"
    mine = api.post(
        notes, json={"body": "Appel le 3/10 — intéressée"}, headers=member.headers
    ).json()
    theirs = api.post(notes, json={"body": "Owner note"}, headers=owner.headers).json()

    assert (
        api.patch(
            f"{notes}/{mine['note_id']}", json={"body": "edited"}, headers=member.headers
        ).json()["body"]
        == "edited"
    )
    edit_other = api.patch(
        f"{notes}/{theirs['note_id']}", json={"body": "x"}, headers=member.headers
    )
    delete_other = api.delete(f"{notes}/{theirs['note_id']}", headers=member.headers)
    assert (edit_other.status_code, _code(edit_other)) == (403, "not_note_author")
    assert delete_other.status_code == 403
    # Admin-level users (contacts:delete) may delete anyone's note, but not edit it.
    assert api.delete(f"{notes}/{mine['note_id']}", headers=owner.headers).status_code == 204
    assert api.post(notes, json={"body": "   "}, headers=member.headers).status_code == 422
    # A note under another contact's path is not found.
    other = api.post(_contacts(tenant), json={"first_name": "B"}, headers=owner.headers).json()
    wrong = api.patch(
        f"{_contacts(tenant)}/{other['contact_id']}/notes/{theirs['note_id']}",
        json={"body": "x"},
        headers=owner.headers,
    )
    assert wrong.status_code == 404


def test_notes_cascade_with_contact_and_body_not_audited(api, ws) -> None:
    owner, _, tenant, contact = ws
    secret = f"secret-{uuid.uuid4()}"
    api.post(f"{_contacts(tenant)}/{contact}/notes", json={"body": secret}, headers=owner.headers)

    api.delete(f"{_contacts(tenant)}/{contact}", headers=owner.headers)

    assert (
        api.get(f"{_contacts(tenant)}/{contact}/timeline", headers=owner.headers).status_code == 404
    )
    assert all(
        secret not in repr(e.entry_metadata) for e in audit_log.list(uuid.UUID(tenant), limit=200)
    )


# --- Tasks --------------------------------------------------------------------------


def test_tasks_assignment_completion_and_listing(api, ws, make_user) -> None:
    owner, member, tenant, contact = ws
    tasks = f"{_w(tenant)}/tasks"
    created = api.post(
        tasks,
        json={"title": "Rappeler", "due_at": "2026-10-12T09:00:00+01:00", "contact_id": contact,
              "assignee_user_id": str(member.user_id)},
        headers=owner.headers,
    )  # fmt: skip
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["due_at"].startswith("2026-10-12T08:00:00")  # stored as an instant (UTC)
    naive = api.post(
        tasks, json={"title": "x", "due_at": "2026-10-12T09:00:00"}, headers=owner.headers
    )
    outsider = api.post(
        tasks,
        json={"title": "x", "assignee_user_id": str(make_user().user_id)},
        headers=owner.headers,
    )
    assert naive.status_code == 422
    assert (outsider.status_code, _code(outsider)) == (422, "invalid_assignee")

    mine = api.get(
        tasks, params={"assignee": "me", "status": "open"}, headers=member.headers
    ).json()
    assert [t["task_id"] for t in mine] == [task["task_id"]]
    done = api.patch(
        f"{tasks}/{task['task_id']}", json={"status": "done"}, headers=member.headers
    ).json()
    assert done["status"] == "done" and done["completed_at"] is not None
    reopened = api.patch(
        f"{tasks}/{task['task_id']}", json={"status": "open"}, headers=member.headers
    ).json()
    assert reopened["completed_at"] is None

    # Deleting the contact keeps the task, unlinked.
    api.delete(f"{_contacts(tenant)}/{contact}", headers=owner.headers)
    remaining = api.get(tasks, headers=owner.headers).json()
    assert [(t["task_id"], t["contact_id"]) for t in remaining] == [(task["task_id"], None)]


def test_tasks_are_isolated(api, ws, make_user) -> None:
    owner, _, tenant, contact = ws
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]
    task = api.post(f"{_w(tenant)}/tasks", json={"title": "Private"}, headers=owner.headers).json()

    cross_link = api.post(
        f"{_w(other)}/tasks",
        json={"title": "x", "contact_id": contact},
        headers=other_owner.headers,
    )
    cross_edit = api.patch(
        f"{_w(other)}/tasks/{task['task_id']}", json={"title": "x"}, headers=other_owner.headers
    )

    assert (cross_link.status_code, _code(cross_link)) == (404, "contact_not_found")
    assert cross_edit.status_code == 404
    assert api.get(f"{_w(other)}/tasks", headers=other_owner.headers).json() == []


# --- Timeline -------------------------------------------------------------------------


def test_timeline_merges_notes_tasks_and_events(api, ws) -> None:
    owner, _, tenant, contact = ws
    api.patch(f"{_contacts(tenant)}/{contact}", json={"last_name": "Alaoui"}, headers=owner.headers)
    api.post(f"{_contacts(tenant)}/{contact}/notes", json={"body": "n1"}, headers=owner.headers)
    api.post(
        f"{_w(tenant)}/tasks", json={"title": "t1", "contact_id": contact}, headers=owner.headers
    )
    api.post(f"{_contacts(tenant)}/{contact}/notes", json={"body": "n2"}, headers=owner.headers)

    full = api.get(
        f"{_contacts(tenant)}/{contact}/timeline?limit=100", headers=owner.headers
    ).json()
    kinds = [i["item_type"] for i in full["items"]]
    assert kinds.count("note") == 2 and kinds.count("task") == 1 and "event" in kinds
    actions = {i["action"] for i in full["items"] if i["item_type"] == "event"}
    assert {"leadstack.contact.created", "leadstack.contact.updated"} <= actions
    ats = [i["at"] for i in full["items"]]
    assert ats == sorted(ats, reverse=True)

    seen, cursor = [], None
    while True:
        url = f"{_contacts(tenant)}/{contact}/timeline?limit=2" + (
            f"&cursor={cursor}" if cursor else ""
        )
        page = api.get(url, headers=owner.headers).json()
        seen += [i["item_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [i["item_id"] for i in full["items"]]


# --- Bulk ---------------------------------------------------------------------------


def test_bulk_tag_untag_and_delete(api, ws, make_user) -> None:
    owner, member, tenant, first = ws
    ids = [first] + [
        api.post(_contacts(tenant), json={"first_name": f"B{i}"}, headers=owner.headers).json()[
            "contact_id"
        ]
        for i in range(4)
    ]
    tag = api.post(f"{_w(tenant)}/tags", json={"name": "Batch"}, headers=owner.headers).json()[
        "tag_id"
    ]
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]
    foreign = api.post(
        _contacts(other), json={"first_name": "Foreign"}, headers=other_owner.headers
    ).json()["contact_id"]
    url = f"{_contacts(tenant)}/bulk"

    tagged = api.post(
        url,
        json={"action": "tag", "contact_ids": [*ids, foreign], "tag_id": tag},
        headers=member.headers,
    )
    assert tagged.json() == {"requested": 6, "affected": 5}
    assert (
        api.post(
            url, json={"action": "tag", "contact_ids": ids, "tag_id": tag}, headers=member.headers
        ).json()["affected"]
        == 0
    )
    untagged = api.post(
        url, json={"action": "untag", "contact_ids": ids[:2], "tag_id": tag}, headers=member.headers
    )
    assert untagged.json()["affected"] == 2

    member_delete = api.post(
        url, json={"action": "delete", "contact_ids": ids}, headers=member.headers
    )
    assert member_delete.status_code == 403
    deleted = api.post(
        url, json={"action": "delete", "contact_ids": [*ids, foreign]}, headers=owner.headers
    )
    assert deleted.json() == {"requested": 6, "affected": 5}
    assert api.get(f"{_contacts(other)}/{foreign}", headers=other_owner.headers).status_code == 200
    too_many = api.post(
        url,
        json={"action": "delete", "contact_ids": [str(uuid.uuid4())] * 501},
        headers=owner.headers,
    )
    assert too_many.status_code == 422
    entries = [
        e
        for e in audit_log.list(uuid.UUID(tenant), limit=200)
        if e.action == "leadstack.contacts.bulk_delete"
    ]
    assert entries and entries[0].entry_metadata == {"requested": 6, "affected": 5, "tag_id": None}
