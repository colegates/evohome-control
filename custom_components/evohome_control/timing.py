"""Timezone-aware schedule math shared by DHW and heating zones.

Both the zone schedule (``{"switchpoints": [{"timeOfDay", "heatSetpoint"}, ...]}``)
and the DHW schedule (``[{"timeOfDay", "state"}, ...]``) have the same structure -
a weekly programme keyed by ``dayOfWeek`` - but differ in the per-switchpoint
value field. This module is a single implementation of "what does the schedule
say the current value is, and when does it next change?" shared by both.

Public API:
  * :func:`format_until` - serialise a datetime to the exact format the
    Resideo API expects on ``timeUntil``/``untilTime``.
  * :func:`resolve_tz_sync` / :func:`async_resolve_tz` - turn a Resideo
    ``timeZoneId`` (often a Windows id like ``"GMTStandardTime"``) into a
    ``ZoneInfo``. The async form runs the ZoneInfo lookup off the event
    loop; both are memoised per id so subsequent calls don't touch disk.
  * :func:`schedule_state_now` - the generic calculator that returns
    ``(current_value, next_change_datetime)`` or ``None`` when the schedule
    has nothing usable.
  * :func:`zone_next_switchpoint` and :func:`dhw_schedule_state_now` -
    convenience wrappers for the two concrete value types.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from typing import TYPE_CHECKING, Any, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import DAYS_OF_WEEK

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


# Minimal Windows-id -> IANA mapping covering the ids Resideo actually emits
# for the regions the integration supports. Keys are the space-stripped form
# so both "GMTStandardTime" and "GMT Standard Time" hit the same entry.
# Extend as new ids surface in the wild.
_WINDOWS_TO_IANA: dict[str, str] = {
    # British Isles
    "GMTStandardTime": "Europe/London",
    # Western Europe / Benelux / Germany
    "W.EuropeStandardTime": "Europe/Berlin",
    "WEuropeStandardTime": "Europe/Berlin",
    # France, Spain, Monaco
    "RomanceStandardTime": "Europe/Paris",
    # Austria, Czech Republic, Hungary, Serbia, Slovakia, Slovenia
    "CentralEuropeStandardTime": "Europe/Budapest",
    # Poland, Bosnia, Croatia, North Macedonia, Sarajevo, Warsaw, Zagreb
    "CentralEuropeanStandardTime": "Europe/Warsaw",
    # Baltic + Finland
    "FLEStandardTime": "Europe/Kiev",
    # Scandinavia (Norway, Sweden, Denmark)
    "ScandinavianStandardTime": "Europe/Stockholm",
    # UTC itself (if Resideo ever returns the string)
    "UTC": "UTC",
}


def _normalize_windows_id(tz_id: str) -> str:
    """Compact form used to key _WINDOWS_TO_IANA: strip spaces + trim."""
    return tz_id.replace(" ", "").strip()


# Track which unknown ids we've already warned about so we don't spam.
_WARNED_IDS: set[str] = set()


def _reset_warned_ids_for_tests() -> None:
    """Internal hook: clear the "we've warned about this id" memo."""
    _WARNED_IDS.clear()
    _try_zoneinfo.cache_clear()


@lru_cache(maxsize=128)
def _try_zoneinfo(name: str) -> ZoneInfo | None:
    """ZoneInfo(name) or None. Blocks the first time, then memoised."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        return None


def _candidate_iana(tz_id: str) -> str:
    """Map a Resideo id (possibly a Windows id) to an IANA candidate."""
    normalised = _normalize_windows_id(tz_id)
    return _WINDOWS_TO_IANA.get(normalised, tz_id)


def resolve_tz_sync(
    tz_id: str | None,
    *,
    default: tzinfo | None = None,
    source: str | None = None,
) -> tzinfo:
    """Return ``tzinfo`` for ``tz_id``.

    If the id is a known Windows-style id it is mapped to its IANA name
    first. Unknown or unresolvable ids fall back to ``default`` (UTC if
    that's also ``None``) and log ONE warning per offending id.

    Memoised, so after the first call per id there is no filesystem I/O.
    Safe to call from the event loop once warm; the FIRST call for a given
    id will open a zoneinfo file synchronously and should be driven through
    :func:`async_resolve_tz` instead (or run during async setup when a few
    ms of blocking is acceptable).
    """
    if not tz_id:
        return default or timezone.utc

    tz = _try_zoneinfo(_candidate_iana(tz_id))
    if tz is not None:
        return tz

    if tz_id not in _WARNED_IDS:
        _WARNED_IDS.add(tz_id)
        _LOGGER.warning(
            "Unknown Evohome time zone id %r%s - falling back to %s. "
            "If this is a valid Windows time zone id, please open an "
            "issue so we can add it to the mapping.",
            tz_id,
            f" ({source})" if source else "",
            default if default is not None else "UTC",
        )
    return default or timezone.utc


async def async_resolve_tz(
    hass: HomeAssistant,
    tz_id: str | None,
    *,
    default: tzinfo | None = None,
    source: str | None = None,
) -> tzinfo:
    """Async wrapper around :func:`resolve_tz_sync` that runs off the loop.

    The sync helper is memoised, so only the FIRST call per id does the
    blocking ``ZoneInfo(...)`` lookup in the executor. Subsequent calls
    return immediately from the lru_cache.
    """

    def _resolve() -> tzinfo:
        return resolve_tz_sync(tz_id, default=default, source=source)

    return await hass.async_add_executor_job(_resolve)


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
    tz: tzinfo | None = None,
    tz_id: str | None = None,
    value_key: str,
    now: datetime | None = None,
) -> ScheduledState | None:
    """Compute the current scheduled value + next change datetime.

    The schedule is the API's native shape: ``{"dailySchedules": [{"dayOfWeek":
    ..., "switchpoints": [{"timeOfDay": ..., <value_key>: ...}, ...]}, ...]}``.

    Pass either a pre-resolved ``tz`` (preferred - the coordinator caches
    ``LocationData.tz`` so there is no filesystem I/O on the event loop) or
    a raw ``tz_id`` for tests / back-compat (resolved via the memoised
    :func:`resolve_tz_sync`).

    Returns ``None`` when the schedule is absent or has no usable switchpoints.

    ``now`` is injectable for tests; defaults to ``datetime.now`` in the zone's
    local timezone.
    """
    if not schedule:
        return None

    daily_schedules = schedule.get("dailySchedules") or []
    if not daily_schedules:
        return None

    resolved_tz: tzinfo
    if tz is not None:
        resolved_tz = tz
    else:
        resolved_tz = resolve_tz_sync(tz_id)
    now_local = (now or datetime.now(resolved_tz)).astimezone(resolved_tz)

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
            now_local.year,
            now_local.month,
            now_local.day,
            hh,
            mm,
            ss,
            tzinfo=resolved_tz,
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
                    tzinfo=resolved_tz,
                )
                break

    if next_change is None:
        return None

    return ScheduledState(value=current_value, next_change=next_change)


def dhw_schedule_state_now(
    schedule: dict[str, Any] | None,
    *,
    tz: tzinfo | None = None,
    tz_id: str | None = None,
    now: datetime | None = None,
) -> ScheduledState | None:
    """Return current DHW state and the datetime of its next scheduled change."""
    return schedule_state_now(
        schedule, tz=tz, tz_id=tz_id, value_key="state", now=now
    )


def zone_next_switchpoint(
    schedule: dict[str, Any] | None,
    *,
    tz: tzinfo | None = None,
    tz_id: str | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """Return just the next scheduled switchpoint datetime for a zone.

    Convenience for callers (e.g. ``climate.async_set_temperature``) that only
    care about when the current programme block ends.
    """
    state = schedule_state_now(
        schedule, tz=tz, tz_id=tz_id, value_key="heatSetpoint", now=now
    )
    return state.next_change if state is not None else None
