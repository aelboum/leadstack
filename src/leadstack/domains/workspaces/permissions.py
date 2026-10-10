"""LeadStack permissions and the role set every workspace receives.

Permissions are registered in the shared SaaS-OS catalog
(`core.rbac.register_permission`, idempotent). Roles are per tenant
(`core.rbac.create_role`); each workspace gets `owner`, `admin` and
`member`, reconciled idempotently by `service.ensure_workspace_roles()`.

SaaS-OS's own capabilities are granted alongside LeadStack's where SaaS-OS
checks them itself: `invitation:create`/`invitation:revoke` (checked inside
`create_invitation`/`revoke_invitation`) and `membership_role:create`
(checked inside `assign_role`, which additionally requires the actor to hold
every permission the assigned role carries -- so an admin can never grant
the owner role).
"""

from __future__ import annotations

from types import MappingProxyType

RESOURCE_WORKSPACE = "leadstack.workspace"
RESOURCE_CLIENTS = "leadstack.clients"
RESOURCE_MEMBERS = "leadstack.members"
RESOURCE_AUDIT = "leadstack.audit"
RESOURCE_OWNERSHIP = "leadstack.ownership"
RESOURCE_CONTACTS = "leadstack.contacts"
RESOURCE_COMPANIES = "leadstack.companies"
RESOURCE_CRM_SETTINGS = "leadstack.crm_settings"
RESOURCE_DEALS = "leadstack.deals"
RESOURCE_PIPELINES = "leadstack.pipelines"
RESOURCE_FORMS = "leadstack.forms"
RESOURCE_WORKFLOWS = "leadstack.workflows"
RESOURCE_MESSAGES = "leadstack.messages"
RESOURCE_CONSENT = "leadstack.consent"
RESOURCE_APPOINTMENTS = "leadstack.appointments"
RESOURCE_BOOKING_TYPES = "leadstack.booking_types"

WORKSPACE_READ = (RESOURCE_WORKSPACE, "read")
WORKSPACE_UPDATE = (RESOURCE_WORKSPACE, "update")
CLIENTS_CREATE = (RESOURCE_CLIENTS, "create")
CLIENTS_READ = (RESOURCE_CLIENTS, "read")
CLIENTS_MANAGE = (RESOURCE_CLIENTS, "manage")
MEMBERS_READ = (RESOURCE_MEMBERS, "read")
MEMBERS_INVITE = (RESOURCE_MEMBERS, "invite")
MEMBERS_MANAGE = (RESOURCE_MEMBERS, "manage")
AUDIT_READ = (RESOURCE_AUDIT, "read")
OWNERSHIP_MANAGE = (RESOURCE_OWNERSHIP, "manage")
CONTACTS_READ = (RESOURCE_CONTACTS, "read")
CONTACTS_CREATE = (RESOURCE_CONTACTS, "create")
CONTACTS_UPDATE = (RESOURCE_CONTACTS, "update")
CONTACTS_DELETE = (RESOURCE_CONTACTS, "delete")
COMPANIES_READ = (RESOURCE_COMPANIES, "read")
COMPANIES_CREATE = (RESOURCE_COMPANIES, "create")
COMPANIES_UPDATE = (RESOURCE_COMPANIES, "update")
COMPANIES_DELETE = (RESOURCE_COMPANIES, "delete")
CRM_SETTINGS_MANAGE = (RESOURCE_CRM_SETTINGS, "manage")  # tags, custom fields
DEALS_READ = (RESOURCE_DEALS, "read")
DEALS_CREATE = (RESOURCE_DEALS, "create")
DEALS_UPDATE = (RESOURCE_DEALS, "update")
DEALS_DELETE = (RESOURCE_DEALS, "delete")
PIPELINES_MANAGE = (RESOURCE_PIPELINES, "manage")
FORMS_READ = (RESOURCE_FORMS, "read")
FORMS_MANAGE = (RESOURCE_FORMS, "manage")
WORKFLOWS_READ = (RESOURCE_WORKFLOWS, "read")
WORKFLOWS_MANAGE = (RESOURCE_WORKFLOWS, "manage")
MESSAGES_SEND = (RESOURCE_MESSAGES, "send")
CONSENT_MANAGE = (RESOURCE_CONSENT, "manage")  # record a manual grant
APPOINTMENTS_READ = (RESOURCE_APPOINTMENTS, "read")
APPOINTMENTS_MANAGE = (RESOURCE_APPOINTMENTS, "manage")  # manual booking, cancel
BOOKING_TYPES_MANAGE = (RESOURCE_BOOKING_TYPES, "manage")  # types and availability

# Checked by SaaS-OS itself.
PLATFORM_INVITATION_CREATE = ("invitation", "create")
PLATFORM_INVITATION_REVOKE = ("invitation", "revoke")
PLATFORM_ROLE_ASSIGN = ("membership_role", "create")

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER)

_MEMBER = frozenset(
    {
        WORKSPACE_READ,
        MEMBERS_READ,
        CONTACTS_READ,
        CONTACTS_CREATE,
        CONTACTS_UPDATE,
        COMPANIES_READ,
        COMPANIES_CREATE,
        COMPANIES_UPDATE,
        DEALS_READ,
        DEALS_CREATE,
        DEALS_UPDATE,
        FORMS_READ,
        WORKFLOWS_READ,
        MESSAGES_SEND,
        APPOINTMENTS_READ,
        APPOINTMENTS_MANAGE,
    }
)
_ADMIN = _MEMBER | {
    WORKFLOWS_MANAGE,
    FORMS_MANAGE,
    DEALS_DELETE,
    PIPELINES_MANAGE,
    CONTACTS_DELETE,
    CRM_SETTINGS_MANAGE,
    COMPANIES_DELETE,
    WORKSPACE_UPDATE,
    CLIENTS_CREATE,
    CLIENTS_READ,
    CLIENTS_MANAGE,
    MEMBERS_INVITE,
    MEMBERS_MANAGE,
    CONSENT_MANAGE,
    BOOKING_TYPES_MANAGE,
    AUDIT_READ,
    PLATFORM_INVITATION_CREATE,
    PLATFORM_INVITATION_REVOKE,
    PLATFORM_ROLE_ASSIGN,
}
_OWNER = _ADMIN | {OWNERSHIP_MANAGE}

ROLE_PERMISSIONS: MappingProxyType[str, frozenset[tuple[str, str]]] = MappingProxyType(
    {ROLE_OWNER: frozenset(_OWNER), ROLE_ADMIN: frozenset(_ADMIN), ROLE_MEMBER: _MEMBER}
)

ALL_PERMISSIONS: frozenset[tuple[str, str]] = frozenset().union(*ROLE_PERMISSIONS.values())
