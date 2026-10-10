"""`GET /v1/meta` -- the minimal Phase 1 product route.

Proves the product's own router is mounted under the agreed API namespace
on an app built by SaaS-OS's platform builder. It is deliberately public and
static: no tenant data, no build or dependency versions, no configuration.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["meta"])


class MetaResponse(BaseModel):
    product: str
    api_version: str


@router.get("/meta", response_model=MetaResponse)
def get_meta() -> MetaResponse:
    return MetaResponse(product="leadstack", api_version="v1")
