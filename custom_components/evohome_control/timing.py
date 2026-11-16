"""Timezone-aware schedule math shared by DHW and heating zones.

Both the zone schedule (``{"switchpoints": [{"timeOfDay", "heatSetpoint"}, ...]}``)
and the DHW schedule (``[{"timeOfDay", "state"}, ...]``) have the same structure -
a weekly programme keyed by ``dayOfWeek`` - but differ in the per-switchpoint
value field. This module is a single implementation of "what does the schedule
say the current value is, and when does it next change?" shared by both.

Public API:
  * :func:`format_until` - serialise a datetime to the exact format the
    Resideo API expects on ``timeUntil``/``untilTime``.
  * :func:`schedule_state_now` - the generic calculator that returns
    ``(current_value, next_change_datetime)`` or ``None`` when the schedule
    has nothing usable.
  * :func:`zone_next_switchpoint` and :func:`dhw_schedule_state_now` -
    convenience wrappers for the two concrete value types.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import DAYS_OF_WEEK


class ScheduledState(NamedTuple):
    """The current schedule state and the datetime at which it next changes.

    ``value`` is a ``str`` (``"On"``/``"Off"``) for DHW and a ``float`` (heat
    setpoint) for a zone.
    """

    value: Any
    next_change: datetime


def format_until(value: datetime) -> str:
    """Serialise a datetime to the ISO-8601-Z form the Resideo API expects."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_tz(tz_id: str | None) -> Any:
    if not tz_id:
        return timezone.utc
    try:
        return ZoneInfo(tz_id)
    except (ZoneInfoNotFoundError, KeyError):
        return timezone.utc


def _pad_time_of_day(tod: str) -> str | None:
    """Return a HH:MM:SS string, or None if the input isn't well-formed."""
    parts = tod.split(":")
    if len(parts) == 2:
        return f"{tod}:00"
    if len(parts) == 3:
        return tod
    return None


def schedule_state_now(
    schedule: dict[str, Any] | None,
    *,
    tz_id: str | None,
    value_key: str,
    now: datetime | None = None,
) -> ScheduledState | None:
    """Compute the current scheduled value + next change datetime.

    The schedule is the API's native shape: ``{"dailySchedules": [{"dayOfWeek":
    ..., "switchpoints": [{"timeOfDay": ..., <value_key>: ...}, ...]}, ...]}``.

    Returns ``None`` when the schedule is absent or has no usable switchpoints.

    ``now`` is injectable for tests; defaults to ``datetime.now`` in the zone's
    local timezone.
    """
    if not schedule:
        return None

    daily_schedules = schedule.get("dailySchedules") or []
    if not daily_schedules:
        return None

    tz = _resolve_tz(tz_id)
    now_local = (now or datetime.now(tz)).astimezone(tz)

    # day -> sorted [(HH:MM:SS, value), ...], skipping entries missing either
    # timeOfDay or the value field so sparse schedules fall through cleanly.
    day_sps: dict[str, list[tuple[str, Any]]] = {}
    for day_sched in daily_schedules:
        dow = day_sched.get("dayOfWeek")
        if not dow:
            continue
        sps: list[tuple[str, Any]] = []
        for sp in day_sched.get("switchpoints", []):
            tod = _pad_time_of_day(sp.get("timeOfDay", ""))
            if tod is None or value_key not in sp:
                continue
            sps.append((tod, sp[value_key]))
        if sps:
            day_sps[dow] = sorted(sps)

    if not day_sps:
        return None

    today_idx = now_local.weekday()  # 0 = Monday, matches DAYS_OF_WEEK index
    today_name = DAYS_OF_WEEK[today_idx]
    now_str = now_local.strftime("%H:%M:%S")

    # Current value: last switchpoint at-or-before now today, else the last
    # switchpoint of the most recent prior day that has any entries.
    today_sps = day_sps.get(today_name, [])
    before_now = [(t, v) for t, v in today_sps if t <= now_str]

    current_value: Any = None
    if before_now:
        _, current_value = before_now[-1]
    else:
        for i in range(1, 8):
            prev_name = DAYS_OF_WEEK[(today_idx - i) % 7]
            if day_sps.get(prev_name):
                _, current_value = day_sps[prev_name][-1]
                break

    if current_value is None:
        return None

    # Next change: first switchpoint strictly after now today, or the first
    # one of the next populated day (wrapping up to 7 days forward).
    after_now = [(t, v) for t, v in today_sps if t > now_str]

    next_change: datetime | None = None
    if after_now:
        hh, mm, ss = (int(p) for p in after_now[0][0].split(":"))
        next_change = datetime(
            now_local.year, now_local.month, now_local.day, hh, mm, ss, tzinfo=tz
        )
    else:
        for i in range(1, 8):
            next_name = DAYS_OF_WEEK[(today_idx + i) % 7]
            next_sps = day_sps.get(next_name, [])
            if next_sps:
                hh, mm, ss = (int(p) for p in next_sps[0][0].split(":"))
                next_date = now_local.date() + timedelta(days=i)
                next_change = datetime(
                    next_date.year,
                    next_date.month,
                    next_date.day,
                    hh,
                    mm,
                    ss,
                    tzinfo=tz,
                )
                break

    if next_change is None:
        return None

    return ScheduledState(value=current_value, next_change=next_change)


def dhw_schedule_state_now(
    schedule: dict[str, Any] | None,
    *,
    tz_id: str | None,
    now: datetime | None = None,
) -> ScheduledState | None:
    """Return current DHW state and the datetime of its next scheduled change."""
    return schedule_state_now(schedule, tz_id=tz_id, value_key="state", now=now)


def zone_next_switchpoint(
    schedule: dict[str, Any] | None,
    *,
    tz_id: str | None,
    now: datetime | None = None,
) -> datetime | None:
    """Return just the next scheduled switchpoint datetime for a zone.

    Convenience for callers (e.g. ``climate.async_set_temperature``) that only
    care about when the current programme block ends.
    """
    state = schedule_state_now(
        schedule, tz_id=tz_id, value_key="heatSetpoint", now=now
    )
    return state.next_change if state is not None else None
