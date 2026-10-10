"""Slot computation across time zones and daylight-saving transitions."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from leadstack.domains.scheduling.slots import Interval, WeeklyRule, slots

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def _local(instants: list[datetime], zone: str) -> list[str]:
    return [i.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M") for i in instants]


def test_basic_weekly_window_in_local_time() -> None:
    monday = date(2026, 10, 12)
    result = slots(
        rules=[WeeklyRule(0, time(9), time(11))],
        zone_name="Africa/Casablanca",
        duration_minutes=30,
        buffer_minutes=0,
        first_day=monday,
        last_day=monday,
        busy=[],
        not_before=EPOCH,
    )
    assert _local(result, "Africa/Casablanca") == [
        "2026-10-12 09:00", "2026-10-12 09:30", "2026-10-12 10:00", "2026-10-12 10:30",
    ]  # fmt: skip
    assert all(s.tzinfo == UTC for s in result)


def test_busy_intervals_and_buffer_remove_slots() -> None:
    monday = date(2026, 10, 12)
    zone = ZoneInfo("Africa/Casablanca")
    busy_start = datetime(2026, 10, 12, 9, 30, tzinfo=zone).astimezone(UTC)
    result = slots(
        rules=[WeeklyRule(0, time(9), time(11))],
        zone_name="Africa/Casablanca",
        duration_minutes=30,
        buffer_minutes=15,
        first_day=monday,
        last_day=monday,
        busy=[Interval(busy_start, busy_start + timedelta(minutes=30))],
        not_before=EPOCH,
    )
    # 09:00 would end 09:30 + 15 min buffer, overlapping; 10:00 starts inside
    # the busy appointment's buffer (until 10:15).
    assert _local(result, "Africa/Casablanca") == ["2026-10-12 10:30"]


def test_not_before_excludes_past_slots() -> None:
    monday = date(2026, 10, 12)
    cutoff = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("Africa/Casablanca")).astimezone(UTC)
    result = slots(
        rules=[WeeklyRule(0, time(9), time(11))],
        zone_name="Africa/Casablanca",
        duration_minutes=30,
        buffer_minutes=0,
        first_day=monday,
        last_day=monday,
        busy=[],
        not_before=cutoff,
    )
    assert _local(result, "Africa/Casablanca") == ["2026-10-12 10:00", "2026-10-12 10:30"]


@pytest.mark.parametrize(
    ("zone", "day", "window", "expected"),
    [
        # Paris spring-forward 2026-03-29: 02:00 -> 03:00 does not exist.
        ("Europe/Paris", date(2026, 3, 29), (time(1), time(4)), ["01:00", "03:00"]),
        # Paris fall-back 2026-10-25: 02:00-03:00 happens twice; offered once.
        ("Europe/Paris", date(2026, 10, 25), (time(1), time(4)), ["01:00", "02:00", "03:00"]),
    ],
)
def test_dst_gaps_and_overlaps(zone, day, window, expected) -> None:
    rule = WeeklyRule(day.weekday(), *window)
    result = slots(
        rules=[rule],
        zone_name=zone,
        duration_minutes=60,
        buffer_minutes=0,
        first_day=day,
        last_day=day,
        busy=[],
        not_before=EPOCH,
    )
    assert [s.astimezone(ZoneInfo(zone)).strftime("%H:%M") for s in result] == expected
    assert len(result) == len(set(result))


@pytest.mark.parametrize(
    ("day", "utc_hour"),
    [
        (date(2026, 1, 12), 9),  # UTC+1 (standard time in Morocco since 2018)
        (date(2026, 2, 23), 10),  # UTC+0 during Ramadan 2026 (tz database 2026e)
        (date(2026, 6, 15), 9),  # UTC+1 again after Ramadan
    ],
)
def test_morocco_ramadan_offset_change(day, utc_hour) -> None:
    """The same 10:00 local slot maps to different UTC instants around
    Ramadan, exactly as the IANA tz database defines -- never a hardcoded
    offset. (If a future tzdata release revises Morocco's Ramadan dates,
    this test must be updated together with the dependency.)"""
    (slot,) = slots(
        rules=[WeeklyRule(day.weekday(), time(10), time(11))],
        zone_name="Africa/Casablanca",
        duration_minutes=60,
        buffer_minutes=0,
        first_day=day,
        last_day=day,
        busy=[],
        not_before=EPOCH,
    )
    assert slot.astimezone(ZoneInfo("Africa/Casablanca")).strftime("%H:%M") == "10:00"
    assert slot.hour == utc_hour


def test_range_is_bounded() -> None:
    with pytest.raises(ValueError):
        slots(
            rules=[], zone_name="UTC", duration_minutes=30, buffer_minutes=0,
            first_day=date(2026, 1, 1), last_day=date(2026, 3, 1), busy=[], not_before=EPOCH,
        )  # fmt: skip


def test_dst_forward_slot_cannot_overrun_the_window() -> None:
    """Paris 2026-03-29: a 60-minute slot at 01:30 really ends at 03:30
    local (the 02:00 hour is skipped), past a window ending at 03:00."""
    day = date(2026, 3, 29)
    result = slots(
        rules=[WeeklyRule(day.weekday(), time(1, 30), time(3, 0))],
        zone_name="Europe/Paris",
        duration_minutes=60,
        buffer_minutes=0,
        first_day=day,
        last_day=day,
        busy=[],
        not_before=EPOCH,
    )
    assert result == []


def test_availability_until_midnight_offers_the_last_slot() -> None:
    day = date(2026, 10, 12)
    result = slots(
        rules=[WeeklyRule(0, time(23, 0), time.max)],
        zone_name="Africa/Casablanca",
        duration_minutes=30,
        buffer_minutes=0,
        first_day=day,
        last_day=day,
        busy=[],
        not_before=EPOCH,
    )
    assert _local(result, "Africa/Casablanca") == ["2026-10-12 23:00", "2026-10-12 23:30"]


def test_time_zone_rules_come_from_the_pinned_tzdata_package() -> None:
    import zoneinfo

    import leadstack  # noqa: F401 -- resets the time zone search path

    assert zoneinfo.TZPATH == ()
