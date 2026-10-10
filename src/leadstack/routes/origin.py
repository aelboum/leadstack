"""Same-origin guard for state-changing API requests (CSRF defense in depth).

The SaaS-OS session cookie is `SameSite=Lax` and the platform performs no
Origin or CSRF-token check of its own. Lax stops cross-*site* requests, but
not requests from another subdomain of the same registrable domain (for
example a future published landing page). This middleware rejects any
POST/PUT/PATCH/DELETE to `/v1/*` whose `Origin` header names a different
origin than the application's own (`PUBLIC_APP_ORIGIN`).

Requests without an `Origin` header (server-to-server clients, tests) are
not affected: browsers always send `Origin` on cross-origin state-changing
requests. When `PUBLIC_APP_ORIGIN` is not configured, the request's own
`Host` defines the allowed origin.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _normalize(origin: str) -> str | None:
    parts = urlsplit(origin.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    default = {"http": 80, "https": 443}[parts.scheme]
    port = parts.port or default
    return f"{parts.scheme}://{parts.hostname.lower()}:{port}"


class SameOriginMiddleware:
    def __init__(self, app: ASGIApp, allowed_origin: str | None = None) -> None:
        self.app = app
        configured = allowed_origin or os.environ.get("PUBLIC_APP_ORIGIN")
        self.allowed = _normalize(configured) if configured else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in _UNSAFE
            and scope["path"].startswith("/v1/")
            # Public endpoints are meant for other origins (embedded forms);
            # they use no session, so there is nothing to forge.
            and not scope["path"].startswith("/v1/public/")
        ):
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
            }
            origin = headers.get("origin")
            if origin is not None and _normalize(origin) != self._expected(scope, headers):
                response = JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "cross_origin_rejected",
                            "message": "Cross-origin requests are not allowed.",
                        }
                    },
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _expected(self, scope: Scope, headers: dict[str, str]) -> str | None:
        if self.allowed is not None:
            return self.allowed
        host = headers.get("host")
        return _normalize(f"{scope.get('scheme', 'http')}://{host}") if host else None
