"""Bookable slot computation (pure, no I/O).

Availability is expressed in *local wall-clock time* of the booking type's
IANA time zone (e.g. Monday 09:00-12:00 in `Africa/Casablanca`). Slots are
returned as UTC instants. Daylight-saving transitions -- including
Morocco's Ramadan suspension of DST -- are handled by `zoneinfo`:

- a local time that does not exist (clocks jump forward) yields no slot;
- a local time that occurs twice (clocks fall back) yields one slot, at the
  first occurrence (`fold=0`), so a slot is never offered twice.

Busy intervals (existing appointments, already in UTC) remove overlapping
slots; `buffer` minutes are kept free after each appointment and slot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MAX_RANGE_DAYS = 31


@dataclass(frozen=True)
class WeeklyRule:
    weekday: int  # Monday = 0 ... Sunday = 6
    start: time
    end: time


@dataclass(frozen=True)
class Interval:
    start: datetime  # aware, UTC
    end: datetime

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end


def _exists(local: datetime, zone: ZoneInfo) -> bool:
    """False for wall-clock times skipped by a forward DST jump."""
    round_trip = local.replace(tzinfo=zone).astimezone(UTC).astimezone(zone)
    return round_trip.replace(tzinfo=None) == local


def slots(
    *,
    rules: list[WeeklyRule],
    zone_name: str,
    duration_minutes: int,
    buffer_minutes: int,
    first_day: date,
    last_day: date,
    busy: list[Interval],
    not_before: datetime,
) -> list[datetime]:
    """Start instants (UTC) of free slots between `first_day` and
    `last_day` (inclusive, local dates), none earlier than `not_before`."""
    if (last_day - first_day).days > MAX_RANGE_DAYS or last_day < first_day:
        raise ValueError("date range")
    zone = ZoneInfo(zone_name)
    step = timedelta(minutes=duration_minutes)
    buffer = timedelta(minutes=buffer_minutes)
    blocked = [Interval(b.start, b.end + buffer) for b in busy]
    found: set[datetime] = set()
    day = first_day
    while day <= last_day:
        for rule in rules:
            if rule.weekday != day.weekday():
                continue
            cursor = datetime.combine(day, rule.start)
            window_end = (
                datetime.combine(day + timedelta(days=1), time(0))
                if rule.end == time.max  # availability until midnight
                else datetime.combine(day, rule.end)
            )
            while cursor + step <= window_end:
                if _exists(cursor, zone):
                    start = cursor.replace(tzinfo=zone, fold=0).astimezone(UTC)
                    end = start + step
                    # Elapsed time crosses DST jumps: the real end, in local
                    # wall-clock time, must still be inside the window.
                    if end.astimezone(zone).replace(tzinfo=None) > window_end:
                        cursor += step
                        continue
                    candidate = Interval(start, end + buffer)
                    if start >= not_before and not any(candidate.overlaps(b) for b in blocked):
                        found.add(start)
                cursor += step
        day += timedelta(days=1)
    return sorted(found)
