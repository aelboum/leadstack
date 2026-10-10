"""The HTTP application is built through SaaS-OS's platform builder and
mounts LeadStack's own routes. No database or Redis is reachable here (see
tests/conftest.py), so readiness must report unhealthy -- never healthy just
because clients could be constructed."""

from __future__ import annotations

from fastapi.testclient import TestClient

from leadstack.app import create_app


def test_app_is_composed_from_platform_builder_and_product_routes() -> None:
    app = create_app()
    client = TestClient(app)  # no lifespan: these routes need no dependency

    assert app.title == "LeadStack"
    assert app.debug is False
    # The platform's auth router is mounted (fails closed without a session):
    assert client.get("/auth/me").json() == {"detail": "Authentication required."}
    assert client.get("/auth/me").status_code == 401
    # LeadStack's own router, under the agreed /v1 namespace:
    assert client.get("/v1/meta").status_code == 200
    # The SaaS-OS dev application's tenant-status route is not mounted by the
    # builder; answering here would mean api.main's app leaked in.
    tenant_status = client.get("/v1/tenants/00000000-0000-0000-0000-000000000001/status")
    assert tenant_status.status_code == 404
    assert tenant_status.json() == {"detail": "Not Found"}


def test_liveness_returns_200() -> None:
    client = TestClient(create_app())  # no lifespan: liveness needs no dependency

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_meta_route() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/meta")

    assert response.status_code == 200
    assert response.json() == {"product": "leadstack", "api_version": "v1"}


def test_readiness_reports_unreachable_dependencies_as_unavailable() -> None:
    client = TestClient(create_app())

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert {check["name"]: check["status"] for check in body["checks"]} == {
        "database": "unhealthy",
        "redis": "unhealthy",
    }
