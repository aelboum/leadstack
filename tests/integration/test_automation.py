"""Phase 7: event-triggered workflow automation."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest
from infra.db import select, tenant_session_scope
from infra.jobs import TenantJobPayload, enqueue_job, get_jobs_config

from leadstack.domains.automation.models import Event, WorkflowRun
from leadstack.domains.automation.service import process_events
from leadstack.domains.forms.service import issue_token
from leadstack.jobs import PROCESS_EVENTS_JOB_NAME
from leadstack.routes.forms import client_bucket
from leadstack.worker import build_leadstack_worker
from tests.integration.test_crm import _contacts
from tests.integration.test_workspaces import _code, _create, _invite_and_accept, _member_id

pytestmark = pytest.mark.integration


def _w(tenant: str) -> str:
    return f"/v1/workspaces/{tenant}"


@pytest.fixture
def ws(api, make_user):
    owner, member = make_user(), make_user()
    tenant = _create(api, owner)["tenant_id"]
    _invite_and_accept(api, owner, tenant, member, "member")
    tag = api.post(f"{_w(tenant)}/tags", json={"name": "Lead"}, headers=owner.headers).json()
    return owner, member, tenant, tag["tag_id"]


def _workflow(api, owner, tenant, **body):
    response = api.post(f"{_w(tenant)}/workflows", json={"name": "W"} | body, headers=owner.headers)
    assert response.status_code == 201, response.text
    return response.json()


def _runs(api, owner, tenant, workflow_id):
    return api.get(f"{_w(tenant)}/workflows/{workflow_id}/runs", headers=owner.headers).json()


def _detail(api, owner, tenant, contact_id):
    return api.get(f"{_contacts(tenant)}/{contact_id}/detail", headers=owner.headers).json()


def test_contact_created_runs_actions_once(api, ws) -> None:
    owner, member, tenant, tag = ws
    workflow = _workflow(
        api, owner, tenant, trigger="contact.created",
        actions=[{"type": "add_tag", "tag_id": tag},
                 {"type": "create_task", "title": "Rappeler", "due_in_days": 1,
                  "assignee_user_id": str(member.user_id)},
                 {"type": "set_owner", "user_id": str(member.user_id)}],
    )  # fmt: skip
    contact = api.post(
        _contacts(tenant), json={"first_name": "Nora"}, headers=member.headers
    ).json()

    assert process_events(uuid.UUID(tenant)) >= 1
    assert process_events(uuid.UUID(tenant)) == 0  # nothing pending any more

    detail = _detail(api, owner, tenant, contact["contact_id"])
    assert [t["tag_id"] for t in detail["tags"]] == [tag]
    assert detail["owner_user_id"] == str(member.user_id)
    tasks = api.get(f"{_w(tenant)}/tasks", params={"assignee": "me"}, headers=member.headers).json()
    assert [(t["title"], t["contact_id"]) for t in tasks] == [("Rappeler", contact["contact_id"])]
    runs = _runs(api, owner, tenant, workflow["workflow_id"])
    assert [(r["status"], [s["outcome"] for s in r["steps"]]) for r in runs] == [
        ("succeeded", ["done", "done", "done"])
    ]


def test_conditions_filter_events(api, ws) -> None:
    owner, _, tenant, tag = ws
    workflow = _workflow(
        api, owner, tenant, trigger="contact.created", conditions={"source": "salon"},
        actions=[{"type": "add_tag", "tag_id": tag}],
    )  # fmt: skip
    salon = api.post(
        _contacts(tenant), json={"first_name": "A", "source": "salon"}, headers=owner.headers
    ).json()
    web = api.post(
        _contacts(tenant), json={"first_name": "B", "source": "web"}, headers=owner.headers
    ).json()

    process_events(uuid.UUID(tenant))

    assert _detail(api, owner, tenant, salon["contact_id"])["tags"]
    assert _detail(api, owner, tenant, web["contact_id"])["tags"] == []
    statuses = sorted(r["status"] for r in _runs(api, owner, tenant, workflow["workflow_id"]))
    assert statuses == ["skipped", "succeeded"]


EMAIL_FIELD = {"key": "email", "label": "E", "type": "email", "required": True, "maps_to": "email"}


def test_form_submission_and_deal_won_triggers(api, ws) -> None:
    owner, _, tenant, tag = ws
    form = api.post(
        f"{_w(tenant)}/forms",
        json={"name": "F", "consent_text": "ok", "fields": [EMAIL_FIELD]},
        headers=owner.headers,
    ).json()
    _workflow(api, owner, tenant, trigger="form.submitted", conditions={"form_id": form["form_id"]},
              actions=[{"type": "add_tag", "tag_id": tag}])  # fmt: skip
    pipeline = api.post(f"{_w(tenant)}/pipelines", json={"name": "P"}, headers=owner.headers).json()
    won = next(s["stage_id"] for s in pipeline["stages"] if s["kind"] == "won")
    _workflow(api, owner, tenant, trigger="deal.stage_changed", conditions={"status": "won"},
              actions=[{"type": "create_task", "title": "Facturer"}])  # fmt: skip

    key = form["public_key"]
    submitted = api.post(
        f"/v1/public/forms/{key}/submissions",
        json={"values": {"email": "lead@example.ma"}, "consent": True,
              "form_token": issue_token(key, client_bucket("testclient"), now=time.time() - 10)},
    )  # fmt: skip
    assert submitted.status_code == 200, submitted.text
    contact = api.get(_contacts(tenant), headers=owner.headers).json()["items"][0]
    deal = api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "D", "contact_id": contact["contact_id"]},
        headers=owner.headers,
    ).json()
    api.post(
        f"{_w(tenant)}/deals/{deal['deal_id']}/stage", json={"stage_id": won}, headers=owner.headers
    )

    process_events(uuid.UUID(tenant))

    assert [t["name"] for t in _detail(api, owner, tenant, contact["contact_id"])["tags"]] == [
        "Lead"
    ]
    tasks = api.get(f"{_w(tenant)}/tasks", headers=owner.headers).json()
    assert [(t["title"], t["contact_id"]) for t in tasks] == [("Facturer", contact["contact_id"])]


def test_unauthorized_creator_pauses_the_workflow(api, ws, make_user) -> None:
    owner, _, tenant, tag = ws
    admin = make_user()
    _invite_and_accept(api, owner, tenant, admin, "admin")
    workflow = _workflow(
        api, admin, tenant, trigger="contact.created", actions=[{"type": "add_tag", "tag_id": tag}]
    )
    api.put(
        f"{_w(tenant)}/members/{_member_id(api, owner, tenant, admin)}/role",
        json={"role": "member"},
        headers=owner.headers,
    )
    contact = api.post(_contacts(tenant), json={"first_name": "X"}, headers=owner.headers).json()

    process_events(uuid.UUID(tenant))

    runs = _runs(api, owner, tenant, workflow["workflow_id"])
    assert (runs[0]["status"], runs[0]["error_code"]) == ("failed", "not_authorized")
    listed = next(w for w in api.get(f"{_w(tenant)}/workflows", headers=owner.headers).json()
                  if w["workflow_id"] == workflow["workflow_id"])  # fmt: skip
    assert (listed["status"], listed["paused_reason"]) == ("paused", "creator_not_authorized")
    assert _detail(api, owner, tenant, contact["contact_id"])["tags"] == []
    resume = api.post(
        f"{_w(tenant)}/workflows/{workflow['workflow_id']}/status",
        json={"status": "active"},
        headers=owner.headers,
    )
    assert (resume.status_code, _code(resume)) == (422, "invalid_workflow")


@pytest.mark.parametrize(
    "body",
    [
        {
            "trigger": "contact.created",
            "actions": [{"type": "add_tag", "tag_id": str(uuid.uuid4())}],
        },
        {"trigger": "contact.created", "actions": [{"type": "send_email"}]},
        {
            "trigger": "contact.created",
            "conditions": {"email": "x"},
            "actions": [{"type": "create_task", "title": "t"}],
        },
        {
            "trigger": "contact.created",
            "actions": [{"type": "set_owner", "user_id": str(uuid.uuid4())}],
        },
        {
            "trigger": "contact.created",
            "actions": [{"type": "create_task", "title": "t", "due_in_days": 400}],
        },
        {"trigger": "contact.created", "actions": []},
    ],
)
def test_invalid_workflows_are_rejected(api, ws, body) -> None:
    owner, _, tenant, _ = ws
    response = api.post(f"{_w(tenant)}/workflows", json={"name": "W"} | body, headers=owner.headers)
    assert response.status_code == 422


def test_foreign_tag_and_member_permissions(api, ws, make_user) -> None:
    owner, member, tenant, _ = ws
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]
    foreign_tag = api.post(
        f"{_w(other)}/tags", json={"name": "T"}, headers=other_owner.headers
    ).json()["tag_id"]
    body = {
        "name": "W",
        "trigger": "contact.created",
        "actions": [{"type": "add_tag", "tag_id": foreign_tag}],
    }

    assert api.post(f"{_w(tenant)}/workflows", json=body, headers=owner.headers).status_code == 422
    assert api.post(f"{_w(tenant)}/workflows", json=body, headers=member.headers).status_code == 403
    assert api.get(f"{_w(tenant)}/workflows", headers=member.headers).status_code == 200
    assert api.get(f"{_w(other)}/workflows", headers=owner.headers).status_code == 404


def test_concurrent_processing_runs_each_workflow_once(api, ws) -> None:
    owner, _, tenant, _ = ws
    _workflow(
        api,
        owner,
        tenant,
        trigger="contact.created",
        actions=[{"type": "create_task", "title": "Once"}],
    )
    api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers)
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        process_events(uuid.UUID(tenant))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    tasks = api.get(f"{_w(tenant)}/tasks", headers=owner.headers).json()
    assert [t["title"] for t in tasks] == ["Once"]
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        assert len(session.execute(select(WorkflowRun)).scalars().all()) == 1


def test_workflow_ignores_events_from_before_it_existed(api, ws) -> None:
    owner, _, tenant, tag = ws
    early = api.post(_contacts(tenant), json={"first_name": "Early"}, headers=owner.headers).json()
    _workflow(
        api, owner, tenant, trigger="contact.created", actions=[{"type": "add_tag", "tag_id": tag}]
    )

    process_events(uuid.UUID(tenant))

    assert _detail(api, owner, tenant, early["contact_id"])["tags"] == []


def test_events_carry_no_personal_data(api, ws) -> None:
    owner, _, tenant, _ = ws
    api.post(
        _contacts(tenant),
        json={"first_name": "Yasmine", "email": "yasmine@example.ma", "phone": "+212612345678"},
        headers=owner.headers,
    )
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        attributes = [e.attributes for e in session.execute(select(Event)).scalars()]
    blob = repr(attributes)
    for personal in ("Yasmine", "yasmine@example.ma", "+212612345678"):
        assert personal not in blob
    assert {"has_email": True, "has_phone": True, "source": None} in attributes


def test_worker_processes_dispatched_events(api, ws, platform_env) -> None:
    owner, _, tenant, tag = ws
    _workflow(
        api, owner, tenant, trigger="contact.created", actions=[{"type": "add_tag", "tag_id": tag}]
    )
    contact = api.post(_contacts(tenant), json={"first_name": "W"}, headers=owner.headers).json()
    queue = f"leadstack-test:{uuid.uuid4().hex}"

    async def scenario() -> int:
        await enqueue_job(
            PROCESS_EVENTS_JOB_NAME, TenantJobPayload(tenant_id=tenant), queue_name=queue
        )
        worker = build_leadstack_worker(get_jobs_config(), burst=True, queue_name=queue)
        try:
            await worker.main()
        finally:
            await worker.close()
        return worker.jobs_complete

    assert asyncio.run(scenario()) == 1
    assert _detail(api, owner, tenant, contact["contact_id"])["tags"]
