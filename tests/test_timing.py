"""Tests for the shared timing.schedule_state_now helper.

Scenarios the Dining Room incident surfaced:
  * mid-day wrap: current switchpoint is today's most-recent one, the next
    change is later today
  * end-of-day wrap: last switchpoint of today → first of the next day
  * cross-weekday wrap: Sunday's last switchpoint → Monday's first
  * sparse schedule: missing days are skipped forward until a populated day
  * empty / None schedule → None
  * zone-shaped (heatSetpoint) switchpoints are handled identically to DHW
"""
from __future__ import annotations

import os
import sys

import tests.conftest  # noqa: F401 — HA stubs

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from custom_components.evohome_control.timing import (
    dhw_schedule_state_now,
    format_until,
    schedule_state_now,
    zone_next_switchpoint,
)


# 2026-11-16 is a Monday (weekday=0).
_MON_LOCAL = ZoneInfo("Europe/London")  # Nov -> GMT, UTC+0, matches UTC


_DHW_SCHEDULE_FULL = {
    "dailySchedules": [
        {
            "dayOfWeek": day,
            "switchpoints": [
                {"timeOfDay": "07:00:00", "state": "On"},
                {"timeOfDay": "09:00:00", "state": "Off"},
                {"timeOfDay": "17:00:00", "state": "On"},
                {"timeOfDay": "22:00:00", "state": "Off"},
            ],
        }
        for day in (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )
    ]
}

_ZONE_SCHEDULE_FULL = {
    "dailySchedules": [
        {
            "dayOfWeek": day,
            "switchpoints": [
                {"timeOfDay": "06:30:00", "heatSetpoint": 21.0},
                {"timeOfDay": "08:30:00", "heatSetpoint": 18.0},
                {"timeOfDay": "17:30:00", "heatSetpoint": 21.0},
                {"timeOfDay": "22:00:00", "heatSetpoint": 16.0},
            ],
        }
        for day in (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )
    ]
}


# ---------------------------------------------------------------------------
# Same-day forward wrap (next switchpoint is later today)
# ---------------------------------------------------------------------------


def test_zone_mid_day_next_switchpoint_is_later_today():
    """Monday 10:30 → current block is 08:30/18.0; next is 17:30/21.0 today."""
    now = datetime(2026, 11, 16, 10, 30, tzinfo=timezone.utc)
    nxt = zone_next_switchpoint(_ZONE_SCHEDULE_FULL, tz_id="UTC", now=now)
    assert nxt == datetime(2026, 11, 16, 17, 30, tzinfo=timezone.utc)


def test_zone_full_state_same_day():
    now = datetime(2026, 11, 16, 10, 30, tzinfo=timezone.utc)
    state = schedule_state_now(
        _ZONE_SCHEDULE_FULL, tz_id="UTC", value_key="heatSetpoint", now=now
    )
    assert state is not None
    assert state.value == 18.0
    assert state.next_change == datetime(2026, 11, 16, 17, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# End-of-day wrap: after last switchpoint today → first tomorrow
# ---------------------------------------------------------------------------


def test_zone_wrap_end_of_day_to_next_day():
    """Monday 23:30 → last switchpoint today was 22:00/16.0; next is Tuesday
    06:30/21.0 (same weekly pattern)."""
    now = datetime(2026, 11, 16, 23, 30, tzinfo=timezone.utc)
    nxt = zone_next_switchpoint(_ZONE_SCHEDULE_FULL, tz_id="UTC", now=now)
    assert nxt == datetime(2026, 11, 17, 6, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Cross-weekday wrap: Sunday → Monday
# ---------------------------------------------------------------------------


def test_zone_wrap_sunday_to_monday():
    """Sunday 2026-11-22 23:30 → next switchpoint is Monday 2026-11-23 06:30."""
    now = datetime(2026, 11, 22, 23, 30, tzinfo=timezone.utc)
    nxt = zone_next_switchpoint(_ZONE_SCHEDULE_FULL, tz_id="UTC", now=now)
    assert nxt == datetime(2026, 11, 23, 6, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Sparse schedule: skip empty days forward until a populated one
# ---------------------------------------------------------------------------


_SPARSE_ZONE_SCHEDULE = {
    "dailySchedules": [
        {
            "dayOfWeek": "Monday",
            "switchpoints": [
                {"timeOfDay": "06:30:00", "heatSetpoint": 21.0},
                {"timeOfDay": "22:00:00", "heatSetpoint": 16.0},
            ],
        },
        {"dayOfWeek": "Tuesday", "switchpoints": []},
        {"dayOfWeek": "Wednesday", "switchpoints": []},
        {
            "dayOfWeek": "Thursday",
            "switchpoints": [
                {"timeOfDay": "07:30:00", "heatSetpoint": 20.0},
            ],
        },
    ]
}


def test_zone_sparse_schedule_wraps_over_empty_days():
    """After Monday 22:00, Tuesday + Wednesday are empty → next change is
    Thursday 07:30."""
    now = datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc)
    nxt = zone_next_switchpoint(_SPARSE_ZONE_SCHEDULE, tz_id="UTC", now=now)
    assert nxt == datetime(2026, 11, 19, 7, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Empty / missing schedule returns None
# ---------------------------------------------------------------------------


def test_zone_empty_schedule_returns_none():
    assert (
        zone_next_switchpoint(
            {"dailySchedules": []}, tz_id="UTC", now=datetime(2026, 11, 16, 10, tzinfo=timezone.utc)
        )
        is None
    )


def test_zone_none_schedule_returns_none():
    assert zone_next_switchpoint(None, tz_id="UTC") is None


def test_zone_all_days_empty_switchpoints_returns_none():
    schedule = {
        "dailySchedules": [
            {"dayOfWeek": "Monday", "switchpoints": []},
            {"dayOfWeek": "Tuesday", "switchpoints": []},
        ]
    }
    assert (
        zone_next_switchpoint(
            schedule, tz_id="UTC", now=datetime(2026, 11, 16, 10, tzinfo=timezone.utc)
        )
        is None
    )


# ---------------------------------------------------------------------------
# DHW / zone parity: same logic, different value_key
# ---------------------------------------------------------------------------


def test_dhw_and_zone_use_same_calendar_math():
    """Zone next_switchpoint and DHW next_change line up when the two
    schedules have switchpoints at identical times."""
    aligned_dhw = {
        "dailySchedules": [
            {
                "dayOfWeek": day,
                "switchpoints": [
                    {"timeOfDay": "06:30:00", "state": "On"},
                    {"timeOfDay": "22:00:00", "state": "Off"},
                ],
            }
            for day in ("Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday")
        ]
    }
    aligned_zone = {
        "dailySchedules": [
            {
                "dayOfWeek": day,
                "switchpoints": [
                    {"timeOfDay": "06:30:00", "heatSetpoint": 21.0},
                    {"timeOfDay": "22:00:00", "heatSetpoint": 16.0},
                ],
            }
            for day in ("Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday")
        ]
    }
    now = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    zone_nxt = zone_next_switchpoint(aligned_zone, tz_id="UTC", now=now)
    dhw_state = dhw_schedule_state_now(aligned_dhw, tz_id="UTC", now=now)
    assert dhw_state is not None
    assert dhw_state.next_change == zone_nxt


# ---------------------------------------------------------------------------
# format_until: produces the exact string Resideo expects
# ---------------------------------------------------------------------------


def test_format_until_converts_to_utc_z():
    london = ZoneInfo("Europe/London")
    # BST: 2026-07-01 23:00 local == 22:00Z
    bst = datetime(2026, 7, 1, 23, 0, tzinfo=london)
    assert format_until(bst) == "2026-07-01T22:00:00Z"


def test_format_until_naive_assumed_utc():
    assert format_until(datetime(2026, 11, 16, 23, 0)) == "2026-11-16T23:00:00Z"
