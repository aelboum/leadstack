"""Tags, custom fields, notes, tasks, contact timeline and bulk actions.

All routes are tenant routes behind `require_permission` (see
routes/workspaces.py); the domain re-checks authorization.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from api.context import RequestContext
from api.dependencies import require_permission
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from leadstack.domains.crm import activity
from leadstack.domains.crm import service as crm
from leadstack.domains.workspaces import permissions as perms
from leadstack.routes.pagination import CursorQuery, LimitQuery
from leadstack.routes.validation import StrictModel

router = APIRouter(tags=["crm-activity"])

W = "/workspaces/{tenant_id}"


def _require(permission: tuple[str, str]):
    resource, action = permission
    return Depends(require_permission(resource, action))


NoteBody = Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
TagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
CustomValue = str | int | bool | None


# --- Tags -------------------------------------------------------------------------


class TagIn(StrictModel):
    name: TagName


class TagOut(BaseModel):
    tag_id: uuid.UUID
    name: str


@router.get(f"{W}/tags", response_model=list[TagOut])
def list_tags(context: RequestContext = _require(perms.CONTACTS_READ)):
    return [TagOut(**t.__dict__) for t in activity.list_tags(context.actor_id, context.tenant_id)]


@router.post(f"{W}/tags", status_code=status.HTTP_201_CREATED, response_model=TagOut)
def create_tag(body: TagIn, context: RequestContext = _require(perms.CRM_SETTINGS_MANAGE)):
    return TagOut(**activity.create_tag(context.actor_id, context.tenant_id, body.name).__dict__)


@router.delete(f"{W}/tags/{{tag_id}}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: uuid.UUID, context: RequestContext = _require(perms.CRM_SETTINGS_MANAGE)):
    activity.delete_tag(context.actor_id, context.tenant_id, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(f"{W}/contacts/{{contact_id}}/tags/{{tag_id}}", response_model=list[TagOut])
def tag_contact(
    contact_id: uuid.UUID,
    tag_id: uuid.UUID,
    context: RequestContext = _require(perms.CONTACTS_UPDATE),
):
    tags = activity.set_contact_tag(context.actor_id, context.tenant_id, contact_id, tag_id, True)
    return [TagOut(**t.__dict__) for t in tags]


@router.delete(f"{W}/contacts/{{contact_id}}/tags/{{tag_id}}", response_model=list[TagOut])
def untag_contact(
    contact_id: uuid.UUID,
    tag_id: uuid.UUID,
    context: RequestContext = _require(perms.CONTACTS_UPDATE),
):
    tags = activity.set_contact_tag(context.actor_id, context.tenant_id, contact_id, tag_id, False)
    return [TagOut(**t.__dict__) for t in tags]


# --- Custom fields ---------------------------------------------------------------------


class FieldDefinitionIn(StrictModel):
    key: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    label: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    field_type: Literal["text", "number", "date", "boolean", "select"]
    options: list[Annotated[str, StringConstraints(max_length=100)]] | None = Field(
        default=None, max_length=50
    )


class FieldDefinitionOut(BaseModel):
    field_id: uuid.UUID
    key: str
    label: str
    field_type: str
    options: list[str] | None


class CustomValuesIn(StrictModel):
    values: dict[Annotated[str, StringConstraints(max_length=64)], CustomValue] = Field(
        max_length=50
    )


@router.get(f"{W}/custom-fields", response_model=list[FieldDefinitionOut])
def list_custom_fields(context: RequestContext = _require(perms.CONTACTS_READ)):
    items = activity.list_field_definitions(context.actor_id, context.tenant_id)
    return [FieldDefinitionOut(**d.__dict__) for d in items]


@router.post(
    f"{W}/custom-fields", status_code=status.HTTP_201_CREATED, response_model=FieldDefinitionOut
)
def create_custom_field(
    body: FieldDefinitionIn, context: RequestContext = _require(perms.CRM_SETTINGS_MANAGE)
):
    view = activity.create_field_definition(
        context.actor_id,
        context.tenant_id,
        key=body.key,
        label=body.label,
        field_type=body.field_type,
        options=body.options,
    )
    return FieldDefinitionOut(**view.__dict__)


@router.delete(f"{W}/custom-fields/{{field_id}}", status_code=status.HTTP_204_NO_CONTENT)
def delete_custom_field(
    field_id: uuid.UUID, context: RequestContext = _require(perms.CRM_SETTINGS_MANAGE)
):
    activity.delete_field_definition(context.actor_id, context.tenant_id, field_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(f"{W}/contacts/{{contact_id}}/custom-fields", response_model=dict[str, CustomValue])
def set_custom_values(
    contact_id: uuid.UUID,
    body: CustomValuesIn,
    context: RequestContext = _require(perms.CONTACTS_UPDATE),
):
    return activity.update_custom_values(
        context.actor_id, context.tenant_id, contact_id, dict(body.values)
    )


# --- Contact detail (with tags and visible custom fields) ------------------------------


class ContactDetail(BaseModel):
    contact_id: uuid.UUID
    first_name: str | None
    last_name: str | None
    email: str | None
    phone_e164: str | None
    company_id: uuid.UUID | None
    job_title: str | None
    source: str | None
    owner_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    custom_fields: dict[str, CustomValue]
    tags: list[TagOut]


@router.get(f"{W}/contacts/{{contact_id}}/detail", response_model=ContactDetail)
def contact_detail(contact_id: uuid.UUID, context: RequestContext = _require(perms.CONTACTS_READ)):
    view = crm.get_contact(context.actor_id, context.tenant_id, contact_id)
    tags = activity.tags_of(context.actor_id, context.tenant_id, [contact_id])[contact_id]
    fields = view.__dict__ | {
        "custom_fields": activity.visible_custom_fields(
            context.actor_id, context.tenant_id, view.custom_fields
        ),
        "tags": [TagOut(**t.__dict__) for t in tags],
    }
    return ContactDetail(**fields)


# --- Notes -------------------------------------------------------------------------


class NoteIn(StrictModel):
    body: NoteBody


class NoteOut(BaseModel):
    note_id: uuid.UUID
    contact_id: uuid.UUID
    body: str
    author_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


@router.post(
    f"{W}/contacts/{{contact_id}}/notes",
    status_code=status.HTTP_201_CREATED,
    response_model=NoteOut,
)
def add_note(
    contact_id: uuid.UUID, body: NoteIn, context: RequestContext = _require(perms.CONTACTS_UPDATE)
):
    note = activity.add_note(context.actor_id, context.tenant_id, contact_id, body.body)
    return NoteOut(**note.__dict__)


@router.patch(f"{W}/contacts/{{contact_id}}/notes/{{note_id}}", response_model=NoteOut)
def edit_note(
    contact_id: uuid.UUID,
    note_id: uuid.UUID,
    body: NoteIn,
    context: RequestContext = _require(perms.CONTACTS_UPDATE),
):
    note = activity.edit_note(context.actor_id, context.tenant_id, contact_id, note_id, body.body)
    return NoteOut(**note.__dict__)


@router.delete(
    f"{W}/contacts/{{contact_id}}/notes/{{note_id}}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_note(
    contact_id: uuid.UUID,
    note_id: uuid.UUID,
    context: RequestContext = _require(perms.CONTACTS_UPDATE),
):
    activity.delete_note(context.actor_id, context.tenant_id, contact_id, note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Tasks -------------------------------------------------------------------------


class TaskIn(StrictModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    description: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    due_at: AwareDatetime | None = None
    contact_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    status: Literal["open", "done"] = "open"


class TaskPatch(StrictModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    description: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    due_at: AwareDatetime | None = None
    contact_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    status: Literal["open", "done"] | None = None


class TaskOut(BaseModel):
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


def _sent(body: BaseModel) -> dict[str, object]:
    return {name: getattr(body, name) for name in body.model_fields_set}


@router.post(f"{W}/tasks", status_code=status.HTTP_201_CREATED, response_model=TaskOut)
def create_task(body: TaskIn, context: RequestContext = _require(perms.CONTACTS_UPDATE)):
    fields = _sent(body) | {"title": body.title, "status": body.status}
    return TaskOut(**activity.create_task(context.actor_id, context.tenant_id, fields).__dict__)


@router.get(f"{W}/tasks", response_model=list[TaskOut])
def list_tasks(
    assignee: Literal["me"] | uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    status_filter: Literal["open", "done"] | None = Query(None, alias="status"),
    limit: int = LimitQuery,
    context: RequestContext = _require(perms.CONTACTS_READ),
):
    assignee_id = context.actor_id if assignee == "me" else assignee
    tasks = activity.list_tasks(
        context.actor_id,
        context.tenant_id,
        assignee_user_id=assignee_id if isinstance(assignee_id, uuid.UUID) else None,
        contact_id=contact_id,
        status=status_filter,
        limit=limit,
    )
    return [TaskOut(**t.__dict__) for t in tasks]


@router.patch(f"{W}/tasks/{{task_id}}", response_model=TaskOut)
def update_task(
    task_id: uuid.UUID, body: TaskPatch, context: RequestContext = _require(perms.CONTACTS_UPDATE)
):
    fields = _sent(body)
    if "title" in fields and fields["title"] is None:
        fields.pop("title")  # a title cannot be cleared
    if "status" in fields and fields["status"] is None:
        fields.pop("status")
    task = activity.update_task(context.actor_id, context.tenant_id, task_id, fields)
    return TaskOut(**task.__dict__)


@router.delete(f"{W}/tasks/{{task_id}}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: uuid.UUID, context: RequestContext = _require(perms.CONTACTS_UPDATE)):
    activity.delete_task(context.actor_id, context.tenant_id, task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Timeline ----------------------------------------------------------------------


class TimelineItemOut(BaseModel):
    item_type: str
    item_id: uuid.UUID
    at: datetime
    note: NoteOut | None = None
    task: TaskOut | None = None
    action: str | None = None
    actor_user_id: uuid.UUID | None = None


class TimelineOut(BaseModel):
    items: list[TimelineItemOut]
    next_cursor: str | None


@router.get(f"{W}/contacts/{{contact_id}}/timeline", response_model=TimelineOut)
def timeline(
    contact_id: uuid.UUID,
    limit: int = LimitQuery,
    cursor: str | None = CursorQuery,
    context: RequestContext = _require(perms.CONTACTS_READ),
):
    page = activity.timeline(
        context.actor_id, context.tenant_id, contact_id, limit=limit, cursor=cursor
    )
    return TimelineOut(
        items=[
            TimelineItemOut(
                item_type=i.item_type,
                item_id=i.item_id,
                at=i.at,
                note=NoteOut(**i.note.__dict__) if i.note else None,
                task=TaskOut(**i.task.__dict__) if i.task else None,
                action=i.action,
                actor_user_id=i.actor_user_id,
            )
            for i in page.items
        ],
        next_cursor=page.next_cursor,
    )


# --- Bulk --------------------------------------------------------------------------


class BulkIn(StrictModel):
    action: Literal["tag", "untag", "delete"]
    contact_ids: list[uuid.UUID] = Field(min_length=1, max_length=activity.MAX_BULK)
    tag_id: uuid.UUID | None = None


class BulkOut(BaseModel):
    requested: int
    affected: int


@router.post(f"{W}/contacts/bulk", response_model=BulkOut)
def bulk(body: BulkIn, context: RequestContext = _require(perms.CONTACTS_UPDATE)):
    # `delete` additionally requires leadstack.contacts:delete (checked in the domain).
    result = activity.bulk(
        context.actor_id,
        context.tenant_id,
        action=body.action,
        contact_ids=body.contact_ids,
        tag_id=body.tag_id,
    )
    return BulkOut(**result.__dict__)
