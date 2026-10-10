"""Workspace domain errors. Each carries a stable machine-readable `code`;
the HTTP layer maps them to responses (leadstack.routes.errors)."""

from __future__ import annotations


class WorkspaceError(Exception):
    code = "workspace_error"


class WorkspaceNotFoundError(WorkspaceError):
    """Unknown workspace, or one the caller may not see -- deliberately the
    same error, so existence is never disclosed."""

    code = "workspace_not_found"


class NotAnAgencyError(WorkspaceError):
    code = "not_an_agency"


class InvalidWorkspaceKindError(WorkspaceError):
    code = "invalid_workspace_kind"


class MemberNotFoundError(WorkspaceError):
    code = "member_not_found"


class OwnerProtectionError(WorkspaceError):
    """The change would let a non-owner act on an owner, or leave the
    workspace without an active owner."""

    code = "owner_protected"


class SelfManagementError(WorkspaceError):
    code = "cannot_manage_self"


class InvalidRoleError(WorkspaceError):
    code = "invalid_role"


class RoleGrantNotAuthorizedError(WorkspaceError):
    code = "role_grant_not_authorized"


class InvitationNotFoundError(WorkspaceError):
    code = "invitation_not_found"


class InvitationAlreadyUsedError(WorkspaceError):
    code = "invitation_already_accepted"


class InvitationRejectedError(WorkspaceError):
    """Unknown, expired, revoked, already used, or for a closed workspace --
    never distinguished (mirrors SaaS-OS's own non-enumerating error)."""

    code = "invitation_invalid"


class ClientNotFoundError(WorkspaceError):
    code = "client_not_found"


class InvalidLifecycleTransitionError(WorkspaceError):
    code = "invalid_lifecycle_transition"
