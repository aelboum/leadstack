"""Workspace business operations, composed from SaaS-OS tenancy, identity,
RBAC and audit services.

Every operation takes the *verified* acting user and re-checks
authorization through `core.rbac.can()` itself, in addition to the HTTP
ingress chain -- business rules never live only in a route. Tenant-owned
reads and writes run inside `infra.db.tenant_session_scope()` with the
verified tenant, plus explicit ownership filters on top of RLS.

Agency staff reach a client workspace only through a direct membership in
that client (docs/adr/0002-tenancy-model.md, option b). Hierarchy alone
grants nothing.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core import audit_log
from core.audit_log import ActorType, AuditOutcome
from core.identity import (
    InvalidMembershipTransitionError,
    InvitationAlreadyAcceptedError,
    InvitationInvalidError,
    MembershipStatus,
    TenantMembership,
    accept_invitation,
    add_tenant_membership,
    compute_invitation_status,
    create_invitation,
    get_membership,
    list_invitations_for_tenant,
    list_tenant_members,
    reactivate_membership,
    revoke_invitation,
    revoke_membership,
    suspend_membership,
)
from core.identity import (
    InvitationNotFoundError as PlatformInvitationNotFoundError,
)
from core.rbac import (
    DuplicatePermissionGrantError,
    DuplicateRoleAssignmentError,
    DuplicateRoleNameError,
    RoleAssignmentNotAuthorizedError,
    assign_first_role_for_new_tenant,
    assign_role,
    can,
    create_role,
    get_role_permission,
    grant_permission,
    list_membership_roles,
    list_roles,
    register_permission,
    remove_role,
)
from core.tenancy import (
    InvalidTenantTransitionError,
    Tenant,
    TenantNotFoundError,
    TenantStatus,
    create_tenant,
    get_descendant_ids,
    get_tenant,
    is_accessible_to_principals,
    transition_tenant_status,
)
from infra.db import (
    IntegrityError,
    acquire_tenant_advisory_lock,
    delete,
    select,
    session_scope,
    tenant_session_scope,
)

from leadstack.domains import regional
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.errors import (
    ClientNotFoundError,
    InvalidLifecycleTransitionError,
    InvalidRoleError,
    InvalidWorkspaceKindError,
    InvitationAlreadyUsedError,
    InvitationNotFoundError,
    InvitationRejectedError,
    MemberNotFoundError,
    NotAnAgencyError,
    OwnerProtectionError,
    RoleGrantNotAuthorizedError,
    SelfManagementError,
    WorkspaceNotFoundError,
)
from leadstack.domains.workspaces.models import InvitationGrant, UserWorkspace, Workspace

logger = logging.getLogger(__name__)

AUDIT_RESOURCE_WORKSPACE = "leadstack.workspace"
AUDIT_RESOURCE_MEMBER = "leadstack.member"
AUDIT_RESOURCE_INVITATION = "leadstack.invitation"
MAX_LISTED_WORKSPACES = 200
MAX_AUDIT_PAGE = 100
# Invitations conferring management rights are bearer tokens not bound to an
# email (SaaS-OS users carry none, ADR-0004), so they live for less time.
PRIVILEGED_INVITATION_LIFETIME = timedelta(hours=72)
MEMBERSHIP_LOCK_KEY = "leadstack.membership"
# The mutex holds one pooled connection for the whole operation while
# SaaS-OS calls inside it check out others. Bounding concurrent holders per
# process keeps the pool (SaaS-OS default 5 + 10 overflow) from being
# exhausted by waiters, which would otherwise starve the lock holder.
_MUTEX_SLOTS = threading.BoundedSemaphore(2)
# Concurrent SaaS-OS `can()` evaluations per process (see _bounded_can). The
# default suits SaaS-OS's default pool (5 + 10 overflow); raise it together
# with DB_POOL_SIZE / DB_POOL_MAX_OVERFLOW.
_AUTHZ_SLOTS = threading.BoundedSemaphore(
    max(1, int(os.environ.get("LEADSTACK_MAX_CONCURRENT_AUTHZ", "4")))
)


class PermissionDeniedError(Exception):
    code = "forbidden"


# --- Views -------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceView:
    tenant_id: uuid.UUID
    kind: str
    display_name: str
    locale: str
    time_zone: str
    currency: str
    country: str | None
    status: str
    parent_tenant_id: uuid.UUID | None


@dataclass(frozen=True)
class MemberView:
    membership_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class InvitationView:
    invitation_id: uuid.UUID
    email: str
    role: str | None
    status: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class CreatedInvitation:
    invitation: InvitationView
    token: str


@dataclass(frozen=True)
class AcceptedInvitation:
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    role: str | None


@dataclass(frozen=True)
class AuditEntryView:
    entry_id: uuid.UUID
    created_at: datetime
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    actor_type: str
    actor_user_id: uuid.UUID | None


@dataclass(frozen=True)
class AuditPage:
    items: tuple[AuditEntryView, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class RegionalSettings:
    locale: str
    time_zone: str
    currency: str
    country: str | None

    @classmethod
    def validated(
        cls, *, locale: str, time_zone: str, currency: str, country: str | None
    ) -> RegionalSettings:
        return cls(
            locale=regional.validate_locale(locale),
            time_zone=regional.validate_time_zone(time_zone),
            currency=regional.validate_currency(currency),
            country=regional.validate_country(country) if country is not None else None,
        )


# --- Authorization -------------------------------------------------------------


def _bounded_can(actor_id: uuid.UUID, tenant_id: uuid.UUID, resource: str, action: str) -> bool:
    """`core.rbac.can()` holds one pooled connection while its helpers check
    out more (measured nesting depth 2 at the pinned SaaS-OS commit). With
    many concurrent callers every caller can hold one connection while
    waiting for a second, exhausting the pool (QueuePool timeout -> 500).
    Bounding concurrent calls per process keeps peak demand below the pool
    size. Upstream defect: docs/architecture/saas-os-integration.md."""
    with _AUTHZ_SLOTS:
        return can(actor_id=actor_id, tenant_id=tenant_id, action=action, resource=resource)


def authorize(actor_id: uuid.UUID, tenant_id: uuid.UUID, permission: tuple[str, str]) -> None:
    resource, action = permission
    if not _bounded_can(actor_id, tenant_id, resource, action):
        raise PermissionDeniedError()


def _holds(actor_id: uuid.UUID, tenant_id: uuid.UUID, permission: tuple[str, str]) -> bool:
    resource, action = permission
    return _bounded_can(actor_id, tenant_id, resource, action)


@contextmanager
def _membership_mutex(tenant_id: uuid.UUID) -> Iterator[None]:
    """Serialize membership-changing operations within one workspace. The
    transaction holding the PostgreSQL advisory lock stays open for the
    whole operation, so check-then-act sequences (owner counts, role
    replacement, invitation acceptance) cannot interleave."""
    with _MUTEX_SLOTS, tenant_session_scope(tenant_id) as session:
        acquire_tenant_advisory_lock(session, tenant_id, MEMBERSHIP_LOCK_KEY)
        yield


# --- Roles ------------------------------------------------------------------


def ensure_workspace_roles(tenant_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Create any missing LeadStack role in `tenant_id` and grant any missing
    permission. Idempotent and safe to re-run (also how permissions added by
    a later release reach existing workspaces). Never revokes."""
    roles = {role.name: role.id for role in list_roles(tenant_id)}
    for name in perms.ROLES:
        if name not in roles:
            try:
                roles[name] = create_role(tenant_id, name).id
            except DuplicateRoleNameError:
                roles = {role.name: role.id for role in list_roles(tenant_id)}
    for name, granted in perms.ROLE_PERMISSIONS.items():
        for resource, action in sorted(granted):
            permission_id = register_permission(resource, action).id
            if get_role_permission(tenant_id, roles[name], permission_id) is None:
                try:
                    grant_permission(tenant_id, roles[name], permission_id)
                except DuplicatePermissionGrantError:
                    pass
    return {name: roles[name] for name in perms.ROLES}


def _role_names(tenant_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return {role.id: role.name for role in list_roles(tenant_id)}


def _member_roles(
    tenant_id: uuid.UUID, membership_id: uuid.UUID, names: dict[uuid.UUID, str]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            names.get(assignment.role_id, "?")
            for assignment in list_membership_roles(tenant_id, membership_id)
        )
    )


# --- Workspaces ------------------------------------------------------------------


def _audit(
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: object,
    *,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    metadata: dict[str, object] | None = None,
) -> None:
    audit_log.record(
        tenant_id=tenant_id,
        actor_type=ActorType.USER,
        actor_user_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        outcome=outcome,
        metadata=metadata,
    )


def _index_workspace(user_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    try:
        with session_scope() as session:
            session.add(UserWorkspace(user_id=user_id, tenant_id=tenant_id))
    except IntegrityError:
        pass  # already indexed


def _provision(
    actor_id: uuid.UUID,
    *,
    kind: str,
    display_name: str,
    settings: RegionalSettings,
    parent_id: uuid.UUID | None,
) -> WorkspaceView:
    """Create a tenant and make `actor_id` its owner. SaaS-OS operations are
    separate transactions, so the tenant is created PENDING and only
    activated once every step succeeded; on failure it is moved to DELETED
    (never left half-provisioned and usable)."""
    tenant = create_tenant(display_name, parent_id=parent_id)
    try:
        membership = add_tenant_membership(tenant.id, actor_id)
        roles = ensure_workspace_roles(tenant.id)
        assign_first_role_for_new_tenant(tenant.id, membership.id, roles[perms.ROLE_OWNER])
        with tenant_session_scope(tenant.id) as session:
            session.add(
                Workspace(
                    tenant_id=tenant.id,
                    kind=kind,
                    display_name=display_name,
                    locale=settings.locale,
                    time_zone=settings.time_zone,
                    currency=settings.currency,
                    country=settings.country,
                    created_by_user_id=actor_id,
                )
            )
        _index_workspace(actor_id, tenant.id)
        transition_tenant_status(tenant.id, TenantStatus.ACTIVE, actor_user_id=actor_id)
        _audit(
            tenant.id,
            actor_id,
            "leadstack.workspace.created",
            AUDIT_RESOURCE_WORKSPACE,
            tenant.id,
            metadata={"kind": kind, "parent_tenant_id": str(parent_id) if parent_id else None},
        )
    except Exception:
        try:
            transition_tenant_status(tenant.id, TenantStatus.DELETED, actor_user_id=actor_id)
        except Exception:
            logger.exception(
                "workspace_provisioning_cleanup_failed", extra={"tenant_id": str(tenant.id)}
            )
        raise
    return get_workspace(actor_id, tenant.id)


def create_workspace(
    actor_id: uuid.UUID, *, kind: str, display_name: str, settings: RegionalSettings
) -> WorkspaceView:
    """A new root workspace -- an agency, or a direct customer -- owned by
    the acting user. Client workspaces are created by their agency."""
    if kind not in ("agency", "direct"):
        raise InvalidWorkspaceKindError()
    return _provision(
        actor_id, kind=kind, display_name=display_name, settings=settings, parent_id=None
    )


def _load_profile(tenant: Tenant) -> WorkspaceView | None:
    with tenant_session_scope(tenant.id) as session:
        row = session.execute(
            select(Workspace).where(Workspace.tenant_id == tenant.id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return WorkspaceView(
            tenant_id=row.tenant_id,
            kind=row.kind,
            display_name=row.display_name,
            locale=row.locale,
            time_zone=row.time_zone,
            currency=row.currency,
            country=row.country,
            status=str(tenant.status),
            parent_tenant_id=tenant.parent_id,
        )


def _get_tenant(tenant_id: uuid.UUID) -> Tenant:
    try:
        return get_tenant(tenant_id)
    except TenantNotFoundError:
        raise WorkspaceNotFoundError() from None


def workspace_profile(tenant_id: uuid.UUID) -> WorkspaceView:
    """System use (public endpoints, emails); no actor, no authorization."""
    view = _load_profile(_get_tenant(tenant_id))
    if view is None:
        raise WorkspaceNotFoundError()
    return view


def workspace_display_name(tenant_id: uuid.UUID) -> str:
    """System use (e.g. naming the sender in a consent email); no actor."""
    return workspace_profile(tenant_id).display_name


def get_workspace(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> WorkspaceView:
    authorize(actor_id, tenant_id, perms.WORKSPACE_READ)
    view = _load_profile(_get_tenant(tenant_id))
    if view is None:
        raise WorkspaceNotFoundError()
    return view


def list_my_workspaces(actor_id: uuid.UUID) -> list[WorkspaceView]:
    """Workspaces where the user has an ACTIVE membership in an accessible
    tenant. The index only nominates candidates; each is re-validated."""
    with session_scope() as session:
        candidates = list(
            session.execute(
                select(UserWorkspace.tenant_id)
                .where(UserWorkspace.user_id == actor_id)
                .order_by(UserWorkspace.created_at.desc())
                .limit(MAX_LISTED_WORKSPACES)
            ).scalars()
        )
    views: list[WorkspaceView] = []
    dead: list[uuid.UUID] = []
    for tenant_id in candidates:
        try:
            tenant = get_tenant(tenant_id)
        except TenantNotFoundError:
            dead.append(tenant_id)
            continue
        if TenantStatus(tenant.status) in (
            TenantStatus.DELETED,
            TenantStatus.PURGING,
            TenantStatus.PURGED,
        ):
            dead.append(tenant_id)
            continue
        if not is_accessible_to_principals(TenantStatus(tenant.status)):
            continue
        membership = get_membership(tenant_id, actor_id)
        if membership is None or membership.status == MembershipStatus.REVOKED.value:
            dead.append(tenant_id)
            continue
        if membership.status != MembershipStatus.ACTIVE.value:
            continue
        if not _holds(actor_id, tenant_id, perms.WORKSPACE_READ):
            continue
        view = _load_profile(tenant)
        if view is not None:
            views.append(view)
    if dead:
        # Permanently dead hints only (revoked membership, closed tenant);
        # suspended ones may come back and are kept.
        with session_scope() as session:
            session.execute(
                delete(UserWorkspace).where(
                    UserWorkspace.user_id == actor_id, UserWorkspace.tenant_id.in_(dead)
                )
            )
    return views


def update_workspace(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    display_name: str | None = None,
    locale: str | None = None,
    time_zone: str | None = None,
    currency: str | None = None,
    country: str | None = None,
    clear_country: bool = False,
) -> WorkspaceView:
    authorize(actor_id, tenant_id, perms.WORKSPACE_UPDATE)
    changes: dict[str, object] = {}
    if display_name is not None:
        changes["display_name"] = display_name
    if locale is not None:
        changes["locale"] = regional.validate_locale(locale)
    if time_zone is not None:
        changes["time_zone"] = regional.validate_time_zone(time_zone)
    if currency is not None:
        changes["currency"] = regional.validate_currency(currency)
    if country is not None:
        changes["country"] = regional.validate_country(country)
    elif clear_country:
        changes["country"] = None
    tenant = _get_tenant(tenant_id)
    if changes:
        with tenant_session_scope(tenant_id) as session:
            row = session.execute(
                select(Workspace).where(Workspace.tenant_id == tenant_id).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise WorkspaceNotFoundError()
            for field, value in changes.items():
                setattr(row, field, value)
            row.updated_at = datetime.now(UTC)
        _audit(
            tenant_id,
            actor_id,
            "leadstack.workspace.updated",
            AUDIT_RESOURCE_WORKSPACE,
            tenant_id,
            metadata={"fields": sorted(changes)},
        )
    view = _load_profile(tenant)
    if view is None:
        raise WorkspaceNotFoundError()
    return view


# --- Agency clients ----------------------------------------------------------


def _require_agency(actor_id: uuid.UUID, agency_id: uuid.UUID) -> WorkspaceView:
    agency = get_workspace(actor_id, agency_id)
    if agency.kind != "agency":
        raise NotAnAgencyError()
    return agency


def create_client(
    actor_id: uuid.UUID,
    agency_id: uuid.UUID,
    *,
    display_name: str,
    locale: str | None = None,
    time_zone: str | None = None,
    currency: str | None = None,
    country: str | None = None,
) -> WorkspaceView:
    """A client workspace under `agency_id`. Regional settings default to
    the agency's. The acting user becomes the client's owner through a
    direct membership (ADR-0002 option b)."""
    authorize(actor_id, agency_id, perms.CLIENTS_CREATE)
    agency = _require_agency(actor_id, agency_id)
    settings = RegionalSettings.validated(
        locale=locale or agency.locale,
        time_zone=time_zone or agency.time_zone,
        currency=currency or agency.currency,
        country=country if country is not None else agency.country,
    )
    client = _provision(
        actor_id, kind="client", display_name=display_name, settings=settings, parent_id=agency_id
    )
    _audit(
        agency_id,
        actor_id,
        "leadstack.client.created",
        AUDIT_RESOURCE_WORKSPACE,
        client.tenant_id,
    )
    return client


def _client_tenant(agency_id: uuid.UUID, client_id: uuid.UUID) -> Tenant:
    try:
        tenant = get_tenant(client_id)
    except TenantNotFoundError:
        raise ClientNotFoundError() from None
    if tenant.parent_id != agency_id:
        raise ClientNotFoundError()
    return tenant


def list_clients(actor_id: uuid.UUID, agency_id: uuid.UUID) -> list[WorkspaceView]:
    """The agency's direct client workspaces, including suspended ones;
    deleted clients are omitted. Authorized by the agency-level permission:
    an agency may see its own clients' names and status without being a
    member of each (it cannot see any client data)."""
    authorize(actor_id, agency_id, perms.CLIENTS_READ)
    _require_agency(actor_id, agency_id)
    views: list[WorkspaceView] = []
    for client_id in sorted(get_descendant_ids(agency_id), key=str):
        if client_id == agency_id:
            continue
        try:
            tenant = _client_tenant(agency_id, client_id)
        except ClientNotFoundError:
            continue
        if TenantStatus(tenant.status) not in (TenantStatus.ACTIVE, TenantStatus.SUSPENDED):
            continue
        view = _load_profile(tenant)
        if view is not None:
            views.append(view)
    return views


def set_client_status(
    actor_id: uuid.UUID, agency_id: uuid.UUID, client_id: uuid.UUID, target: str
) -> WorkspaceView:
    """Suspend or reactivate a client workspace. Suspension denies all of
    the client's principals (SaaS-OS lifecycle fence); data is untouched."""
    authorize(actor_id, agency_id, perms.CLIENTS_MANAGE)
    _require_agency(actor_id, agency_id)
    tenant = _client_tenant(agency_id, client_id)
    try:
        target_status = {"active": TenantStatus.ACTIVE, "suspended": TenantStatus.SUSPENDED}[target]
    except KeyError:
        raise InvalidLifecycleTransitionError() from None
    if TenantStatus(tenant.status) not in (TenantStatus.ACTIVE, TenantStatus.SUSPENDED):
        raise ClientNotFoundError()
    try:
        tenant = transition_tenant_status(client_id, target_status, actor_user_id=actor_id)
    except InvalidTenantTransitionError:
        raise InvalidLifecycleTransitionError() from None
    for audited_tenant in (agency_id, client_id):
        _audit(
            audited_tenant,
            actor_id,
            f"leadstack.client.{target_status.value}",
            AUDIT_RESOURCE_WORKSPACE,
            client_id,
        )
    view = _load_profile(tenant)
    if view is None:
        raise ClientNotFoundError()
    return view


# --- Members -------------------------------------------------------------------


def list_members(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[MemberView]:
    authorize(actor_id, tenant_id, perms.MEMBERS_READ)
    names = _role_names(tenant_id)
    return [
        MemberView(
            membership_id=membership.id,
            user_id=membership.user_id,
            status=membership.status,
            roles=_member_roles(tenant_id, membership.id, names),
        )
        for membership in list_tenant_members(tenant_id)
    ]


def _find_member(tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership:
    for membership in list_tenant_members(tenant_id):
        if membership.id == membership_id:
            return membership
    raise MemberNotFoundError()


def _is_owner(actor_id: uuid.UUID, tenant_id: uuid.UUID, names: dict[uuid.UUID, str]) -> bool:
    """Ownership is a role held through the actor's own ACTIVE membership,
    never something obtained by delegation."""
    membership = get_membership(tenant_id, actor_id)
    return (
        membership is not None
        and membership.status == MembershipStatus.ACTIVE.value
        and perms.ROLE_OWNER in _member_roles(tenant_id, membership.id, names)
    )


def _active_owner_count(tenant_id: uuid.UUID, names: dict[uuid.UUID, str]) -> int:
    return sum(
        1
        for membership in list_tenant_members(tenant_id)
        if membership.status == MembershipStatus.ACTIVE.value
        and perms.ROLE_OWNER in _member_roles(tenant_id, membership.id, names)
    )


def _guard_target(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, target: TenantMembership
) -> tuple[dict[uuid.UUID, str], bool]:
    """Common checks before changing another member. Returns the role-name
    map and whether the target is an owner."""
    if target.user_id == actor_id:
        raise SelfManagementError()
    names = _role_names(tenant_id)
    target_is_owner = perms.ROLE_OWNER in _member_roles(tenant_id, target.id, names)
    if target_is_owner and not _is_owner(actor_id, tenant_id, names):
        raise OwnerProtectionError()
    return names, target_is_owner


def change_member_role(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, membership_id: uuid.UUID, role: str
) -> MemberView:
    """Give a member exactly one LeadStack role. Granting `owner` needs the
    ownership permission; SaaS-OS's `assign_role` additionally refuses any
    role carrying a permission the actor does not hold."""
    authorize(actor_id, tenant_id, perms.MEMBERS_MANAGE)
    if role not in perms.ROLES:
        raise InvalidRoleError()
    with _membership_mutex(tenant_id):
        return _change_member_role(actor_id, tenant_id, membership_id, role)


def _change_member_role(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, membership_id: uuid.UUID, role: str
) -> MemberView:
    target = _find_member(tenant_id, membership_id)
    names, target_is_owner = _guard_target(actor_id, tenant_id, target)
    if role == perms.ROLE_OWNER and not _is_owner(actor_id, tenant_id, names):
        raise OwnerProtectionError()
    if (
        target_is_owner
        and role != perms.ROLE_OWNER
        and target.status == MembershipStatus.ACTIVE.value
        and _active_owner_count(tenant_id, names) <= 1
    ):
        raise OwnerProtectionError()
    role_ids = ensure_workspace_roles(tenant_id)
    try:
        assign_role(tenant_id, membership_id, role_ids[role], actor_user_id=actor_id)
    except DuplicateRoleAssignmentError:
        pass
    except RoleAssignmentNotAuthorizedError:
        raise RoleGrantNotAuthorizedError() from None
    for assignment in list_membership_roles(tenant_id, membership_id):
        if assignment.role_id != role_ids[role] and names.get(assignment.role_id) in perms.ROLES:
            remove_role(tenant_id, membership_id, assignment.role_id)
    _audit(
        tenant_id,
        actor_id,
        "leadstack.member.role_changed",
        AUDIT_RESOURCE_MEMBER,
        membership_id,
        metadata={"role": role},
    )
    return MemberView(
        membership_id=target.id,
        user_id=target.user_id,
        status=target.status,
        roles=_member_roles(tenant_id, membership_id, _role_names(tenant_id)),
    )


_STATUS_ACTIONS = {
    "suspend": suspend_membership,
    "reactivate": reactivate_membership,
    "revoke": revoke_membership,
}


def set_member_status(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, membership_id: uuid.UUID, action: str
) -> MemberView:
    authorize(actor_id, tenant_id, perms.MEMBERS_MANAGE)
    if action not in _STATUS_ACTIONS:
        raise InvalidLifecycleTransitionError()
    with _membership_mutex(tenant_id):
        return _set_member_status(actor_id, tenant_id, membership_id, action)


def _set_member_status(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, membership_id: uuid.UUID, action: str
) -> MemberView:
    target = _find_member(tenant_id, membership_id)
    names, target_is_owner = _guard_target(actor_id, tenant_id, target)
    if (
        action in ("suspend", "revoke")
        and target_is_owner
        and target.status == MembershipStatus.ACTIVE.value
        and _active_owner_count(tenant_id, names) <= 1
    ):
        raise OwnerProtectionError()
    try:
        updated = _STATUS_ACTIONS[action](tenant_id, membership_id, actor_user_id=actor_id)
    except InvalidMembershipTransitionError:
        raise InvalidLifecycleTransitionError() from None
    return MemberView(
        membership_id=updated.id,
        user_id=updated.user_id,
        status=updated.status,
        roles=_member_roles(tenant_id, membership_id, names),
    )


# --- Invitations -----------------------------------------------------------------


def _grants(tenant_id: uuid.UUID) -> dict[uuid.UUID, str]:
    with tenant_session_scope(tenant_id) as session:
        rows = session.execute(
            select(InvitationGrant).where(InvitationGrant.tenant_id == tenant_id)
        ).scalars()
        return {row.invitation_id: row.role for row in rows}


def invite_member(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, *, email: str, role: str
) -> CreatedInvitation:
    """Create an invitation conferring `role`. The actor must hold every
    permission of that role (checked now, and again by SaaS-OS when the role
    is granted at acceptance). Returns the one-time token exactly once; only
    its hash is stored."""
    authorize(actor_id, tenant_id, perms.MEMBERS_INVITE)
    if role not in perms.ROLES:
        raise InvalidRoleError()
    if not all(_holds(actor_id, tenant_id, p) for p in perms.ROLE_PERMISSIONS[role]):
        raise RoleGrantNotAuthorizedError()
    ensure_workspace_roles(tenant_id)
    expires_at = (
        datetime.now(UTC) + PRIVILEGED_INVITATION_LIFETIME
        if role in (perms.ROLE_OWNER, perms.ROLE_ADMIN)
        else None  # SaaS-OS default (7 days)
    )
    invitation, token = create_invitation(tenant_id, actor_id, email, expires_at=expires_at)
    try:
        with tenant_session_scope(tenant_id) as session:
            session.add(
                InvitationGrant(invitation_id=invitation.id, tenant_id=tenant_id, role=role)
            )
    except Exception:
        # Never leave a live invitation without its role record.
        revoke_invitation(tenant_id, invitation.id, actor_user_id=actor_id)
        raise
    _audit(
        tenant_id,
        actor_id,
        "leadstack.invitation.created",
        AUDIT_RESOURCE_INVITATION,
        invitation.id,
        metadata={"role": role},
    )
    view = InvitationView(
        invitation_id=invitation.id,
        email=invitation.invited_email,
        role=role,
        status=compute_invitation_status(invitation).value,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )
    return CreatedInvitation(invitation=view, token=token)


def list_invitations(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[InvitationView]:
    """Invitee emails are third-party personal data: visible only to those
    who may invite."""
    authorize(actor_id, tenant_id, perms.MEMBERS_INVITE)
    grants = _grants(tenant_id)
    return [
        InvitationView(
            invitation_id=invitation.id,
            email=invitation.invited_email,
            role=grants.get(invitation.id),
            status=compute_invitation_status(invitation).value,
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
        )
        for invitation in sorted(
            list_invitations_for_tenant(tenant_id), key=lambda i: i.created_at, reverse=True
        )
    ]


def cancel_invitation(actor_id: uuid.UUID, tenant_id: uuid.UUID, invitation_id: uuid.UUID) -> None:
    authorize(actor_id, tenant_id, perms.MEMBERS_INVITE)
    try:
        revoke_invitation(tenant_id, invitation_id, actor_user_id=actor_id)
    except (PlatformInvitationNotFoundError, InvitationInvalidError):
        raise InvitationNotFoundError() from None
    except InvitationAlreadyAcceptedError:
        raise InvitationAlreadyUsedError() from None


def accept(actor_id: uuid.UUID, tenant_id: uuid.UUID, token: str) -> AcceptedInvitation:
    """Redeem an invitation for the acting user. The token is the bearer
    credential; `tenant_id` only routes the lookup.

    - Serialized per workspace (membership mutex), so the accepted
      invitation can be identified unambiguously afterwards.
    - The invited role is granted under the *inviter's* authority, which
      SaaS-OS re-checks now: an inviter who has since lost that authority
      cannot confer it (the membership is then created with no role, and
      the refusal is audited).
    - An existing member keeps their current roles; accepting an invitation
      never adds a second LeadStack role or downgrades anyone.
    """
    with _membership_mutex(tenant_id):
        before = get_membership(tenant_id, actor_id)
        names = _role_names(tenant_id)
        existing_roles = (
            set(_member_roles(tenant_id, before.id, names)) & set(perms.ROLES)
            if before is not None
            else set()
        )
        accepted_before = {
            i.id for i in list_invitations_for_tenant(tenant_id) if i.accepted_at is not None
        }
        try:
            membership = accept_invitation(token, actor_id, tenant_id)
        except (InvitationInvalidError, InvalidMembershipTransitionError):
            # Unknown/expired/revoked/used token, closed workspace, or an
            # existing suspended/revoked membership: one indistinguishable error.
            raise InvitationRejectedError() from None
        _index_workspace(actor_id, tenant_id)
        (invitation,) = [
            i
            for i in list_invitations_for_tenant(tenant_id)
            if i.accepted_by_user_id == actor_id
            and i.accepted_at is not None
            and i.id not in accepted_before
        ]
        role = _grants(tenant_id).get(invitation.id)
        granted: str | None = None
        if existing_roles:
            granted = sorted(existing_roles)[0] if len(existing_roles) == 1 else None
        elif role is not None:
            role_ids = ensure_workspace_roles(tenant_id)
            try:
                assign_role(
                    tenant_id,
                    membership.id,
                    role_ids[role],
                    actor_user_id=invitation.inviter_user_id,
                )
                granted = role
            except DuplicateRoleAssignmentError:
                granted = role
            except RoleAssignmentNotAuthorizedError:
                _audit(
                    tenant_id,
                    actor_id,
                    "leadstack.invitation.role_grant_refused",
                    AUDIT_RESOURCE_INVITATION,
                    invitation.id,
                    outcome=AuditOutcome.DENIED,
                    metadata={"role": role},
                )
    _audit(
        tenant_id,
        actor_id,
        "leadstack.invitation.accepted",
        AUDIT_RESOURCE_INVITATION,
        invitation.id,
        metadata={"role": granted, "existing_member": bool(existing_roles)},
    )
    return AcceptedInvitation(tenant_id=tenant_id, membership_id=membership.id, role=granted)


# --- Audit trail -------------------------------------------------------------------


def _encode_cursor(until: datetime, skip: list[uuid.UUID]) -> str:
    raw = json.dumps({"u": until.isoformat(), "s": [str(i) for i in skip]})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, frozenset[uuid.UUID]]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        until = datetime.fromisoformat(data["u"])
        skip = frozenset(uuid.UUID(s) for s in data["s"])
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise InvalidCursorError() from None
    if until.tzinfo is None or len(skip) > MAX_AUDIT_PAGE:
        raise InvalidCursorError()
    return until, skip


class InvalidCursorError(ValueError):
    code = "invalid_cursor"


def list_audit(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, *, limit: int, cursor: str | None = None
) -> AuditPage:
    """Newest first. The cursor carries the last timestamp seen and the ids
    already returned at that exact timestamp, so equal timestamps neither
    repeat nor drop entries. Metadata is not exposed."""
    authorize(actor_id, tenant_id, perms.AUDIT_READ)
    limit = max(1, min(limit, MAX_AUDIT_PAGE))
    until, skip = _decode_cursor(cursor) if cursor else (None, frozenset())
    fetched = audit_log.list(tenant_id, until=until, limit=limit + len(skip) + 1)
    entries = [entry for entry in fetched if entry.id not in skip]
    page, more = entries[:limit], len(entries) > limit
    next_cursor = None
    if more and page:
        last = page[-1].created_at
        same = [e.id for e in page if e.created_at == last]
        if until == last:
            same.extend(skip)
        next_cursor = _encode_cursor(last, same)
    return AuditPage(
        items=tuple(
            AuditEntryView(
                entry_id=entry.id,
                created_at=entry.created_at,
                action=entry.action,
                resource_type=entry.resource_type,
                resource_id=entry.resource_id,
                outcome=entry.outcome,
                actor_type=entry.actor_type,
                actor_user_id=entry.actor_user_id,
            )
            for entry in page
        ),
        next_cursor=next_cursor,
    )


# --- Cross-domain helpers ----------------------------------------------------------


def workspace_country(tenant_id: uuid.UUID) -> str | None:
    """The workspace's country (default region for phone numbers), read in
    the caller's already-authorized tenant. Performs no authorization: use
    only after the caller has authorized an action in `tenant_id`."""
    with tenant_session_scope(tenant_id) as session:
        return session.execute(
            select(Workspace.country).where(Workspace.tenant_id == tenant_id)
        ).scalar_one_or_none()


def is_active_member(tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    membership = get_membership(tenant_id, user_id)
    return membership is not None and membership.status == MembershipStatus.ACTIVE.value
