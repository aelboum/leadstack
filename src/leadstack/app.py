"""LeadStack's HTTP application composition root.

LeadStack owns its own application (SaaS-OS ADR-0017): it builds the
FastAPI app through SaaS-OS's reusable `api.platform.build_platform_app()`
and mounts its own routers on top. It never imports `api.main:app` or
`api.server` -- an import-linter contract in `pyproject.toml` enforces that.

What the platform builder contributes, unchanged:

- lifespan: structured logging and tracing setup, then the fail-closed
  database-role guard (`infra.db.validate_application_role`) -- the app
  refuses to start under a superuser or BYPASSRLS role;
- request correlation-ID middleware;
- `/healthz` (liveness) and `/readyz` (a real `SELECT 1` against
  PostgreSQL and a real `PING` against Redis);
- `/auth/*` OIDC login routes.

Run with the factory form, so importing this module never builds an app:

    uvicorn --factory leadstack.app:create_app
"""

from __future__ import annotations

from api.platform import build_platform_app
from fastapi import FastAPI

from leadstack import __version__
from leadstack.domains.workspaces.purge import register_purge_participant
from leadstack.routes import v1_router
from leadstack.routes.errors import install_error_handlers
from leadstack.routes.forms import PublicBodyGuardMiddleware, PublicCorsMiddleware
from leadstack.routes.origin import SameOriginMiddleware

APP_TITLE = "LeadStack"


def create_app() -> FastAPI:
    app = build_platform_app(title=APP_TITLE, version=__version__)
    app.include_router(v1_router)
    install_error_handlers(app)
    app.add_middleware(SameOriginMiddleware)
    app.add_middleware(PublicBodyGuardMiddleware)
    # Added last = outermost: CORS headers also reach 413/429 answers.
    app.add_middleware(PublicCorsMiddleware)
    register_purge_participant()
    return app
