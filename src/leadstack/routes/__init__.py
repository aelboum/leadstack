"""LeadStack's HTTP routes.

Every product route lives under `/v1`: the SaaS-OS single-origin edge
(SaaS-OS ADR-0024) sends only `/auth/*` and `/v1/*` to the backend, and
every other path to the frontend.
"""

from __future__ import annotations

from fastapi import APIRouter

from leadstack.routes.automation import router as automation_router
from leadstack.routes.crm import router as crm_router
from leadstack.routes.crm_activity import router as crm_activity_router
from leadstack.routes.forms import public_router as public_forms_router
from leadstack.routes.forms import router as forms_router
from leadstack.routes.messaging import public_router as public_consent_router
from leadstack.routes.messaging import router as messaging_router
from leadstack.routes.meta import router as meta_router
from leadstack.routes.sales import router as sales_router
from leadstack.routes.scheduling import public_router as public_booking_router
from leadstack.routes.scheduling import router as scheduling_router
from leadstack.routes.workspaces import router as workspaces_router

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(meta_router)
v1_router.include_router(workspaces_router)
v1_router.include_router(crm_router)
v1_router.include_router(crm_activity_router)
v1_router.include_router(sales_router)
v1_router.include_router(forms_router)
v1_router.include_router(public_forms_router)
v1_router.include_router(automation_router)
v1_router.include_router(messaging_router)
v1_router.include_router(public_consent_router)
v1_router.include_router(scheduling_router)
v1_router.include_router(public_booking_router)

__all__ = ["v1_router"]
