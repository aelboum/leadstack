"""Phase 9: booking types, public booking and appointments (ADR-0008)."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from infra.db import select, tenant_session_scope

from leadstack.domains.crm.models import Contact
from leadstack.domains.forms.service import issue_token
from leadstack.domains.scheduling import service as scheduling
from leadstack.routes.forms import client_bucket
from tests.integration.test_crm import _contacts
from tests.integration.test_workspaces import _code, _create, _invite_and_accept, _member_id

pytestmark = pytest.mark.integration

CASABLANCA = ZoneInfo("Africa/Casablanca")
CLIENT = client_bucket("testclient")
WEEKDAYS_9_TO_12 = [{"weekday": d, "start": 540, "end": 720} for d in range(5)]


def _next_monday(min_days: int = 3) -> date:
    day = datetime.now(UTC).date() + timedelta(days=min_days)
    return day + timedelta(days=(7 - day.weekday()) % 7)


def _type(api, owner, tenant, **overrides) -> dict:
    body = {
        "name": "Consultation",
        "host_user_id": str(owner.user_id),
        "duration_minutes": 30,
        "time_zone": "Africa/Casablanca",
        "rules": WEEKDAYS_9_TO_12,
        **overrides,
    }
    response = api.post(f"/v1/workspaces/{tenant}/booking-types", json=body, headers=owner.headers)
    assert response.status_code == 201, response.text
    return response.json()


def _page(api, key: str, day: date, last: date | None = None):
    return api.get(f"/v1/public/booking/{key}", params={"from": str(day), "to": str(last or day)})


def _token(key: str, client: str = CLIENT) -> str:
    return issue_token("booking:" + key, client, now=time.time() - 10)


def _book(api, key: str, start: str, **overrides):
    body = {
        "starts_at": start,
        "name": "Salma Bennani",
        "email": "salma@example.ma",
        "consent": True,
        "booking_token": _token(key),
        **overrides,
    }
    return api.post(f"/v1/public/booking/{key}", json=body)


def _local(instant: str) -> str:
    return datetime.fromisoformat(instant).astimezone(CASABLANCA).strftime("%H:%M")


@pytest.fixture
def ws(api, make_user):
    owner = make_user()
    tenant = _create(api, owner)["tenant_id"]
    return owner, tenant


def test_public_page_lists_local_slots(api, ws) -> None:
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    monday = _next_monday()

    page = _page(api, key, monday)

    assert page.status_code == 200, page.text
    body = page.json()
    assert body["time_zone"] == "Africa/Casablanca" and body["booking_token"]
    assert [_local(s) for s in body["slots"]] == [
        "09:00",
        "09:30",
        "10:00",
        "10:30",
        "11:00",
        "11:30",
    ]
    assert page.headers["cache-control"] == "no-store"


def test_booking_creates_contact_and_removes_the_slot(api, ws) -> None:
    owner, tenant = ws
    key = _type(api, owner, tenant, buffer_minutes=15)["public_key"]
    monday = _next_monday()
    slots = _page(api, key, monday).json()["slots"]

    booked = _book(api, key, slots[1])  # 09:30
    again = _book(api, key, slots[1], email="other@example.ma")
    remaining = [_local(s) for s in _page(api, key, monday).json()["slots"]]

    assert booked.status_code == 200, booked.text
    assert (again.status_code, _code(again)) == (409, "slot_unavailable")
    # 09:00 ends 09:30 (+15 min buffer overlaps); 10:00 starts within 09:30-10:00+15.
    assert remaining == ["10:30", "11:00", "11:30"]
    contact = api.get(_contacts(tenant), headers=owner.headers).json()["items"][0]
    assert (contact["email"], contact["first_name"], contact["last_name"]) == (
        "salma@example.ma", "Salma", "Bennani",
    )  # fmt: skip
    listed = api.get(
        f"/v1/workspaces/{tenant}/appointments",
        params={"from": f"{monday}T00:00:00Z", "to": f"{monday + timedelta(days=1)}T00:00:00Z"},
        headers=owner.headers,
    ).json()
    assert [(a["contact_id"], a["source"], a["status"]) for a in listed] == [
        (contact["contact_id"], "public", "booked")
    ]


def test_concurrent_bookings_of_one_slot_succeed_once(api, ws) -> None:
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    start = _page(api, key, _next_monday()).json()["slots"][0]
    request = scheduling.BookingRequest(
        starts_at=datetime.fromisoformat(start), name="A B", email="", phone=None, consent=True,
        token=_token(key),
    )  # fmt: skip

    def attempt(i: int) -> str:
        try:
            scheduling.book(key, CLIENT, scheduling.BookingRequest(
                **{**request.__dict__, "email": f"c{i}@example.ma"}))  # fmt: skip
            return "ok"
        except scheduling.SlotUnavailableError:
            return "taken"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))

    assert sorted(results) == ["ok"] + ["taken"] * 5


def test_existing_contact_is_linked_not_modified(api, ws) -> None:
    owner, tenant = ws
    existing = api.post(
        _contacts(tenant),
        json={"first_name": "Karim", "email": "salma@example.ma"},
        headers=owner.headers,
    ).json()
    key = _type(api, owner, tenant)["public_key"]
    start = _page(api, key, _next_monday()).json()["slots"][0]

    response = _book(api, key, start, name="Someone Else")

    assert response.status_code == 200
    contact = api.get(f"{_contacts(tenant)}/{existing['contact_id']}", headers=owner.headers).json()
    assert (contact["first_name"], contact["last_name"]) == ("Karim", None)
    assert len(api.get(_contacts(tenant), headers=owner.headers).json()["items"]) == 1


@pytest.mark.parametrize(
    "mutation",
    ["no_consent", "form_token", "other_client", "off_grid", "outside_hours", "bad_email"],
)
def test_invalid_bookings_are_rejected(api, ws, mutation) -> None:
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    monday = _next_monday()
    first = datetime.fromisoformat(_page(api, key, monday).json()["slots"][0])
    overrides = {
        "no_consent": {"consent": False},
        "form_token": {"booking_token": issue_token(key, CLIENT, now=time.time() - 10)},
        "other_client": {"booking_token": _token(key, client_bucket("203.0.113.9"))},
        "off_grid": {"starts_at": (first + timedelta(minutes=10)).isoformat()},
        "outside_hours": {"starts_at": (first - timedelta(hours=1)).isoformat()},
        "bad_email": {"email": "not-an-email"},
    }[mutation]

    response = _book(api, key, first.isoformat(), **overrides)

    expected = 409 if mutation in ("off_grid", "outside_hours") else 422
    assert response.status_code == expected, response.text
    assert api.get(_contacts(tenant), headers=owner.headers).json()["items"] == []


def test_disabled_type_and_removed_host_hide_the_page(api, ws, make_user) -> None:
    owner, tenant = ws
    host = make_user()
    _invite_and_accept(api, owner, tenant, host, "member")
    created = _type(api, owner, tenant, host_user_id=str(host.user_id))
    key = created["public_key"]
    start = _page(api, key, _next_monday()).json()["slots"][0]

    api.post(
        f"/v1/workspaces/{tenant}/members/{_member_id(api, owner, tenant, host)}/status",
        json={"action": "suspend"},
        headers=owner.headers,
    )
    host_gone = _book(api, key, start)
    api.post(
        f"/v1/workspaces/{tenant}/booking-types/{created['booking_type_id']}/status",
        json={"status": "disabled"},
        headers=owner.headers,
    )
    disabled = _page(api, key, _next_monday())

    assert (host_gone.status_code, _code(host_gone)) == (404, "booking_type_not_found")
    assert disabled.status_code == 404


def test_permissions_and_isolation(api, ws, make_user) -> None:
    owner, tenant = ws
    member = make_user()
    _invite_and_accept(api, owner, tenant, member, "member")
    contact = api.post(
        _contacts(tenant), json={"email": "m@example.ma"}, headers=owner.headers
    ).json()
    start = datetime.combine(_next_monday(), datetime.min.time(), tzinfo=CASABLANCA) + timedelta(
        hours=15
    )
    manual = {
        "host_user_id": str(owner.user_id),
        "contact_id": contact["contact_id"],
        "starts_at": start.isoformat(),
        "duration_minutes": 45,
        "time_zone": "Africa/Casablanca",
    }

    member_type = api.post(
        f"/v1/workspaces/{tenant}/booking-types",
        json={"name": "x", "host_user_id": str(member.user_id), "duration_minutes": 30,
              "time_zone": "UTC", "rules": WEEKDAYS_9_TO_12},
        headers=member.headers,
    )  # fmt: skip
    created = api.post(f"/v1/workspaces/{tenant}/appointments", json=manual, headers=member.headers)
    overlap = api.post(
        f"/v1/workspaces/{tenant}/appointments",
        json={**manual, "starts_at": (start + timedelta(minutes=30)).isoformat()},
        headers=member.headers,
    )
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]
    appointment_id = created.json()["appointment_id"]
    cross = api.post(
        f"/v1/workspaces/{other}/appointments/{appointment_id}/cancel", headers=other_owner.headers
    )
    cancelled = api.post(
        f"/v1/workspaces/{tenant}/appointments/{appointment_id}/cancel", headers=member.headers
    )
    rebooked = api.post(
        f"/v1/workspaces/{tenant}/appointments", json=manual, headers=member.headers
    )

    assert member_type.status_code == 403
    assert created.status_code == 201, created.text
    assert (overlap.status_code, _code(overlap)) == (409, "slot_unavailable")
    assert cross.status_code == 404
    assert cancelled.json()["status"] == "cancelled"
    assert rebooked.status_code == 201  # a cancelled appointment frees the time


def test_host_must_be_a_member_and_inputs_are_validated(api, ws, make_user) -> None:
    owner, tenant = ws
    outsider = make_user()
    base = {
        "name": "x",
        "duration_minutes": 30,
        "time_zone": "Africa/Casablanca",
        "rules": WEEKDAYS_9_TO_12,
    }

    def create(**body):
        return api.post(
            f"/v1/workspaces/{tenant}/booking-types", json={**base, **body}, headers=owner.headers
        )

    assert _code(create(host_user_id=str(outsider.user_id))) == "invalid_host"
    assert (
        _code(create(host_user_id=str(owner.user_id), time_zone="Mars/Olympus"))
        == "invalid_booking_type"
    )
    bad_rule = [{"weekday": 0, "start": 600, "end": 540}]
    assert create(host_user_id=str(owner.user_id), rules=bad_rule).status_code == 422
    off_grid = [{"weekday": 0, "start": 541, "end": 600}]
    assert create(host_user_id=str(owner.user_id), rules=off_grid).status_code == 422


def test_public_range_is_bounded(api, ws) -> None:
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    monday = _next_monday()

    response = _page(api, key, monday, monday + timedelta(days=40))

    assert (response.status_code, _code(response)) == (422, "invalid_range")


def test_horizon_and_notice_limit_offered_slots(api, ws) -> None:
    owner, tenant = ws
    key = _type(api, owner, tenant, horizon_days=1)["public_key"]
    far = _next_monday(min_days=10)

    assert _page(api, key, far).json()["slots"] == []


def test_dst_fall_back_offers_each_instant_once(api, ws) -> None:
    """Europe/Paris 2026-10-25 02:00-03:00 occurs twice; computed with a
    fixed `now` so the test does not depend on the run date."""
    owner, tenant = ws
    created = _type(
        api, owner, tenant, time_zone="Europe/Paris", duration_minutes=60,
        rules=[{"weekday": 6, "start": 60, "end": 240}],
    )  # fmt: skip
    now = datetime(2026, 10, 1, tzinfo=UTC)

    page = scheduling.public_page(
        created["public_key"], CLIENT, date(2026, 10, 25), date(2026, 10, 25), now=now
    )

    paris = [s.astimezone(ZoneInfo("Europe/Paris")).strftime("%H:%M %Z") for s in page.slots]
    assert paris == ["01:00 CEST", "02:00 CEST", "03:00 CET"]


def test_contact_erasure_removes_appointments(api, ws) -> None:
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    start = _page(api, key, _next_monday()).json()["slots"][0]
    _book(api, key, start)
    contact_id = api.get(_contacts(tenant), headers=owner.headers).json()["items"][0]["contact_id"]

    api.delete(f"{_contacts(tenant)}/{contact_id}", headers=owner.headers)

    from leadstack.domains.scheduling.models import Appointment

    with tenant_session_scope(uuid.UUID(tenant)) as session:
        assert session.execute(select(Appointment)).scalars().all() == []
        assert session.execute(select(Contact)).scalars().all() == []


def test_concurrent_overlapping_slots_never_both_book(api, make_user) -> None:
    """Different start times that overlap through the buffer are not caught
    by the unique (host, start) backstop: the per-host lock must serialize
    them. Repeated to give the race a real chance."""
    for _ in range(4):
        owner = make_user()
        tenant = _create(api, owner)["tenant_id"]
        key = _type(api, owner, tenant, buffer_minutes=15)["public_key"]
        first, second = _page(api, key, _next_monday()).json()["slots"][:2]  # 09:00, 09:30

        def attempt(start: str, i: int, key: str = key) -> str:
            request = scheduling.BookingRequest(
                starts_at=datetime.fromisoformat(start), name="A B",
                email=f"o{i}@example.ma", phone=None, consent=True, token=_token(key),
            )  # fmt: skip
            try:
                scheduling.book(key, CLIENT, request)
                return "ok"
            except scheduling.SlotUnavailableError:
                return "taken"

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, [first, second, first, second], range(4)))

        assert results.count("ok") == 1, results


def test_junk_requests_do_not_consume_the_page_quota(api, ws, monkeypatch) -> None:
    import leadstack.routes.scheduling as routes

    monkeypatch.setattr(routes, "BOOKINGS_PER_TYPE_PER_HOUR", 1)
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    start = _page(api, key, _next_monday()).json()["slots"][0]

    junk = [_book(api, key, start, booking_token="1." + "x" * 20) for _ in range(3)]
    real = _book(api, key, start)

    assert {r.status_code for r in junk} == {422}
    assert real.status_code == 200, real.text


def test_honeypot_rejects_without_booking(api, ws) -> None:
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    start = _page(api, key, _next_monday()).json()["slots"][0]

    response = _book(api, key, start, website="http://spam.example")

    assert response.status_code == 422
    assert api.get(_contacts(tenant), headers=owner.headers).json()["items"] == []


def test_per_type_daily_cap(api, ws, monkeypatch) -> None:
    monkeypatch.setattr(scheduling, "DEFAULT_DAILY_BOOKINGS_PER_TYPE", 1)
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    other = _type(api, owner, tenant, name="Second", host_user_id=str(owner.user_id))["public_key"]
    slots = _page(api, key, _next_monday()).json()["slots"]

    first = _book(api, key, slots[0])
    second = _book(api, key, slots[2], email="b@example.ma")
    other_page = _book(
        api, other, _page(api, other, _next_monday()).json()["slots"][-1], email="c@example.ma"
    )

    assert first.status_code == 200
    assert (second.status_code, _code(second)) == (429, "booking_quota_exceeded")
    assert other_page.status_code == 200  # another page keeps its own allowance


def test_public_page_hides_slots_when_host_removed(api, ws, make_user) -> None:
    owner, tenant = ws
    host = make_user()
    _invite_and_accept(api, owner, tenant, host, "member")
    key = _type(api, owner, tenant, host_user_id=str(host.user_id))["public_key"]
    api.post(
        f"/v1/workspaces/{tenant}/members/{_member_id(api, owner, tenant, host)}/status",
        json={"action": "suspend"},
        headers=owner.headers,
    )

    assert _page(api, key, _next_monday()).status_code == 404


def test_booking_dispatches_contact_events(api, ws, monkeypatch) -> None:
    import leadstack.routes.scheduling as routes

    seen: list[object] = []

    async def fake_dispatch(tenant_id) -> None:
        seen.append(tenant_id)

    monkeypatch.setattr(routes, "dispatch_events", fake_dispatch)
    owner, tenant = ws
    key = _type(api, owner, tenant)["public_key"]
    start = _page(api, key, _next_monday()).json()["slots"][0]

    assert _book(api, key, start).status_code == 200
    assert [str(t) for t in seen] == [tenant]


def test_booking_type_status_and_deletion_are_audited(api, ws) -> None:
    from core import audit_log

    owner, tenant = ws
    created = _type(api, owner, tenant)
    url = f"/v1/workspaces/{tenant}/booking-types/{created['booking_type_id']}"
    api.post(f"{url}/status", json={"status": "disabled"}, headers=owner.headers)
    api.delete(url, headers=owner.headers)

    entries = audit_log.list(uuid.UUID(tenant), limit=100)
    actions = {e.action for e in entries if e.resource_id == created["booking_type_id"]}
    assert {"leadstack.booking_type.created", "leadstack.booking_type.disabled",
            "leadstack.booking_type.deleted"} <= actions  # fmt: skip
