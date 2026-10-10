"""Event-triggered workflows (immediate actions only).

A workflow = trigger (event type) + conditions (equality on the event's
non-personal attributes) + up to 10 allowlisted actions. Actions run
through the ordinary domain services *as the workflow's creator*, so every
action is authorized at execution time exactly like an API call by that
user; a creator who lost the permission makes the run fail with
`not_authorized` and pauses the workflow.

Reliability:
- Events come from a transactional outbox (`events.emit`).
- `process_events` is safe under duplicate and concurrent delivery: the
  unique (workflow, event) run row is claimed in its own short transaction
  before any action runs; a second worker sees the conflict and skips.
- No database session is held while actions execute (they open their own
  sessions and authorization checks; nesting would exhaust the pool).
- A run that crashed mid-way (status `running` older than
  STALE_RUN_MINUTES) is retried on the next pass. Actions are idempotent
  (tagging, owner assignment) except `create_task`, which is at-least-once.
- Delays and schedules are deliberately absent until decision D6.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core import audit_log
from core.audit_log import ActorType, AuditOutcome
from core.tenancy import TenantNotFoundError, TenantStatus, get_tenant, is_accessible_to_principals
from infra.db import (
    IntegrityError,
    acquire_tenant_advisory_lock,
    delete,
    select,
    tenant_session_scope,
)

from leadstack.domains.automation import events as ev
from leadstack.domains.automation.models import Event, Workflow, WorkflowRun
from leadstack.domains.crm import activity
from leadstack.domains.crm import service as crm
from leadstack.domains.crm.activity_models import Tag
from leadstack.domains.sales.models import Deal
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.service import (
    PermissionDeniedError,
    _holds,
    authorize,
    is_active_member,
)

logger = logging.getLogger(__name__)

MAX_ACTIONS = 10
MAX_WORKFLOWS = 50
EVENT_BATCH = 100
STALE_RUN_MINUTES = 10
CONDITION_KEYS: dict[str, frozenset[str]] = {
    ev.CONTACT_CREATED: frozenset({"source", "has_email", "has_phone"}),
    ev.FORM_SUBMITTED: frozenset({"form_id", "contact_is_new"}),
    ev.DEAL_STAGE_CHANGED: frozenset({"pipeline_id", "to_stage_id", "status"}),
}
ACTION_TYPES = ("add_tag", "create_task", "set_owner")


class AutomationError(Exception):
    code = "automation_error"


class InvalidWorkflowError(AutomationError):
    code = "invalid_workflow"


class WorkflowNotFoundError(AutomationError):
    code = "workflow_not_found"


class WorkflowLimitError(AutomationError):
    code = "workflow_limit_reached"


@dataclass(frozen=True)
class WorkflowView:
    workflow_id: uuid.UUID
    name: str
    trigger: str
    conditions: dict[str, Any]
    actions: list[dict[str, Any]]
    status: str
    paused_reason: str | None
    created_by_user_id: uuid.UUID
    created_at: datetime


@dataclass(frozen=True)
class RunView:
    run_id: uuid.UUID
    event_id: uuid.UUID
    status: str
    error_code: str | None
    steps: list[dict[str, Any]]
    started_at: datetime
    finished_at: datetime | None


def _view(row: Workflow) -> WorkflowView:
    return WorkflowView(
        row.id, row.name, row.trigger, dict(row.conditions), list(row.actions), row.status,
        row.paused_reason, row.created_by_user_id, row.created_at,
    )  # fmt: skip


def _audit(tenant_id, actor_type, actor_id, action: str, rid, metadata=None) -> None:
    audit_log.record(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_user_id=actor_id,
        action=action,
        resource_type="leadstack.workflow",
        resource_id=str(rid),
        outcome=AuditOutcome.SUCCESS,
        metadata=metadata,
    )


# --- Definitions -------------------------------------------------------------------


_BOOL_KEYS = frozenset({"has_email", "has_phone", "contact_is_new"})
_UUID_KEYS = frozenset({"form_id", "pipeline_id", "to_stage_id"})


def _normalize_condition(key: str, value: Any) -> Any:
    """Typed conditions, normalized to the exact form events carry, so a
    condition can never silently fail to match because of formatting."""
    if key in _BOOL_KEYS:
        if not isinstance(value, bool):
            raise InvalidWorkflowError()
        return value
    if key in _UUID_KEYS:
        return str(uuid.UUID(str(value)))
    if key == "status":
        if value not in ("open", "won", "lost"):
            raise InvalidWorkflowError()
        return value
    if not isinstance(value, str) or not value or len(value) > 64:
        raise InvalidWorkflowError()
    return value


def _validate(
    tenant_id: uuid.UUID, trigger: str, conditions: dict[str, Any], actions: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if trigger not in CONDITION_KEYS:
        raise InvalidWorkflowError()
    if set(conditions) - CONDITION_KEYS[trigger]:
        raise InvalidWorkflowError()
    conditions = {key: _normalize_condition(key, value) for key, value in conditions.items()}
    if not 1 <= len(actions) <= MAX_ACTIONS:
        raise InvalidWorkflowError()
    cleaned: list[dict[str, Any]] = []
    tag_ids: list[uuid.UUID] = []
    for action in actions:
        kind = action.get("type")
        if kind == "add_tag":
            tag_ids.append(uuid.UUID(str(action.get("tag_id"))))
            cleaned.append({"type": kind, "tag_id": str(action.get("tag_id"))})
        elif kind == "create_task":
            title = str(action.get("title", "")).strip()
            due = action.get("due_in_days")
            assignee = action.get("assignee_user_id")
            if not title or len(title) > 200:
                raise InvalidWorkflowError()
            if due is not None and (
                isinstance(due, bool) or not isinstance(due, int) or not 0 <= due <= 365
            ):
                raise InvalidWorkflowError()
            if assignee is not None and not is_active_member(tenant_id, uuid.UUID(str(assignee))):
                raise InvalidWorkflowError()
            cleaned.append({"type": kind, "title": title, "due_in_days": due,
                            "assignee_user_id": str(assignee) if assignee else None})  # fmt: skip
        elif kind == "set_owner":
            user = uuid.UUID(str(action.get("user_id")))
            if not is_active_member(tenant_id, user):
                raise InvalidWorkflowError()
            cleaned.append({"type": kind, "user_id": str(user)})
        else:
            raise InvalidWorkflowError()
    if tag_ids:
        with tenant_session_scope(tenant_id) as session:
            found = set(
                session.execute(
                    select(Tag.id).where(Tag.tenant_id == tenant_id, Tag.id.in_(tag_ids))
                ).scalars()
            )
        if found != set(tag_ids):
            raise InvalidWorkflowError()  # tags must belong to this workspace
    return dict(conditions), cleaned


def create_workflow(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str,
    trigger: str,
    conditions: dict[str, Any],
    actions: list[dict[str, Any]],
) -> WorkflowView:
    authorize(actor_id, tenant_id, perms.WORKFLOWS_MANAGE)
    name = name.strip()
    if not name or len(name) > 100:
        raise InvalidWorkflowError()
    try:
        conditions, cleaned = _validate(tenant_id, trigger, conditions, actions)
    except (ValueError, TypeError):
        raise InvalidWorkflowError() from None
    with tenant_session_scope(tenant_id) as session:
        acquire_tenant_advisory_lock(session, tenant_id, "leadstack.workflows")
        count = len(
            session.execute(select(Workflow.id).where(Workflow.tenant_id == tenant_id)).all()
        )
        if count >= MAX_WORKFLOWS:
            raise WorkflowLimitError()
        row = Workflow(
            tenant_id=tenant_id, name=name, trigger=trigger, conditions=conditions,
            actions=cleaned, created_by_user_id=actor_id,
        )  # fmt: skip
        session.add(row)
        session.flush()
        session.refresh(row)
        view = _view(row)
    _audit(tenant_id, ActorType.USER, actor_id, "leadstack.workflow.created", view.workflow_id,
           {"trigger": trigger, "actions": len(cleaned)})  # fmt: skip
    return view


def list_workflows(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[WorkflowView]:
    authorize(actor_id, tenant_id, perms.WORKFLOWS_READ)
    with tenant_session_scope(tenant_id) as session:
        rows = session.execute(
            select(Workflow).where(Workflow.tenant_id == tenant_id).order_by(Workflow.created_at)
        ).scalars()
        return [_view(r) for r in rows]


def set_workflow_status(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, workflow_id: uuid.UUID, status: str
) -> WorkflowView:
    """Pause or resume. Resuming re-checks that the creator may still run it."""
    authorize(actor_id, tenant_id, perms.WORKFLOWS_MANAGE)
    if status not in ("active", "paused"):
        raise InvalidWorkflowError()
    with tenant_session_scope(tenant_id) as session:
        row = _workflow_row(session, tenant_id, workflow_id)
        creator = row.created_by_user_id
    if status == "active" and not _creator_may_run(tenant_id, creator):
        raise InvalidWorkflowError()
    with tenant_session_scope(tenant_id) as session:
        row = _workflow_row(session, tenant_id, workflow_id, lock=True)
        row.status, row.paused_reason = status, None if status == "active" else "manual"
        row.updated_at = datetime.now(UTC)
        session.flush()
        view = _view(row)
    _audit(tenant_id, ActorType.USER, actor_id, f"leadstack.workflow.{status}", workflow_id)
    return view


def delete_workflow(actor_id: uuid.UUID, tenant_id: uuid.UUID, workflow_id: uuid.UUID) -> None:
    authorize(actor_id, tenant_id, perms.WORKFLOWS_MANAGE)
    with tenant_session_scope(tenant_id) as session:
        session.delete(_workflow_row(session, tenant_id, workflow_id))
    _audit(tenant_id, ActorType.USER, actor_id, "leadstack.workflow.deleted", workflow_id)


def list_runs(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, workflow_id: uuid.UUID, *, limit: int
) -> list[RunView]:
    authorize(actor_id, tenant_id, perms.WORKFLOWS_READ)
    with tenant_session_scope(tenant_id) as session:
        _workflow_row(session, tenant_id, workflow_id)
        rows = session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.tenant_id == tenant_id, WorkflowRun.workflow_id == workflow_id)
            .order_by(WorkflowRun.started_at.desc())
            .limit(max(1, min(limit, 100)))
        ).scalars()
        return [
            RunView(
                r.id, r.event_id, r.status, r.error_code, list(r.steps), r.started_at, r.finished_at
            )
            for r in rows
        ]


def _workflow_row(session, tenant_id, workflow_id, *, lock: bool = False) -> Workflow:
    statement = select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.id == workflow_id)
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).scalar_one_or_none()
    if row is None:
        raise WorkflowNotFoundError()
    return row


# --- Execution -----------------------------------------------------------------------

# Configuration errors that will fail every future run too: pause instead.
_CONFIGURATION_ERRORS = (activity.TagNotFoundError, activity.InvalidAssigneeError)
MUTATING_ACTIONS = frozenset({"add_tag", "set_owner"})


def _creator_may_run(tenant_id: uuid.UUID, creator: uuid.UUID) -> bool:
    return is_active_member(tenant_id, creator) and _holds(
        creator, tenant_id, perms.WORKFLOWS_MANAGE
    )


def _target_contact(tenant_id: uuid.UUID, event: dict[str, Any]) -> uuid.UUID | None:
    if event["event_type"] == ev.CONTACT_CREATED:
        return event["subject_id"]
    if event["event_type"] == ev.FORM_SUBMITTED:
        contact = event["attributes"].get("contact_id")
        return uuid.UUID(contact) if contact else None
    with tenant_session_scope(tenant_id) as session:
        return session.execute(
            select(Deal.contact_id).where(
                Deal.tenant_id == tenant_id, Deal.id == event["subject_id"]
            )
        ).scalar_one_or_none()


def _matches(conditions: dict[str, Any], attributes: dict[str, Any]) -> bool:
    return all(attributes.get(key) == value for key, value in conditions.items())


def _execute(
    tenant_id: uuid.UUID,
    creator: uuid.UUID,
    action: dict[str, Any],
    contact_id: uuid.UUID | None,
    *,
    existing_contact_from_form: bool,
) -> str:
    """One action as the creator; returns the step outcome."""
    kind = action["type"]
    if kind == "create_task":
        due = action.get("due_in_days")
        fields: dict[str, Any] = {"title": action["title"], "contact_id": contact_id}
        if due is not None:
            fields["due_at"] = datetime.now(UTC) + timedelta(days=due)
        if action.get("assignee_user_id"):
            fields["assignee_user_id"] = uuid.UUID(action["assignee_user_id"])
        activity.create_task(creator, tenant_id, fields)
        return "done"
    if contact_id is None:
        return "skipped_no_contact"
    if existing_contact_from_form and kind in MUTATING_ACTIONS:
        # An anonymous visitor typed the address of an existing contact: a
        # public form must not be able to change existing CRM records.
        return "skipped_existing_contact"
    if kind == "add_tag":
        activity.set_contact_tag(creator, tenant_id, contact_id, uuid.UUID(action["tag_id"]), True)
        return "done"
    if kind == "set_owner":
        crm.update_contact(
            creator,
            tenant_id,
            contact_id,
            crm.ContactInput(owner_user_id=uuid.UUID(action["user_id"])),
        )
        return "done"
    raise InvalidWorkflowError()


@dataclass(frozen=True)
class _Claim:
    run_id: uuid.UUID
    token: uuid.UUID


def _claim_run(tenant_id: uuid.UUID, workflow_id: uuid.UUID, event_id: uuid.UUID) -> _Claim | str:
    """Claim the (workflow, event) run. Returns the claim, or a reason:
    "inactive" (paused/deleted meanwhile), "done" (terminal run exists),
    "in_progress" (another worker holds a fresh claim)."""
    token = uuid.uuid4()
    try:
        with tenant_session_scope(tenant_id) as session:
            active = session.execute(
                select(Workflow.id)
                .where(
                    Workflow.tenant_id == tenant_id,
                    Workflow.id == workflow_id,
                    Workflow.status == "active",
                )
                .with_for_update(read=True)
            ).scalar_one_or_none()
            if active is None:
                return "inactive"
            run = WorkflowRun(
                tenant_id=tenant_id, workflow_id=workflow_id, event_id=event_id,
                status="running", steps=[], claim_token=token,
            )  # fmt: skip
            session.add(run)
            session.flush()
            return _Claim(run.id, token)
    except IntegrityError:
        pass
    stale_before = datetime.now(UTC) - timedelta(minutes=STALE_RUN_MINUTES)
    with tenant_session_scope(tenant_id) as session:
        run = session.execute(
            select(WorkflowRun)
            .where(
                WorkflowRun.tenant_id == tenant_id,
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.event_id == event_id,
            )
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if run is None:
            return "in_progress"  # another worker is taking it over right now
        if run.status != "running":
            return "done"
        if run.started_at > stale_before:
            return "in_progress"
        run.started_at, run.claim_token = datetime.now(UTC), token  # take over a crashed run
        return _Claim(run.id, token)


def _finish_run(
    tenant_id, claim: _Claim, status: str, steps: list[dict[str, Any]], error: str | None
) -> None:
    with tenant_session_scope(tenant_id) as session:
        run = session.get(WorkflowRun, claim.run_id, with_for_update=True)
        if run is not None and run.claim_token == claim.token:
            run.status, run.steps, run.error_code = status, steps, error
            run.finished_at = datetime.now(UTC)


def _pause(tenant_id: uuid.UUID, workflow_id: uuid.UUID, reason: str) -> None:
    with tenant_session_scope(tenant_id) as session:
        row = session.get(Workflow, workflow_id)
        if row is not None and row.status == "active":
            row.status, row.paused_reason = "paused", reason
    _audit(tenant_id, ActorType.SYSTEM, None, "leadstack.workflow.auto_paused", workflow_id,
           {"reason": reason})  # fmt: skip


def _run_workflow(tenant_id: uuid.UUID, workflow: dict[str, Any], event: dict[str, Any]) -> str:
    """Returns "finished" when the run is terminal (or not needed) and
    "pending" when another worker still holds it -- the event must then stay
    pending so a crashed claim is eventually taken over."""
    claim = _claim_run(tenant_id, workflow["id"], event["id"])
    if claim == "in_progress":
        return "pending"
    if isinstance(claim, str):
        return "finished"
    steps: list[dict[str, Any]] = []
    try:
        if not _matches(workflow["conditions"], event["attributes"]):
            _finish_run(tenant_id, claim, "skipped", [], "conditions_not_met")
            return "finished"
        creator = workflow["created_by_user_id"]
        if not _creator_may_run(tenant_id, creator):
            _finish_run(tenant_id, claim, "failed", [], "not_authorized")
            _pause(tenant_id, workflow["id"], "creator_not_authorized")
            return "finished"
        contact_id = _target_contact(tenant_id, event)
        existing = (
            event["event_type"] == ev.FORM_SUBMITTED
            and event["attributes"].get("contact_is_new") is False
        )
        for index, action in enumerate(workflow["actions"]):
            try:
                outcome = _execute(
                    tenant_id, creator, action, contact_id, existing_contact_from_form=existing
                )
            except PermissionDeniedError:
                steps.append({"index": index, "type": action["type"], "outcome": "not_authorized"})
                _finish_run(tenant_id, claim, "failed", steps, "not_authorized")
                _pause(tenant_id, workflow["id"], "creator_not_authorized")
                return "finished"
            except _CONFIGURATION_ERRORS as exc:
                steps.append({"index": index, "type": action["type"], "outcome": exc.code})
                _finish_run(tenant_id, claim, "failed", steps, exc.code)
                _pause(tenant_id, workflow["id"], "configuration_error")
                return "finished"
            except (AutomationError, crm.CrmError) as exc:
                steps.append({"index": index, "type": action["type"], "outcome": exc.code})
                _finish_run(tenant_id, claim, "failed", steps, exc.code)
                return "finished"
            steps.append({"index": index, "type": action["type"], "outcome": outcome})
        _finish_run(tenant_id, claim, "succeeded", steps, None)
        return "finished"
    except Exception:
        # Never leave a run `running` because of an unexpected error: record
        # it as failed (visible in the run history), then let the job retry.
        logger.exception("workflow_run_internal_error", extra={"workflow_id": str(workflow["id"])})
        _finish_run(tenant_id, claim, "failed", steps, "internal_error")
        raise


def process_events(tenant_id: uuid.UUID, *, limit: int = EVENT_BATCH) -> int:
    """Process pending events of one workspace, oldest first. Returns the
    number of events completed.

    - A workspace that does not admit principals (suspended or closed) is
      left alone: its events stay pending and its workflows are not paused.
    - An event is marked processed only when every matching workflow has a
      terminal run for it; a run held by another (possibly crashed) worker
      keeps the event pending until that run finishes or is taken over.
    """
    try:
        if not is_accessible_to_principals(TenantStatus(get_tenant(tenant_id).status)):
            return 0
    except TenantNotFoundError:
        return 0
    with tenant_session_scope(tenant_id) as session:
        pending = [
            {"id": e.id, "event_type": e.event_type, "subject_id": e.subject_id,
             "attributes": dict(e.attributes), "occurred_at": e.occurred_at}
            for e in session.execute(
                select(Event)
                .where(Event.tenant_id == tenant_id, Event.processed_at.is_(None))
                .order_by(Event.occurred_at, Event.id)
                .limit(limit)
            ).scalars()
        ]  # fmt: skip
        workflows = [
            {"id": w.id, "trigger": w.trigger, "conditions": dict(w.conditions),
             "actions": list(w.actions), "created_by_user_id": w.created_by_user_id,
             "created_at": w.created_at}
            for w in session.execute(
                select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.status == "active")
            ).scalars()
        ]  # fmt: skip
    completed = 0
    for event in pending:
        outcomes = [
            _run_workflow(tenant_id, workflow, event)
            for workflow in workflows
            # A workflow only reacts to events that happened after it existed.
            if workflow["trigger"] == event["event_type"]
            and workflow["created_at"] <= event["occurred_at"]
        ]
        if "pending" in outcomes:
            continue
        with tenant_session_scope(tenant_id) as session:
            row = session.get(Event, event["id"])
            if row is not None and row.processed_at is None:
                row.processed_at = datetime.now(UTC)
        completed += 1
    return completed


def prune(tenant_id: uuid.UUID, *, older_than_days: int) -> tuple[int, int]:
    """Retention: delete processed events and finished runs older than the
    given age (they keep identifiers of contacts that may since be erased)."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    with tenant_session_scope(tenant_id) as session:
        runs = session.execute(
            delete(WorkflowRun).where(
                WorkflowRun.tenant_id == tenant_id,
                WorkflowRun.status != "running",
                WorkflowRun.finished_at < cutoff,
            )
        )
        events = session.execute(
            delete(Event).where(
                Event.tenant_id == tenant_id,
                Event.processed_at.is_not(None),
                Event.processed_at < cutoff,
            )
        )
        return int(getattr(events, "rowcount", 0) or 0), int(getattr(runs, "rowcount", 0) or 0)
