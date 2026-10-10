"""CRM productivity: tags, notes, tasks, custom-field definitions, the
contact timeline and bulk actions.

Same discipline as `crm.service`: authorize with `can()`, operate inside
`tenant_session_scope()` with explicit tenant filters, audit identifiers and
counts only (never note bodies, task titles or field values).

Permissions:
- reading: `leadstack.contacts:read`
- notes, tasks, tagging, custom-field values: `leadstack.contacts:update`
- deleting someone else's note, bulk delete: `leadstack.contacts:delete`
- managing tags and custom-field definitions: `leadstack.crm_settings:manage`
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core import audit_log
from core.audit_log import ActorType, AuditOutcome
from infra.db import (
    IntegrityError,
    acquire_tenant_advisory_lock,
    delete,
    select,
    tenant_session_scope,
)

from leadstack.domains.crm import custom_fields as cf
from leadstack.domains.crm.activity_models import (
    ContactTag,
    CustomFieldDefinition,
    Note,
    Tag,
    Task,
)
from leadstack.domains.crm.models import Contact
from leadstack.domains.crm.service import ContactNotFoundError, CrmError
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.service import _holds, authorize, is_active_member

MAX_BULK = 500
MAX_TAGS = 200
MAX_TIMELINE_PAGE = 100
SETTINGS_LOCK_KEY = "leadstack.crm_settings"
STRIP_BATCH = 500


class TagNotFoundError(CrmError):
    code = "tag_not_found"


class DuplicateTagError(CrmError):
    code = "tag_exists"


class TagLimitError(CrmError):
    code = "tag_limit_reached"


class NoteNotFoundError(CrmError):
    code = "note_not_found"


class TaskNotFoundError(CrmError):
    code = "task_not_found"


class InvalidTaskError(CrmError):
    code = "invalid_task"


class InvalidAssigneeError(CrmError):
    code = "invalid_assignee"


class FieldDefinitionNotFoundError(CrmError):
    code = "custom_field_not_found"


class DuplicateFieldDefinitionError(CrmError):
    code = "custom_field_exists"


class FieldDefinitionLimitError(CrmError):
    code = "custom_field_limit_reached"


class BulkLimitError(CrmError):
    code = "bulk_limit_exceeded"


class InvalidTimelineCursorError(CrmError):
    code = "invalid_cursor"


def _audit(
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    resource: str,
    rid: object,
    metadata: dict[str, object] | None = None,
) -> None:
    audit_log.record(
        tenant_id=tenant_id,
        actor_type=ActorType.USER,
        actor_user_id=actor_id,
        action=action,
        resource_type=resource,
        resource_id=str(rid),
        outcome=AuditOutcome.SUCCESS,
        metadata=metadata,
    )


def _violated(exc: IntegrityError) -> str | None:
    """Name of the violated constraint, so only a genuine uniqueness clash is
    reported as a duplicate (any other integrity error propagates)."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None)


def _require_contact(session, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> None:
    found = session.execute(
        select(Contact.id).where(Contact.tenant_id == tenant_id, Contact.id == contact_id)
    ).scalar_one_or_none()
    if found is None:
        raise ContactNotFoundError()


# --- Tags -------------------------------------------------------------------------


@dataclass(frozen=True)
class TagView:
    tag_id: uuid.UUID
    name: str


def list_tags(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[TagView]:
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    with tenant_session_scope(tenant_id) as session:
        rows = session.execute(
            select(Tag).where(Tag.tenant_id == tenant_id).order_by(Tag.name).limit(MAX_TAGS)
        ).scalars()
        return [TagView(tag_id=r.id, name=r.name) for r in rows]


def create_tag(actor_id: uuid.UUID, tenant_id: uuid.UUID, name: str) -> TagView:
    authorize(actor_id, tenant_id, perms.CRM_SETTINGS_MANAGE)
    name = name.strip()
    if not name or len(name) > 50:
        raise CrmError()
    try:
        with tenant_session_scope(tenant_id) as session:
            acquire_tenant_advisory_lock(session, tenant_id, SETTINGS_LOCK_KEY)
            count = len(session.execute(select(Tag.id).where(Tag.tenant_id == tenant_id)).all())
            if count >= MAX_TAGS:
                raise TagLimitError()
            row = Tag(tenant_id=tenant_id, name=name)
            session.add(row)
            session.flush()
            view = TagView(tag_id=row.id, name=row.name)
    except IntegrityError as exc:
        if _violated(exc) == "uq_tags_tenant_name":
            raise DuplicateTagError() from None
        raise
    _audit(tenant_id, actor_id, "leadstack.tag.created", "leadstack.tag", view.tag_id)
    return view


def delete_tag(actor_id: uuid.UUID, tenant_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    authorize(actor_id, tenant_id, perms.CRM_SETTINGS_MANAGE)
    with tenant_session_scope(tenant_id) as session:
        _require_tag(session, tenant_id, tag_id)
        session.execute(delete(Tag).where(Tag.tenant_id == tenant_id, Tag.id == tag_id))
    _audit(tenant_id, actor_id, "leadstack.tag.deleted", "leadstack.tag", tag_id)


def _require_tag(session, tenant_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    found = session.execute(
        select(Tag.id).where(Tag.tenant_id == tenant_id, Tag.id == tag_id)
    ).scalar_one_or_none()
    if found is None:
        raise TagNotFoundError()


def tags_for_contacts(
    session, tenant_id: uuid.UUID, contact_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TagView]]:
    """Tags of many contacts in one query (no per-contact round trips)."""
    if not contact_ids:
        return {}
    rows = session.execute(
        select(ContactTag.contact_id, Tag.id, Tag.name)
        .join(Tag, (Tag.id == ContactTag.tag_id) & (Tag.tenant_id == ContactTag.tenant_id))
        .where(ContactTag.tenant_id == tenant_id, ContactTag.contact_id.in_(contact_ids))
        .order_by(Tag.name)
    ).all()
    result: dict[uuid.UUID, list[TagView]] = {cid: [] for cid in contact_ids}
    for contact_id, tag_id, name in rows:
        result[contact_id].append(TagView(tag_id=tag_id, name=name))
    return result


def tags_of(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TagView]]:
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    with tenant_session_scope(tenant_id) as session:
        return tags_for_contacts(session, tenant_id, contact_ids)


def visible_custom_fields(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, stored: dict[str, object]
) -> dict[str, object]:
    """Only values that have a definition and still satisfy it."""
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    with tenant_session_scope(tenant_id) as session:
        return cf.visible_values(field_specs(session, tenant_id), stored)


def set_contact_tag(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, tag_id: uuid.UUID, on: bool
) -> list[TagView]:
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    try:
        with tenant_session_scope(tenant_id) as session:
            _require_contact(session, tenant_id, contact_id)
            _require_tag(session, tenant_id, tag_id)
            existing = session.get(ContactTag, (tenant_id, contact_id, tag_id))
            if on and existing is None:
                session.add(ContactTag(tenant_id=tenant_id, contact_id=contact_id, tag_id=tag_id))
            elif not on and existing is not None:
                session.delete(existing)
            session.flush()
    except IntegrityError as exc:
        name = _violated(exc)
        if name == "fk_contact_tags_tag":
            raise TagNotFoundError() from None
        if name == "fk_contact_tags_contact":
            raise ContactNotFoundError() from None
        if name != "contact_tags_pkey":  # a concurrent identical request won: idempotent
            raise
    with tenant_session_scope(tenant_id) as session:
        tags = tags_for_contacts(session, tenant_id, [contact_id])[contact_id]
    _audit(
        tenant_id,
        actor_id,
        "leadstack.contact.tagged" if on else "leadstack.contact.untagged",
        "leadstack.contact",
        contact_id,
        {"tag_id": str(tag_id)},
    )
    return tags


# --- Custom-field definitions --------------------------------------------------------


@dataclass(frozen=True)
class FieldDefinitionView:
    field_id: uuid.UUID
    key: str
    label: str
    field_type: str
    options: list[str] | None


def _definition_view(row: CustomFieldDefinition) -> FieldDefinitionView:
    return FieldDefinitionView(
        field_id=row.id,
        key=row.key,
        label=row.label,
        field_type=row.field_type,
        options=list(row.options) if row.options is not None else None,
    )


def field_specs(session, tenant_id: uuid.UUID) -> dict[str, cf.FieldSpec]:
    rows = session.execute(
        select(CustomFieldDefinition).where(CustomFieldDefinition.tenant_id == tenant_id)
    ).scalars()
    return {
        r.key: cf.FieldSpec(
            key=r.key,
            field_type=r.field_type,
            options=tuple(r.options) if r.options is not None else None,
        )
        for r in rows
    }


def list_field_definitions(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[FieldDefinitionView]:
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    with tenant_session_scope(tenant_id) as session:
        rows = session.execute(
            select(CustomFieldDefinition)
            .where(CustomFieldDefinition.tenant_id == tenant_id)
            .order_by(CustomFieldDefinition.created_at, CustomFieldDefinition.key)
        ).scalars()
        return [_definition_view(r) for r in rows]


def create_field_definition(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    key: str,
    label: str,
    field_type: str,
    options: list[str] | None,
) -> FieldDefinitionView:
    authorize(actor_id, tenant_id, perms.CRM_SETTINGS_MANAGE)
    key, label, field_type, options = cf.validate_definition(key, label, field_type, options)
    try:
        with tenant_session_scope(tenant_id) as session:
            acquire_tenant_advisory_lock(session, tenant_id, SETTINGS_LOCK_KEY)
            if len(field_specs(session, tenant_id)) >= cf.MAX_DEFINITIONS:
                raise FieldDefinitionLimitError()
            row = CustomFieldDefinition(
                tenant_id=tenant_id, key=key, label=label, field_type=field_type, options=options
            )
            session.add(row)
            session.flush()
            view = _definition_view(row)
    except IntegrityError as exc:
        if _violated(exc) == "uq_custom_fields_tenant_key":
            raise DuplicateFieldDefinitionError() from None
        raise
    _audit(tenant_id, actor_id, "leadstack.custom_field.created", "leadstack.custom_field",
           view.field_id, {"type": field_type})  # fmt: skip
    return view


def delete_field_definition(actor_id: uuid.UUID, tenant_id: uuid.UUID, field_id: uuid.UUID) -> None:
    """Deletes the definition and every stored value for its key."""
    authorize(actor_id, tenant_id, perms.CRM_SETTINGS_MANAGE)
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.id == field_id
            )
        ).scalar_one_or_none()
        if row is None:
            raise FieldDefinitionNotFoundError()
        key = row.key
        session.delete(row)
        # Remove the stored values too, so a later definition reusing the
        # key never resurfaces old data (batched; definition deletion is rare).
        last: uuid.UUID | None = None
        while True:
            statement = (
                select(Contact)
                .where(Contact.tenant_id == tenant_id)
                .order_by(Contact.id)
                .limit(STRIP_BATCH)
            )
            if last is not None:
                statement = statement.where(Contact.id > last)
            batch = list(session.execute(statement).scalars())
            if not batch:
                break
            for contact in batch:
                if key in (contact.custom_fields or {}):
                    contact.custom_fields = {
                        k: v for k, v in contact.custom_fields.items() if k != key
                    }
            session.flush()
            last = batch[-1].id
    _audit(
        tenant_id, actor_id, "leadstack.custom_field.deleted", "leadstack.custom_field", field_id
    )


def update_custom_values(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, changes: dict[str, Any]
) -> dict[str, object]:
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(Contact)
            .where(Contact.tenant_id == tenant_id, Contact.id == contact_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise ContactNotFoundError()
        specs = field_specs(session, tenant_id)
        row.custom_fields = cf.apply_values(specs, row.custom_fields or {}, changes)
        row.updated_at = datetime.now(UTC)
        values = dict(row.custom_fields)
    _audit(tenant_id, actor_id, "leadstack.contact.updated", "leadstack.contact", contact_id,
           {"fields": sorted(f"custom.{k}" for k in changes)})  # fmt: skip
    return values


# --- Notes --------------------------------------------------------------------------


@dataclass(frozen=True)
class NoteView:
    note_id: uuid.UUID
    contact_id: uuid.UUID
    body: str
    author_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


def _note_view(row: Note) -> NoteView:
    return NoteView(
        row.id, row.contact_id, row.body, row.author_user_id, row.created_at, row.updated_at
    )


def _clean_body(body: str) -> str:
    body = body.strip()
    if not body or len(body) > 10_000:
        raise CrmError()
    return body


def add_note(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, body: str
) -> NoteView:
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    body = _clean_body(body)
    try:
        view = _insert_note(actor_id, tenant_id, contact_id, body)
    except IntegrityError:
        raise ContactNotFoundError() from None  # contact deleted concurrently
    _audit(tenant_id, actor_id, "leadstack.note.created", "leadstack.note", view.note_id,
           {"contact_id": str(contact_id)})  # fmt: skip
    return view


def _insert_note(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, body: str
) -> NoteView:
    with tenant_session_scope(tenant_id) as session:
        _require_contact(session, tenant_id, contact_id)
        row = Note(tenant_id=tenant_id, contact_id=contact_id, body=body, author_user_id=actor_id)
        session.add(row)
        session.flush()
        session.refresh(row)
        view = _note_view(row)
    return view


def _note_row(session, tenant_id: uuid.UUID, contact_id: uuid.UUID, note_id: uuid.UUID) -> Note:
    row = session.execute(
        select(Note)
        .where(Note.tenant_id == tenant_id, Note.contact_id == contact_id, Note.id == note_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise NoteNotFoundError()
    return row


def edit_note(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, note_id: uuid.UUID, body: str
) -> NoteView:
    """Only the author edits a note (it is their statement)."""
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    body = _clean_body(body)
    with tenant_session_scope(tenant_id) as session:
        row = _note_row(session, tenant_id, contact_id, note_id)
        if row.author_user_id != actor_id:
            raise NotNoteAuthorError()
        row.body = body
        row.updated_at = datetime.now(UTC)
        session.flush()
        view = _note_view(row)
    _audit(tenant_id, actor_id, "leadstack.note.updated", "leadstack.note", note_id)
    return view


class NotNoteAuthorError(CrmError):
    code = "not_note_author"


class NotTaskParticipantError(CrmError):
    code = "not_task_participant"


class ConcurrentChangeError(CrmError):
    code = "concurrent_change"


def delete_note(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, contact_id: uuid.UUID, note_id: uuid.UUID
) -> None:
    """The author, or anyone allowed to delete contacts."""
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    may_delete_any = _holds(actor_id, tenant_id, perms.CONTACTS_DELETE)  # before the session
    with tenant_session_scope(tenant_id) as session:
        row = _note_row(session, tenant_id, contact_id, note_id)
        if row.author_user_id != actor_id and not may_delete_any:
            raise NotNoteAuthorError()
        session.delete(row)
    _audit(tenant_id, actor_id, "leadstack.note.deleted", "leadstack.note", note_id)


# --- Tasks --------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskView:
    task_id: uuid.UUID
    contact_id: uuid.UUID | None
    title: str
    description: str | None
    due_at: datetime | None
    assignee_user_id: uuid.UUID | None
    status: str
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _task_view(row: Task) -> TaskView:
    return TaskView(
        row.id, row.contact_id, row.title, row.description, row.due_at, row.assignee_user_id,
        row.status, row.completed_at, row.created_at, row.updated_at,
    )  # fmt: skip


UNSET: Any = object()


def _task_values(session, tenant_id: uuid.UUID, fields: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "title" in fields:
        title = (fields["title"] or "").strip()
        if not title or len(title) > 200:
            raise InvalidTaskError()
        values["title"] = title
    if "description" in fields:
        description = (fields["description"] or "").strip()
        if len(description) > 10_000:
            raise InvalidTaskError()
        values["description"] = description or None
    if "due_at" in fields:
        due = fields["due_at"]
        if due is not None and due.tzinfo is None:
            raise InvalidTaskError()  # an unambiguous instant is required
        values["due_at"] = due
    if "contact_id" in fields:
        if fields["contact_id"] is not None:
            _require_contact(session, tenant_id, fields["contact_id"])
        values["contact_id"] = fields["contact_id"]
    if "assignee_user_id" in fields:
        # Membership was verified by _check_assignee() before the session opened.
        values["assignee_user_id"] = fields["assignee_user_id"]
    if "status" in fields:
        if fields["status"] not in ("open", "done"):
            raise InvalidTaskError()
        values["status"] = fields["status"]
    return values


def _check_assignee(tenant_id: uuid.UUID, fields: dict[str, Any]) -> None:
    """Runs before any session is opened (it needs its own connection)."""
    assignee = fields.get("assignee_user_id")
    if assignee is not None and not is_active_member(tenant_id, assignee):
        raise InvalidAssigneeError()


def create_task(actor_id: uuid.UUID, tenant_id: uuid.UUID, fields: dict[str, Any]) -> TaskView:
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    if "title" not in fields:
        raise InvalidTaskError()
    _check_assignee(tenant_id, fields)
    try:
        with tenant_session_scope(tenant_id) as session:
            values = _task_values(session, tenant_id, fields)
            if values.get("status") == "done":
                values["completed_at"] = datetime.now(UTC)
            row = Task(tenant_id=tenant_id, created_by_user_id=actor_id, **values)
            session.add(row)
            session.flush()
            session.refresh(row)
            view = _task_view(row)
    except IntegrityError as exc:
        if _violated(exc) == "fk_tasks_contact":
            raise ContactNotFoundError() from None  # contact deleted concurrently
        raise
    _audit(tenant_id, actor_id, "leadstack.task.created", "leadstack.task", view.task_id,
           {"contact_id": str(view.contact_id) if view.contact_id else None})  # fmt: skip
    return view


def _require_participant(row: Task, actor_id: uuid.UUID, may_manage_any: bool) -> None:
    """Tasks are changed by their creator, their assignee, or someone allowed
    to delete contacts (an admin)."""
    if may_manage_any or actor_id in (row.created_by_user_id, row.assignee_user_id):
        return
    raise NotTaskParticipantError()


def update_task(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, task_id: uuid.UUID, fields: dict[str, Any]
) -> TaskView:
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    _check_assignee(tenant_id, fields)
    may_manage_any = _holds(actor_id, tenant_id, perms.CONTACTS_DELETE)
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(Task).where(Task.tenant_id == tenant_id, Task.id == task_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise TaskNotFoundError()
        _require_participant(row, actor_id, may_manage_any)
        values = _task_values(session, tenant_id, fields)
        if "status" in values and values["status"] != row.status:
            values["completed_at"] = datetime.now(UTC) if values["status"] == "done" else None
        for name, value in values.items():
            setattr(row, name, value)
        if values:
            row.updated_at = datetime.now(UTC)
        session.flush()
        view = _task_view(row)
    if values:
        _audit(tenant_id, actor_id, "leadstack.task.updated", "leadstack.task", task_id,
               {"fields": sorted(values)})  # fmt: skip
    return view


def delete_task(actor_id: uuid.UUID, tenant_id: uuid.UUID, task_id: uuid.UUID) -> None:
    authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
    may_manage_any = _holds(actor_id, tenant_id, perms.CONTACTS_DELETE)
    with tenant_session_scope(tenant_id) as session:
        row = session.execute(
            select(Task).where(Task.tenant_id == tenant_id, Task.id == task_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise TaskNotFoundError()
        _require_participant(row, actor_id, may_manage_any)
        session.delete(row)
    _audit(tenant_id, actor_id, "leadstack.task.deleted", "leadstack.task", task_id)


def list_tasks(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    assignee_user_id: uuid.UUID | None,
    contact_id: uuid.UUID | None,
    status: str | None,
    limit: int,
) -> list[TaskView]:
    """Open tasks first by due date (undated last), then done tasks newest
    first. Bounded by `limit` (max 100)."""
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    statement = select(Task).where(Task.tenant_id == tenant_id)
    if assignee_user_id is not None:
        statement = statement.where(Task.assignee_user_id == assignee_user_id)
    if contact_id is not None:
        statement = statement.where(Task.contact_id == contact_id)
    if status is not None:
        if status not in ("open", "done"):
            raise InvalidTaskError()
        statement = statement.where(Task.status == status)
    statement = statement.order_by(
        Task.status.desc(),  # 'open' > 'done'
        Task.due_at.asc().nulls_last(),
        Task.created_at.desc(),
        Task.id,
    ).limit(max(1, min(limit, 100)))
    with tenant_session_scope(tenant_id) as session:
        return [_task_view(r) for r in session.execute(statement).scalars()]


# --- Timeline -----------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineItem:
    item_type: str  # "note" | "task" | "event"
    item_id: uuid.UUID
    at: datetime
    note: NoteView | None = None
    task: TaskView | None = None
    action: str | None = None
    actor_user_id: uuid.UUID | None = None


@dataclass(frozen=True)
class TimelinePage:
    items: tuple[TimelineItem, ...]
    next_cursor: str | None


def _encode_cursor(at: datetime, item_id: uuid.UUID) -> str:
    raw = json.dumps({"a": at.isoformat(), "i": str(item_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        data = json.loads(base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()))
        at, item_id = datetime.fromisoformat(data["a"]), uuid.UUID(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise InvalidTimelineCursorError() from None
    if at.tzinfo is None:
        raise InvalidTimelineCursorError()
    return at, item_id


# Audit events are filtered by position in Python (the platform API has no
# id-based keyset); this headroom covers events sharing the boundary instant.
_AUDIT_TIE_HEADROOM = 50


def timeline(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None,
) -> TimelinePage:
    """Notes, tasks and audited events of one contact, newest first.

    Keyset order is (time, id) across all three sources -- UUIDs compare
    the same way in PostgreSQL and Python -- so equal timestamps neither
    repeat nor drop items and the cursor size is constant."""
    authorize(actor_id, tenant_id, perms.CONTACTS_READ)
    limit = max(1, min(limit, MAX_TIMELINE_PAGE))
    position = _decode_cursor(cursor) if cursor else None
    fetch = limit + 1

    def before(at: datetime, item_id: uuid.UUID) -> bool:
        return position is None or (at, item_id) < position

    with tenant_session_scope(tenant_id) as session:
        _require_contact(session, tenant_id, contact_id)
        note_q = select(Note).where(Note.tenant_id == tenant_id, Note.contact_id == contact_id)
        task_q = select(Task).where(Task.tenant_id == tenant_id, Task.contact_id == contact_id)
        if position is not None:
            at, item_id = position
            note_q = note_q.where(
                (Note.created_at < at) | ((Note.created_at == at) & (Note.id < item_id))
            )
            task_q = task_q.where(
                (Task.created_at < at) | ((Task.created_at == at) & (Task.id < item_id))
            )
        notes = session.execute(
            note_q.order_by(Note.created_at.desc(), Note.id.desc()).limit(fetch)
        ).scalars()
        tasks = session.execute(
            task_q.order_by(Task.created_at.desc(), Task.id.desc()).limit(fetch)
        ).scalars()
        items = [TimelineItem("note", n.id, n.created_at, note=_note_view(n)) for n in notes]
        items += [TimelineItem("task", t.id, t.created_at, task=_task_view(t)) for t in tasks]
    events = audit_log.list(
        tenant_id,
        resource_type="leadstack.contact",
        resource_id=str(contact_id),
        until=position[0] if position else None,
        limit=fetch + _AUDIT_TIE_HEADROOM,
    )
    items += [
        TimelineItem("event", e.id, e.created_at, action=e.action, actor_user_id=e.actor_user_id)
        for e in events
        if before(e.created_at, e.id)
    ]
    items.sort(key=lambda i: (i.at, i.item_id), reverse=True)
    page, more = items[:limit], len(items) > limit
    next_cursor = _encode_cursor(page[-1].at, page[-1].item_id) if more and page else None
    return TimelinePage(items=tuple(page), next_cursor=next_cursor)


# --- Bulk actions -------------------------------------------------------------------


@dataclass(frozen=True)
class BulkResult:
    requested: int
    affected: int


def bulk(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    action: str,
    contact_ids: list[uuid.UUID],
    tag_id: uuid.UUID | None = None,
) -> BulkResult:
    """Tag, untag or delete up to MAX_BULK contacts of this workspace in one
    transaction. Identifiers outside the workspace are ignored (counted as
    not affected), never acted upon. Every affected contact gets its own
    audit record (who deleted or tagged which contact stays traceable),
    plus one summary record."""
    ids = list(dict.fromkeys(contact_ids))
    if not ids or len(ids) > MAX_BULK:
        raise BulkLimitError()
    if action == "delete":
        authorize(actor_id, tenant_id, perms.CONTACTS_DELETE)
    elif action in ("tag", "untag"):
        authorize(actor_id, tenant_id, perms.CONTACTS_UPDATE)
        if tag_id is None:
            raise TagNotFoundError()
    else:
        raise CrmError()
    try:
        with tenant_session_scope(tenant_id) as session:
            owned = list(
                session.execute(
                    select(Contact.id)
                    .where(Contact.tenant_id == tenant_id, Contact.id.in_(ids))
                    .with_for_update()
                ).scalars()
            )
            if action == "delete":
                if owned:
                    session.execute(
                        delete(Contact).where(Contact.tenant_id == tenant_id, Contact.id.in_(owned))
                    )
                affected_ids = owned
            else:
                if tag_id is None:  # guarded above; keeps the type checker informed
                    raise TagNotFoundError()
                _require_tag(session, tenant_id, tag_id)
                linked = set(
                    session.execute(
                        select(ContactTag.contact_id).where(
                            ContactTag.tenant_id == tenant_id,
                            ContactTag.tag_id == tag_id,
                            ContactTag.contact_id.in_(owned),
                        )
                    ).scalars()
                )
                if action == "tag":
                    affected_ids = [cid for cid in owned if cid not in linked]
                    session.add_all(
                        ContactTag(tenant_id=tenant_id, contact_id=cid, tag_id=tag_id)
                        for cid in affected_ids
                    )
                else:
                    affected_ids = [cid for cid in owned if cid in linked]
                    if affected_ids:
                        session.execute(
                            delete(ContactTag).where(
                                ContactTag.tenant_id == tenant_id,
                                ContactTag.tag_id == tag_id,
                                ContactTag.contact_id.in_(affected_ids),
                            )
                        )
                session.flush()
    except IntegrityError as exc:
        if _violated(exc) == "fk_contact_tags_tag":
            raise TagNotFoundError() from None
        raise ConcurrentChangeError() from None  # overlapping request; safe to retry
    per_contact = {
        "delete": "leadstack.contact.deleted",
        "tag": "leadstack.contact.tagged",
        "untag": "leadstack.contact.untagged",
    }[action]
    detail: dict[str, object] | None = {"tag_id": str(tag_id)} if tag_id else None
    for contact_id in affected_ids:
        _audit(tenant_id, actor_id, per_contact, "leadstack.contact", contact_id, detail)
    _audit(tenant_id, actor_id, f"leadstack.contacts.bulk_{action}", "leadstack.contact", "bulk",
           {"requested": len(ids), "affected": len(affected_ids),
            "tag_id": str(tag_id) if tag_id else None})  # fmt: skip
    return BulkResult(requested=len(ids), affected=len(affected_ids))
