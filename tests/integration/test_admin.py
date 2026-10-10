"""`leadstack-admin sync-roles` restores permissions missing from existing
workspaces (how a release's new permissions reach them)."""

from __future__ import annotations

import uuid

import pytest
from core.rbac import get_role_permission, list_roles, register_permission, revoke_permission

from leadstack.admin import sync_roles
from leadstack.domains.workspaces import permissions as perms
from tests.integration.test_workspaces import _create

pytestmark = pytest.mark.integration


def test_sync_roles_grants_missing_permissions(api, make_user) -> None:
    owner = make_user()
    tenant = uuid.UUID(_create(api, owner)["tenant_id"])
    member_role = next(r for r in list_roles(tenant) if r.name == perms.ROLE_MEMBER)
    permission = register_permission(*perms.CONTACTS_READ)
    revoke_permission(tenant, member_role.id, permission.id)  # as if added by a later release
    assert get_role_permission(tenant, member_role.id, permission.id) is None

    synced, _ = sync_roles()

    assert synced >= 1
    assert get_role_permission(tenant, member_role.id, permission.id) is not None
    sync_roles()  # idempotent
