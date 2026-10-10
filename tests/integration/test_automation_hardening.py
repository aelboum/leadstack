"""Regression tests for the Phase 7 independent-review findings
(docs/audits/phase-7.md)."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import redis
from core.rbac import RoleScope, create_deny, grant_permission, list_roles, register_permission
from core.tenancy import TenantStatus, transition_tenant_status
from infra.db import select, tenant_session_scope
from infra.jobs import TenantJobPayload

from leadstack.domains.automation import service as automation
from leadstack.domains.automation.models import Event, Workflow, WorkflowRun
from leadstack.domains.crm import activity
from leadstack.domains.forms.service import issue_token
from leadstack.domains.workspaces import permissions as perms
from leadstack.jobs import leadstack_process_events
from leadstack.routes.forms import client_bucket
from tests.integration.test_automation import EMAIL_FIELD, _detail, _runs, _w, _workflow
from tests.integration.test_crm import _contacts
from tests.integration.test_workspaces import _code, _create, _invite_and_accept

pytestmark = pytest.mark.integration


@pytest.fixture
def ws(api, make_user):
    owner = make_user()
    tenant = _create(api, owner)["tenant_id"]
    tag = api.post(f"{_w(tenant)}/tags", json={"name": "Lead"}, headers=owner.headers).json()
    return owner, tenant, tag["tag_id"]


def _pending(tenant: str) -> list[uuid.UUID]:
    with tenant_session_scope(uuid.UUID(tenant)) as session:
        return list(session.execute(select(Event.id).where(Event.processed_at.is_(None))).scalars())


def test_crash_mid_run_is_recorded_and_not_left_running(api, ws, monkeypatch) -> None:
    owner, tenant, _ = ws
    workflow = _workflow(
        api,
        owner,
        tenant,
        trigger="contact.created",
        actions=[{"type": "create_task", "title": "T"}],
    )
    api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers)
    original = activity.create_task

    def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(activity, "create_task", boom)
    with pytest.raises(RuntimeError):
        automation.process_events(uuid.UUID(tenant))
    monkeypatch.setattr(activity, "create_task", original)

    run = _runs(api, owner, tenant, workflow["workflow_id"])[0]
    assert (run["status"], run["error_code"]) == ("failed", "internal_error")
    assert len(_pending(tenant)) == 1  # the job retry will see it again
    automation.process_events(uuid.UUID(tenant))
    assert _pending(tenant) == []
    assert len(_runs(api, owner, tenant, workflow["workflow_id"])) == 1  # never duplicated


def test_run_held_by_another_worker_keeps_event_pending_until_stale(api, ws) -> None:
    owner, tenant, tag = ws
    workflow = _workflow(
        api, owner, tenant, trigger="contact.created", actions=[{"type": "add_tag", "tag_id": tag}]
    )
    contact = api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers).json()
    tenant_id = uuid.UUID(tenant)
    (event_id,) = _pending(tenant)
    with tenant_session_scope(tenant_id) as session:  # a worker that claimed, then crashed
        session.add(
            WorkflowRun(
                tenant_id=tenant_id,
                workflow_id=uuid.UUID(workflow["workflow_id"]),
                event_id=event_id,
                status="running",
                steps=[],
                claim_token=uuid.uuid4(),
            )
        )

    assert automation.process_events(tenant_id) == 0
    assert (
        len(_pending(tenant)) == 1
        and _detail(api, owner, tenant, contact["contact_id"])["tags"] == []
    )

    with tenant_session_scope(tenant_id) as session:
        run = session.execute(select(WorkflowRun)).scalar_one()
        run.started_at = datetime.now(UTC) - timedelta(minutes=automation.STALE_RUN_MINUTES + 1)
    assert automation.process_events(tenant_id) == 1
    assert _detail(api, owner, tenant, contact["contact_id"])["tags"]
    assert _runs(api, owner, tenant, workflow["workflow_id"])[0]["status"] == "succeeded"


def test_slow_original_worker_cannot_overwrite_a_takeover(api, ws) -> None:
    owner, tenant, _ = ws
    workflow = _workflow(
        api,
        owner,
        tenant,
        trigger="contact.created",
        actions=[{"type": "create_task", "title": "T"}],
    )
    api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers)
    tenant_id, workflow_id = uuid.UUID(tenant), uuid.UUID(workflow["workflow_id"])
    (event_id,) = _pending(tenant)

    original = automation._claim_run(tenant_id, workflow_id, event_id)
    assert isinstance(original, automation._Claim)
    with tenant_session_scope(tenant_id) as session:
        session.execute(select(WorkflowRun)).scalar_one().started_at -= timedelta(hours=1)
    takeover = automation._claim_run(tenant_id, workflow_id, event_id)
    assert isinstance(takeover, automation._Claim) and takeover.token != original.token
    automation._finish_run(tenant_id, takeover, "succeeded", [], None)
    automation._finish_run(tenant_id, original, "failed", [], "late")

    assert _runs(api, owner, tenant, workflow["workflow_id"])[0]["status"] == "succeeded"


def test_suspended_workspace_is_left_alone_then_resumes(api, ws) -> None:
    owner, tenant, tag = ws
    workflow = _workflow(
        api, owner, tenant, trigger="contact.created", actions=[{"type": "add_tag", "tag_id": tag}]
    )
    contact = api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers).json()
    tenant_id = uuid.UUID(tenant)

    transition_tenant_status(tenant_id, TenantStatus.SUSPENDED, actor_user_id=owner.user_id)
    assert automation.process_events(tenant_id) == 0
    transition_tenant_status(tenant_id, TenantStatus.ACTIVE, actor_user_id=owner.user_id)

    with tenant_session_scope(tenant_id) as session:
        row = session.get(Workflow, uuid.UUID(workflow["workflow_id"]))
        assert row is not None and row.status == "active"
    assert automation.process_events(tenant_id) == 1
    assert _detail(api, owner, tenant, contact["contact_id"])["tags"]


def test_form_cannot_mutate_an_existing_contact_via_workflows(api, ws, make_user) -> None:
    owner, tenant, tag = ws
    colleague = make_user()
    _invite_and_accept(api, owner, tenant, colleague, "member")
    existing = api.post(
        _contacts(tenant),
        json={"first_name": "Client", "email": "client@example.ma"},
        headers=owner.headers,
    ).json()
    form = api.post(
        f"{_w(tenant)}/forms",
        json={"name": "F", "consent_text": "ok", "fields": [EMAIL_FIELD]},
        headers=owner.headers,
    ).json()
    automation.process_events(uuid.UUID(tenant))  # clear the contact.created event
    workflow = _workflow(
        api,
        owner,
        tenant,
        trigger="form.submitted",
        actions=[
            {"type": "add_tag", "tag_id": tag},
            {"type": "set_owner", "user_id": str(colleague.user_id)},
            {"type": "create_task", "title": "Follow up"},
        ],
    )
    key = form["public_key"]

    api.post(
        f"/v1/public/forms/{key}/submissions",
        json={
            "values": {"email": "CLIENT@example.ma"},
            "consent": True,
            "form_token": issue_token(key, client_bucket("testclient"), now=time.time() - 10),
        },
    )
    automation.process_events(uuid.UUID(tenant))

    detail = _detail(api, owner, tenant, existing["contact_id"])
    assert detail["tags"] == [] and detail["owner_user_id"] is None
    steps = [s["outcome"] for s in _runs(api, owner, tenant, workflow["workflow_id"])[0]["steps"]]
    assert steps == ["skipped_existing_contact", "skipped_existing_contact", "done"]


def test_deny_grant_on_creator_fails_and_pauses(api, ws, make_user) -> None:
    owner, tenant, tag = ws
    admin = make_user()
    _invite_and_accept(api, owner, tenant, admin, "admin")
    workflow = _workflow(
        api, admin, tenant, trigger="contact.created", actions=[{"type": "add_tag", "tag_id": tag}]
    )
    # Managing deny grants is a SaaS-OS capability LeadStack roles do not
    # carry; grant it to the owner role for this test only.
    tenant_id = uuid.UUID(tenant)
    owner_role = next(r for r in list_roles(tenant_id) if r.name == perms.ROLE_OWNER)
    grant_permission(tenant_id, owner_role.id, register_permission("deny_grant", "create").id)
    permission = register_permission(*perms.WORKFLOWS_MANAGE)
    create_deny(
        grantor_user_id=owner.user_id,
        principal_user_id=admin.user_id,
        tenant_id=uuid.UUID(tenant),
        scope_mode=RoleScope.SELF,
        permission_id=permission.id,
    )
    api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers)

    automation.process_events(uuid.UUID(tenant))

    run = _runs(api, owner, tenant, workflow["workflow_id"])[0]
    assert run["error_code"] == "not_authorized"


def test_deleted_tag_pauses_the_workflow(api, ws) -> None:
    owner, tenant, tag = ws
    workflow = _workflow(
        api, owner, tenant, trigger="contact.created", actions=[{"type": "add_tag", "tag_id": tag}]
    )
    api.delete(f"{_w(tenant)}/tags/{tag}", headers=owner.headers)
    api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers)

    automation.process_events(uuid.UUID(tenant))

    run = _runs(api, owner, tenant, workflow["workflow_id"])[0]
    assert (run["status"], run["error_code"]) == ("failed", "tag_not_found")
    listed = api.get(f"{_w(tenant)}/workflows", headers=owner.headers).json()[0]
    assert (listed["status"], listed["paused_reason"]) == ("paused", "configuration_error")


def test_paused_workflow_is_not_claimed(api, ws) -> None:
    owner, tenant, _ = ws
    workflow = _workflow(
        api,
        owner,
        tenant,
        trigger="contact.created",
        actions=[{"type": "create_task", "title": "T"}],
    )
    api.post(
        f"{_w(tenant)}/workflows/{workflow['workflow_id']}/status",
        json={"status": "paused"},
        headers=owner.headers,
    )
    claim = automation._claim_run(
        uuid.UUID(tenant), uuid.UUID(workflow["workflow_id"]), uuid.uuid4()
    )
    assert claim == "inactive"


@pytest.mark.parametrize(
    ("conditions", "ok"),
    [
        ({"has_email": "true"}, False),
        ({"has_email": True}, True),
        ({"source": ""}, False),
        ({"source": "salon"}, True),
    ],
)
def test_conditions_are_typed(api, ws, conditions, ok) -> None:
    owner, tenant, _ = ws
    response = api.post(
        f"{_w(tenant)}/workflows",
        json={
            "name": "W",
            "trigger": "contact.created",
            "conditions": conditions,
            "actions": [{"type": "create_task", "title": "T"}],
        },
        headers=owner.headers,
    )
    assert response.status_code == (201 if ok else 422)


def test_deal_conditions_are_normalized(api, ws) -> None:
    owner, tenant, _ = ws
    pipeline = api.post(f"{_w(tenant)}/pipelines", json={"name": "P"}, headers=owner.headers).json()
    upper = pipeline["pipeline_id"].upper()
    created = api.post(
        f"{_w(tenant)}/workflows",
        json={
            "name": "W",
            "trigger": "deal.stage_changed",
            "conditions": {"pipeline_id": upper, "status": "won"},
            "actions": [{"type": "create_task", "title": "Won"}],
        },
        headers=owner.headers,
    ).json()
    bad = api.post(
        f"{_w(tenant)}/workflows",
        json={
            "name": "W2",
            "trigger": "deal.stage_changed",
            "conditions": {"status": "closed"},
            "actions": [{"type": "create_task", "title": "x"}],
        },
        headers=owner.headers,
    )
    assert created["conditions"]["pipeline_id"] == pipeline["pipeline_id"]
    assert (bad.status_code, _code(bad)) == (422, "invalid_workflow")


def test_processing_is_coalesced_per_workspace(api, ws, services) -> None:
    owner, tenant, _ = ws
    client = redis.Redis.from_url(services.redis_url)
    lock = f"leadstack:events-lock:{tenant}"
    client.set(lock, "1", ex=60)
    try:
        result = asyncio.run(leadstack_process_events(TenantJobPayload(tenant_id=tenant)))
    finally:
        client.delete(lock)
        client.close()
    assert result == {"processed": 0, "coalesced": 1}
    api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers)
    assert (
        asyncio.run(leadstack_process_events(TenantJobPayload(tenant_id=tenant)))["processed"] == 1
    )


def test_prune_removes_old_history(api, ws) -> None:
    owner, tenant, _ = ws
    _workflow(
        api,
        owner,
        tenant,
        trigger="contact.created",
        actions=[{"type": "create_task", "title": "T"}],
    )
    api.post(_contacts(tenant), json={"first_name": "C"}, headers=owner.headers)
    tenant_id = uuid.UUID(tenant)
    automation.process_events(tenant_id)
    with tenant_session_scope(tenant_id) as session:
        for event in session.execute(select(Event)).scalars():
            event.processed_at = datetime.now(UTC) - timedelta(days=100)
        for run in session.execute(select(WorkflowRun)).scalars():
            run.finished_at = datetime.now(UTC) - timedelta(days=100)

    assert automation.prune(tenant_id, older_than_days=90) == (1, 1)


def test_events_lock_is_released_only_by_its_holder(api, ws, services, monkeypatch) -> None:
    """If the lock expired during a long pass and another worker took it,
    the first worker must not delete the other worker's lock."""
    import asyncio

    import redis
    from infra.jobs import TenantJobPayload

    import leadstack.domains.automation.service as automation_service
    from leadstack.jobs import leadstack_process_events

    _owner, tenant = ws[0], ws[1]
    client = redis.Redis.from_url(services.redis_url)
    key = f"leadstack:events-lock:{tenant}"
    client.delete(key)

    calls = []

    def stolen(_tenant_id) -> int:
        calls.append(1)
        if len(calls) == 1:  # only the pass made while "holding" the lock
            client.set(key, "another-worker")  # simulates expiry + takeover mid-pass
        return 0

    monkeypatch.setattr(automation_service, "process_events", stolen)
    try:
        asyncio.run(leadstack_process_events(TenantJobPayload(tenant_id=str(tenant), data={})))
        assert client.get(key) == b"another-worker"
    finally:
        client.delete(key)
        client.close()
