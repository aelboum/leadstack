"""The audit-trail cursor walks entries that share a timestamp without
repeating or dropping any (the platform orders by created_at only)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from leadstack.domains.workspaces import service


@dataclass
class _Entry:
    id: uuid.UUID
    created_at: datetime
    action: str = "a"
    resource_type: str = "r"
    resource_id: str | None = None
    outcome: str = "success"
    actor_type: str = "user"
    actor_user_id: uuid.UUID | None = None


def _install(monkeypatch: pytest.MonkeyPatch, entries: list[_Entry]) -> None:
    ordered = sorted(entries, key=lambda e: e.created_at, reverse=True)

    def fake_list(tenant_id, *, until=None, limit=100):  # mirrors core.audit_log.list
        rows = [e for e in ordered if until is None or e.created_at <= until]
        return rows[:limit]

    monkeypatch.setattr(service.audit_log, "list", fake_list)
    monkeypatch.setattr(service, "authorize", lambda *a, **k: None)


def _walk(limit: int) -> list[uuid.UUID]:
    seen, cursor = [], None
    for _ in range(1000):
        page = service.list_audit(uuid.uuid4(), uuid.uuid4(), limit=limit, cursor=cursor)
        seen.extend(item.entry_id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return seen
    raise AssertionError("pagination did not terminate")


@pytest.mark.parametrize("limit", [1, 2, 3, 7])
def test_ties_are_neither_repeated_nor_dropped(monkeypatch, limit: int) -> None:
    base = datetime(2026, 10, 9, tzinfo=UTC)
    entries = [_Entry(uuid.uuid4(), base) for _ in range(5)]  # five-way tie
    entries += [_Entry(uuid.uuid4(), base - timedelta(seconds=i)) for i in range(1, 4)]
    entries += [_Entry(uuid.uuid4(), base - timedelta(seconds=9)) for _ in range(3)]
    _install(monkeypatch, entries)

    seen = _walk(limit)

    assert len(seen) == len(set(seen)) == len(entries)
    assert set(seen) == {e.id for e in entries}
