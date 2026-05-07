"""Tests for schedule-aware dhw_clear_override and _dhw_schedule_state_now.

Covered scenarios for _dhw_schedule_state_now:
  1. Mid-day mid-Off period
  2. Before today's first switchpoint  (falls back to previous day)
  3. After today's last switchpoint    (wraps to next day)
  4. Empty schedule                    (returns None)
  5. Single day, single switchpoint    (next-change wraps to next week)
  6. Timezone-aware (DST, Europe/London BST)

Handler integration:
  7. Scheduled Off  -> TemporaryOverride state="Off" until next_change
  8. Scheduled On   -> FollowSchedule
  9. No schedule    -> FollowSchedule
"""
from __future__ import annotations

import sys
import os

import tests.conftest  # noqa: F401 — load HA stubs before any integration import

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import datetime as dt_module
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.evohome_control.const import (
    ATTR_DHW_ID,
    ATTR_LOCATION_ID,
    DOMAIN,
)
from custom_components.evohome_control.coordinator import (
    DhwData,
    EvohomeData,
    LocationData,
)
from custom_components.evohome_control.services import (
    _DhwScheduledState,
    _dhw_schedule_state_now,
    _format_until,
    _make_dhw_clear_override,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# 2026-05-07 is a Thursday (weekday=3) in UTC.
FIXED_NOW = datetime(2026, 5, 7, 14, 30, 0, tzinfo=timezone.utc)
DHW_ID = "3932871"
LOCATION_ID = "1706229"

# A typical 7-day schedule.  Thursday has a midday Off gap so we can exercise
# the "mid-period" test.  All other days are simple On/Off pairs.
_WEEKDAY_SPS = [
    {"timeOfDay": "07:00:00", "state": "On"},
    {"timeOfDay": "22:00:00", "state": "Off"},
]
FULL_SCHEDULE = {
    "dailySchedules": [
        {"dayOfWeek": "Monday",    "switchpoints": _WEEKDAY_SPS},
        {"dayOfWeek": "Tuesday",   "switchpoints": _WEEKDAY_SPS},
        {"dayOfWeek": "Wednesday", "switchpoints": _WEEKDAY_SPS},
        {"dayOfWeek": "Thursday",  "switchpoints": [
            {"timeOfDay": "07:00:00", "state": "On"},
            {"timeOfDay": "12:00:00", "state": "Off"},
            {"timeOfDay": "18:00:00", "state": "On"},
            {"timeOfDay": "22:00:00", "state": "Off"},
        ]},
        {"dayOfWeek": "Friday",    "switchpoints": _WEEKDAY_SPS},
        {"dayOfWeek": "Saturday",  "switchpoints": _WEEKDAY_SPS},
        {"dayOfWeek": "Sunday",    "switchpoints": _WEEKDAY_SPS},
    ]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coord(schedule=None, time_zone: str | None = "UTC"):
    dhw = DhwData(
        location_id=LOCATION_ID,
        tcs_id="tcs1",
        dhw_id=DHW_ID,
        temperature=55.0,
        is_available=True,
        state="On",
        mode="PermanentOverride",
        schedule=schedule,
    )
    loc = LocationData(
        location_id=LOCATION_ID,
        name="My Home",
        city=None,
        country=None,
        time_zone=time_zone,
        dhw=dhw,
    )
    data = EvohomeData(locations={LOCATION_ID: loc})
    coord = MagicMock()
    coord.data = data
    coord.client.async_set_dhw_state = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord, dhw


def _call(data: dict):
    c = MagicMock()
    c.data = data
    return c


def _patch_now(fixed: datetime):
    """Context manager that patches datetime.now in services and forwards datetime()."""
    return patch(
        "custom_components.evohome_control.services.datetime",
        **{
            "now.return_value": fixed,
            "side_effect": lambda *a, **kw: dt_module.datetime(*a, **kw),
        },
    )


# ---------------------------------------------------------------------------
# 1. Mid-day mid-Off period
# ---------------------------------------------------------------------------


def test_mid_day_off_period():
    """14:30 Thursday falls between the 12:00 Off and 18:00 On switchpoints."""
    coord, dhw = _make_coord(FULL_SCHEDULE)
    with _patch_now(FIXED_NOW):
        result = _dhw_schedule_state_now(coord, dhw)

    assert result is not None
    assert result.state == "Off"
    # Next change is 18:00 today (UTC)
    assert result.next_change == datetime(2026, 5, 7, 18, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 2. Before today's first switchpoint – fall back to previous day
# ---------------------------------------------------------------------------


def test_before_first_switchpoint_today():
    """06:30 Thursday → no Thursday switchpoint before 06:30; falls back to
    Wednesday's last state ("Off" at 22:00) and points next to Thursday 07:00."""
    now = datetime(2026, 5, 7, 6, 30, 0, tzinfo=timezone.utc)
    coord, dhw = _make_coord(FULL_SCHEDULE)
    with _patch_now(now):
        result = _dhw_schedule_state_now(coord, dhw)

    assert result is not None
    assert result.state == "Off"                                   # Wednesday last
    assert result.next_change == datetime(2026, 5, 7, 7, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 3. After today's last switchpoint – wrap to next day
# ---------------------------------------------------------------------------


def test_after_last_switchpoint_today():
    """23:00 Thursday → last Thursday switchpoint is 22:00 Off; next change is
    Friday's first switchpoint at 07:00."""
    now = datetime(2026, 5, 7, 23, 0, 0, tzinfo=timezone.utc)
    coord, dhw = _make_coord(FULL_SCHEDULE)
    with _patch_now(now):
        result = _dhw_schedule_state_now(coord, dhw)

    assert result is not None
    assert result.state == "Off"
    assert result.next_change == datetime(2026, 5, 8, 7, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 4. Empty schedule → None
# ---------------------------------------------------------------------------


def test_empty_schedule_returns_none():
    coord, dhw = _make_coord({"dailySchedules": []})
    with _patch_now(FIXED_NOW):
        result = _dhw_schedule_state_now(coord, dhw)
    assert result is None


def test_none_schedule_returns_none():
    coord, dhw = _make_coord(None)
    with _patch_now(FIXED_NOW):
        result = _dhw_schedule_state_now(coord, dhw)
    assert result is None


# ---------------------------------------------------------------------------
# 5. Single day, single switchpoint – wraps to same day next week
# ---------------------------------------------------------------------------


def test_single_day_one_switchpoint_wraps_to_next_week():
    """Thursday only, one switchpoint (06:00 On).  At 14:30 the cylinder is On;
    next change wraps forward to next Thursday 06:00 (7 days later)."""
    schedule = {
        "dailySchedules": [
            {"dayOfWeek": "Thursday", "switchpoints": [
                {"timeOfDay": "06:00:00", "state": "On"},
            ]}
        ]
    }
    coord, dhw = _make_coord(schedule)
    with _patch_now(FIXED_NOW):
        result = _dhw_schedule_state_now(coord, dhw)

    assert result is not None
    assert result.state == "On"
    assert result.next_change == datetime(2026, 5, 14, 6, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 6. Timezone-aware: Europe/London BST (UTC+1)
# ---------------------------------------------------------------------------


def test_timezone_aware_next_change_bst():
    """Location is Europe/London (BST, UTC+1).  Schedule switchpoint at 22:00
    local must produce a tz-aware next_change that converts to 21:00 UTC."""
    london = ZoneInfo("Europe/London")
    # 13:30 BST on Thursday 2026-05-07 (= 12:30 UTC)
    now_london = datetime(2026, 5, 7, 13, 30, 0, tzinfo=london)

    schedule = {
        "dailySchedules": [
            {"dayOfWeek": "Thursday", "switchpoints": [
                {"timeOfDay": "07:00:00", "state": "On"},
                {"timeOfDay": "22:00:00", "state": "Off"},
            ]}
        ]
    }
    coord, dhw = _make_coord(schedule, time_zone="Europe/London")
    with _patch_now(now_london):
        result = _dhw_schedule_state_now(coord, dhw)

    assert result is not None
    assert result.state == "On"
    expected_next = datetime(2026, 5, 7, 22, 0, 0, tzinfo=london)
    assert result.next_change == expected_next
    # Formatted for the API it should be 21:00 UTC
    assert _format_until(result.next_change) == "2026-05-07T21:00:00Z"


# ---------------------------------------------------------------------------
# 7. Handler: scheduled Off → TemporaryOverride
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_scheduled_off_sends_temporary_override():
    """When the schedule says Off right now, the handler sends a TemporaryOverride
    state='Off' until the next switchpoint instead of plain FollowSchedule."""
    # 14:30 Thu: Thursday 12:00–18:00 is Off (see FULL_SCHEDULE)
    coord, _ = _make_coord(FULL_SCHEDULE)
    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": coord}}
    handler = _make_dhw_clear_override(hass)

    with _patch_now(FIXED_NOW):
        await handler(_call({ATTR_DHW_ID: DHW_ID}))

    coord.client.async_set_dhw_state.assert_awaited_once_with(
        DHW_ID,
        mode="TemporaryOverride",
        state="Off",
        time_until="2026-05-07T18:00:00Z",
    )
    coord.async_request_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# 8. Handler: scheduled On → FollowSchedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_scheduled_on_sends_follow_schedule():
    """When the schedule says On, plain FollowSchedule is sufficient."""
    # Use 08:00 Thursday: 07:00 On is the last switchpoint before 08:00
    now = datetime(2026, 5, 7, 8, 0, 0, tzinfo=timezone.utc)
    coord, _ = _make_coord(FULL_SCHEDULE)
    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": coord}}
    handler = _make_dhw_clear_override(hass)

    with _patch_now(now):
        await handler(_call({ATTR_DHW_ID: DHW_ID}))

    coord.client.async_set_dhw_state.assert_awaited_once_with(
        DHW_ID,
        mode="FollowSchedule",
    )


# ---------------------------------------------------------------------------
# 9. Handler: no schedule → FollowSchedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_no_schedule_sends_follow_schedule():
    """When no schedule is cached, fall back to plain FollowSchedule."""
    coord, _ = _make_coord(None)
    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": coord}}
    handler = _make_dhw_clear_override(hass)

    with _patch_now(FIXED_NOW):
        await handler(_call({ATTR_DHW_ID: DHW_ID}))

    coord.client.async_set_dhw_state.assert_awaited_once_with(
        DHW_ID,
        mode="FollowSchedule",
    )
    _, kwargs = coord.client.async_set_dhw_state.call_args
    assert "state" not in kwargs
    assert "time_until" not in kwargs
