"""List pagination convention: `?limit=` (1..100, default 50) and an opaque
`?cursor=`; responses are `{"items": [...], "next_cursor": str | null}`.

`paginate()` serves lists already bounded in memory (a workspace's members,
invitations or clients). Database-backed lists use keyset cursors in their
own services."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence

from fastapi import Query

from leadstack.domains.workspaces.service import InvalidCursorError

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

LimitQuery = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
CursorQuery = Query(None, max_length=512)


def _encode(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def _decode(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()).decode()
        prefix, value = raw.split(":", 1)
        offset = int(value)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise InvalidCursorError() from None
    if prefix != "o" or offset < 0:
        raise InvalidCursorError()
    return offset


def paginate[T](items: Sequence[T], limit: int, cursor: str | None) -> tuple[list[T], str | None]:
    offset = _decode(cursor) if cursor else 0
    page = list(items[offset : offset + limit])
    more = offset + limit < len(items)
    return page, (_encode(offset + limit) if more else None)
