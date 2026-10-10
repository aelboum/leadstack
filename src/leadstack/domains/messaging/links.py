"""Signed links for consent confirmation and unsubscription.

A link token is `base64url(json payload) . base64url(HMAC-SHA256)`, keyed by
`LEADSTACK_LINK_SIGNING_SECRET` (SaaS-OS secrets provider). The payload binds
purpose, workspace, contact, consent record and a hash of the consented
address, so a token cannot be reused for another purpose, contact,
workspace or a changed address. Confirmation links expire; unsubscribe
links do not (an old email must always allow unsubscribing). Tokens contain
identifiers and a hash only -- never an email address. Links are signed at
delivery time and are never stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from infra.secrets import get_secrets_provider

SECRET_NAME = "LEADSTACK_LINK_SIGNING_SECRET"  # noqa: S105  # pragma: allowlist secret (a name)
CONFIRM = "confirm"
UNSUBSCRIBE = "unsubscribe"
CONFIRM_TTL_SECONDS = 7 * 24 * 3600


class InvalidLinkError(ValueError):
    code = "invalid_link"


class LinkConfigurationError(RuntimeError):
    """Missing signing secret: fail closed (no unsigned links)."""


@dataclass(frozen=True)
class LinkClaims:
    purpose: str
    tenant_id: uuid.UUID
    contact_id: uuid.UUID
    ref_id: uuid.UUID  # the consent record
    address_hash: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _secret() -> bytes:
    value = get_secrets_provider().get(SECRET_NAME)
    if not value or len(value) < 32:
        raise LinkConfigurationError(f"{SECRET_NAME} must be set (>= 32 characters)")
    return value.encode()


def require_secret() -> None:
    _secret()


def sign(claims: LinkClaims, *, now: float | None = None) -> str:
    payload: dict[str, str | int] = {
        "p": claims.purpose,
        "t": str(claims.tenant_id),
        "c": str(claims.contact_id),
        "k": str(claims.ref_id),
        "a": claims.address_hash,
    }
    if claims.purpose == CONFIRM:
        payload["e"] = int((now if now is not None else time.time()) + CONFIRM_TTL_SECONDS)
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    mac = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify(token: str, purpose: str, *, now: float | None = None) -> LinkClaims:
    try:
        body, mac = token.split(".", 1)
        expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected.encode(), mac.encode()):
            raise InvalidLinkError()
        data = json.loads(_unb64(body))
        claims = LinkClaims(
            purpose=data["p"],
            tenant_id=uuid.UUID(data["t"]),
            contact_id=uuid.UUID(data["c"]),
            ref_id=uuid.UUID(data["k"]),
            address_hash=str(data["a"]),
        )
        expires = int(data["e"]) if claims.purpose == CONFIRM else None
    except (ValueError, KeyError, TypeError):
        raise InvalidLinkError() from None
    if claims.purpose != purpose:
        raise InvalidLinkError()
    if expires is not None and expires < (now if now is not None else time.time()):
        raise InvalidLinkError()
    return claims
