"""LeadStack's tenant purge participant.

SaaS-OS `core.tenancy.purge_tenant()` erases its own `core.*` tenant data,
then calls every registered product participant while the tenant is locked
in PURGING. This participant deletes every LeadStack row of the tenant.
It is idempotent: deleting rows that are already gone is a no-op, as the
contract requires.

Interface note: the participant contract lives in
`core.tenancy.purge_participants`, which SaaS-OS does not re-export from
`core.tenancy.__all__`. It is the mechanism SaaS-OS documents for products
(and exercises with its own reference consumer), so LeadStack uses it here,
in this one module only (docs/architecture/saas-os-integration.md).

Purging a tenant is destructive and LeadStack exposes no route for it.
Registering the participant guarantees that LeadStack data is included
whenever an operator does purge a tenant from a LeadStack process.
"""

from __future__ import annotations

import uuid

from core.tenancy.purge_participants import (
    DuplicateTenantPurgeParticipantError,
    default_registry,
)
from infra.db import delete, session_scope, tenant_session_scope

from leadstack.domains.automation.models import Event, Workflow, WorkflowRun
from leadstack.domains.crm.activity_models import (
    ContactTag,
    CustomFieldDefinition,
    Note,
    Tag,
    Task,
)
from leadstack.domains.crm.models import Company, Contact
from leadstack.domains.forms.models import Form, FormKey, FormSubmission
from leadstack.domains.messaging.models import ContactConsent, EmailSuppression, Message
from leadstack.domains.sales.models import Deal, DealStageHistory, Pipeline, PipelineStage
from leadstack.domains.scheduling.models import Appointment, BookingKey, BookingType
from leadstack.domains.workspaces.models import InvitationGrant, UserWorkspace, Workspace

PARTICIPANT_NAME = "leadstack"

# Children before parents (foreign keys).
_TENANT_TABLES = (
    Appointment,
    BookingType,
    Message,
    ContactConsent,
    EmailSuppression,
    WorkflowRun,
    Workflow,
    Event,
    FormSubmission,
    Form,
    DealStageHistory,
    Deal,
    PipelineStage,
    Pipeline,
    ContactTag,
    Note,
    Task,
    Contact,
    Company,
    Tag,
    CustomFieldDefinition,
    InvitationGrant,
    Workspace,
)


class LeadStackPurgeParticipant:
    @property
    def name(self) -> str:
        return PARTICIPANT_NAME

    def purge_tenant_data(self, tenant_id: uuid.UUID) -> None:
        with tenant_session_scope(tenant_id) as session:
            for model in _TENANT_TABLES:
                session.execute(delete(model).where(model.tenant_id == tenant_id))
        # The user-keyed index has no tenant RLS; filter explicitly.
        with session_scope() as session:
            session.execute(delete(UserWorkspace).where(UserWorkspace.tenant_id == tenant_id))
            session.execute(delete(FormKey).where(FormKey.tenant_id == tenant_id))
            session.execute(delete(BookingKey).where(BookingKey.tenant_id == tenant_id))


def register_purge_participant() -> None:
    """Idempotent: safe to call from every process entrypoint."""
    try:
        default_registry().register(LeadStackPurgeParticipant())
    except DuplicateTenantPurgeParticipantError:
        pass
