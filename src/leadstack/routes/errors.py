"""Error responses for LeadStack routes.

Convention (docs/architecture/api-conventions.md): every error body is
`{"detail": ...}`, matching SaaS-OS's own errors and FastAPI's validation
errors. LeadStack's own errors make `detail` an object with a stable
machine-readable `code` and a human-readable `message`:

    {"detail": {"code": "workspace_not_found", "message": "Workspace not found."}}

Messages never include identifiers, internal state or exception text.
"""

from __future__ import annotations

from core.identity import (
    DuplicateInvitationError,
    InvalidInvitationEmailError,
    InvitationNotAuthorizedError,
)
from core.identity import MembershipNotFoundError as PlatformMembershipNotFoundError
from core.tenancy import TenantHierarchyDepthExceededError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from leadstack.domains.automation import service as automation
from leadstack.domains.crm import activity
from leadstack.domains.crm import custom_fields as cf
from leadstack.domains.crm import service as crm
from leadstack.domains.crm.contact_points import InvalidEmailError, InvalidPhoneError
from leadstack.domains.forms import service as forms
from leadstack.domains.messaging import links
from leadstack.domains.messaging import service as messaging
from leadstack.domains.money import InvalidAmountError
from leadstack.domains.regional import RegionalValueError
from leadstack.domains.sales import service as sales
from leadstack.domains.scheduling import service as scheduling
from leadstack.domains.workspaces import errors as ws
from leadstack.domains.workspaces.service import InvalidCursorError, PermissionDeniedError

_STATUS: dict[type[Exception], tuple[int, str]] = {
    ws.WorkspaceNotFoundError: (404, "Workspace not found."),
    ws.ClientNotFoundError: (404, "Client workspace not found."),
    ws.MemberNotFoundError: (404, "Member not found."),
    ws.InvitationNotFoundError: (404, "Invitation not found."),
    PermissionDeniedError: (403, "You do not have permission to perform this action."),
    ws.OwnerProtectionError: (
        403,
        "Only owners can change owners, and a workspace must keep an active owner.",
    ),
    ws.SelfManagementError: (403, "You cannot change your own membership."),
    ws.RoleGrantNotAuthorizedError: (403, "You cannot grant a role with more access than yours."),
    ws.InvalidRoleError: (422, "Unknown role."),
    ws.InvalidWorkspaceKindError: (422, "Unsupported workspace kind."),
    ws.NotAnAgencyError: (409, "This action is only available to agency workspaces."),
    ws.InvalidLifecycleTransitionError: (409, "This status change is not allowed."),
    ws.InvitationRejectedError: (400, "This invitation is invalid or no longer usable."),
    ws.InvitationAlreadyUsedError: (409, "This invitation has already been accepted."),
    crm.ContactNotFoundError: (404, "Contact not found."),
    crm.CompanyNotFoundError: (404, "Company not found."),
    crm.UnknownCompanyReferenceError: (422, "Unknown company."),
    crm.InvalidOwnerError: (422, "The owner must be an active member of this workspace."),
    crm.EmptyContactError: (422, "Provide a name, an email address or a phone number."),
    crm.InvalidCursorError: (422, "Invalid pagination cursor."),
    crm.FieldTooLongError: (422, "A field is too long."),
    crm.CompanyNameRequiredError: (422, "A company name is required."),
    crm.InvalidWebsiteError: (422, "The website must be an http or https address."),
    activity.TagNotFoundError: (404, "Tag not found."),
    activity.DuplicateTagError: (409, "A tag with this name already exists."),
    activity.TagLimitError: (409, "This workspace has reached its tag limit."),
    activity.NoteNotFoundError: (404, "Note not found."),
    activity.NotNoteAuthorError: (403, "Only the author can change this note."),
    activity.TaskNotFoundError: (404, "Task not found."),
    activity.NotTaskParticipantError: (
        403,
        "Only the task's creator, its assignee or an admin can change it.",
    ),
    activity.ConcurrentChangeError: (409, "These contacts changed meanwhile. Please retry."),
    activity.InvalidTaskError: (422, "Invalid task."),
    activity.InvalidAssigneeError: (
        422,
        "The assignee must be an active member of this workspace.",
    ),
    activity.FieldDefinitionNotFoundError: (404, "Custom field not found."),
    activity.DuplicateFieldDefinitionError: (409, "A custom field with this key already exists."),
    activity.FieldDefinitionLimitError: (409, "This workspace has reached its custom-field limit."),
    activity.BulkLimitError: (422, "Select between 1 and 500 contacts."),
    activity.InvalidTimelineCursorError: (422, "Invalid pagination cursor."),
    cf.UnknownCustomFieldError: (422, "Unknown custom field."),
    cf.InvalidCustomFieldValueError: (422, "Invalid value for a custom field."),
    cf.CustomFieldError: (422, "Invalid custom field definition."),
    crm.CrmError: (422, "Invalid request."),
    sales.PipelineNotFoundError: (404, "Pipeline not found."),
    sales.StageNotFoundError: (404, "Stage not found in this pipeline."),
    sales.DealNotFoundError: (404, "Deal not found."),
    sales.DuplicatePipelineError: (409, "A pipeline with this name already exists."),
    sales.PipelineInUseError: (409, "Move or delete this pipeline's deals first."),
    sales.InvalidStagesError: (422, "Invalid stages."),
    sales.InvalidDealError: (422, "Invalid deal."),
    sales.InvalidDealOwnerError: (422, "The owner must be an active member of this workspace."),
    sales.UnknownReferenceError: (422, "Unknown contact or company."),
    sales.InvalidCursorError: (422, "Invalid pagination cursor."),
    sales.LimitReachedError: (409, "This workspace has reached its pipeline limit."),
    sales.SalesError: (422, "Invalid request."),
    InvalidAmountError: (422, "Invalid amount for this currency."),
    automation.WorkflowNotFoundError: (404, "Workflow not found."),
    automation.InvalidWorkflowError: (422, "Invalid workflow."),
    automation.WorkflowLimitError: (409, "This workspace has reached its workflow limit."),
    messaging.NoEmailAddressError: (422, "This contact has no email address."),
    messaging.ConsentWithdrawnError: (409, "This contact has unsubscribed from emails."),
    messaging.WithdrawnByContactError: (
        409,
        "This contact unsubscribed themselves; only they can subscribe again.",
    ),
    messaging.InvalidMessageError: (422, "Invalid message."),
    links.InvalidLinkError: (400, "This link is invalid or has expired."),
    scheduling.BookingTypeNotFoundError: (404, "Booking page not found."),
    scheduling.AppointmentNotFoundError: (404, "Appointment not found."),
    scheduling.InvalidBookingTypeError: (422, "Invalid booking settings."),
    scheduling.BookingTypeLimitError: (409, "Too many booking types in this workspace."),
    scheduling.InvalidHostError: (422, "The host must be an active member of this workspace."),
    scheduling.SlotUnavailableError: (409, "This time is no longer available."),
    scheduling.InvalidRangeError: (422, "Invalid date range."),
    scheduling.RangeTooLargeError: (422, "Too many appointments in this range; narrow it."),
    scheduling.BookingRejectedError: (422, "Please check your details and try again."),
    scheduling.BookingQuotaExceededError: (429, "Booking is temporarily unavailable."),
    forms.FormNotFoundError: (404, "Form not found."),
    forms.InvalidFormDefinitionError: (422, "Invalid form definition."),
    forms.SubmissionRejectedError: (422, "Please check the form and try again."),
    forms.FormLimitError: (409, "This workspace has reached its form limit."),
    forms.FormQuotaExceededError: (429, "This form is receiving too many submissions. Try later."),
    InvalidPhoneError: (422, "Invalid phone number."),
    InvalidEmailError: (422, "Invalid email address."),
}

# SaaS-OS errors that can surface through LeadStack operations.
_PLATFORM: dict[type[Exception], tuple[int, str, str]] = {
    InvitationNotAuthorizedError: (
        403,
        "forbidden",
        "You do not have permission to perform this action.",
    ),
    PlatformMembershipNotFoundError: (404, "member_not_found", "Member not found."),
    links.LinkConfigurationError: (503, "unavailable", "This service is temporarily unavailable."),
    messaging.MessagingConfigurationError: (
        503,
        "unavailable",
        "This service is temporarily unavailable.",
    ),
    forms.FormConfigurationError: (
        503,
        "unavailable",
        "This service is temporarily unavailable.",
    ),
    TenantHierarchyDepthExceededError: (
        409,
        "hierarchy_too_deep",
        "Workspaces cannot be nested this deeply.",
    ),
}


def error_body(code: str, message: str) -> dict[str, object]:
    return {"detail": {"code": code, "message": message}}


def _response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(code, message))


async def _domain_error(_: Request, exc: Exception) -> JSONResponse:
    for error_type, (status, message) in _STATUS.items():
        if isinstance(exc, error_type):
            return _response(status, getattr(exc, "code", "error"), message)
    raise exc


async def _platform_error(_: Request, exc: Exception) -> JSONResponse:
    for error_type, (status, code, message) in _PLATFORM.items():
        if isinstance(exc, error_type):
            return _response(status, code, message)
    raise exc


async def _regional_error(_: Request, exc: Exception) -> JSONResponse:
    field = getattr(exc, "field", "value")
    return _response(422, f"unsupported_{field}", f"Unsupported {field.replace('_', ' ')}.")


async def _cursor_error(_: Request, __: Exception) -> JSONResponse:
    return _response(422, "invalid_cursor", "Invalid pagination cursor.")


async def _invitation_email_error(_: Request, __: Exception) -> JSONResponse:
    return _response(422, "invalid_email", "Invalid email address.")


async def _duplicate_invitation(_: Request, __: Exception) -> JSONResponse:
    return _response(409, "invitation_exists", "A pending invitation already exists.")


def install_error_handlers(app: FastAPI) -> None:
    for error_type in _STATUS:
        app.add_exception_handler(error_type, _domain_error)
    for error_type in _PLATFORM:
        app.add_exception_handler(error_type, _platform_error)
    app.add_exception_handler(RegionalValueError, _regional_error)
    app.add_exception_handler(InvalidCursorError, _cursor_error)
    app.add_exception_handler(InvalidInvitationEmailError, _invitation_email_error)
    app.add_exception_handler(DuplicateInvitationError, _duplicate_invitation)
