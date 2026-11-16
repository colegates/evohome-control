"""Tests for the Windows → IANA tz mapping and off-event-loop resolution.

Covers the bug where Resideo returns Windows-style ids (``GMTStandardTime``
for Colegates) which caused ``timing._resolve_tz`` to fall back to UTC - fine
in winter when the UK is already on UTC, but silently an hour off during BST.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import tests.conftest  # noqa: F401 — stubs HA before any integration import

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from custom_components.evohome_control import timing
from custom_components.evohome_control.timing import (
    _reset_warned_ids_for_tests,
    async_resolve_tz,
    format_until,
    resolve_tz_sync,
    zone_next_switchpoint,
)


@pytest.fixture(autouse=True)
def _reset_warn_memo():
    """Give each test a clean "already warned" slate + lru_cache."""
    _reset_warned_ids_for_tests()
    yield
    _reset_warned_ids_for_tests()


# ---------------------------------------------------------------------------
# Windows ids map to the expected IANA zones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("windows_id", "expected_iana"),
    [
        # Colegates's actual id
        ("GMTStandardTime", "Europe/London"),
        # Spaced form Resideo sometimes emits
        ("GMT Standard Time", "Europe/London"),
        # Western / Central Europe variants
        ("W. Europe Standard Time", "Europe/Berlin"),
        ("WEuropeStandardTime", "Europe/Berlin"),
        ("Romance Standard Time", "Europe/Paris"),
        ("Central Europe Standard Time", "Europe/Budapest"),
        ("Central European Standard Time", "Europe/Warsaw"),
        # UTC passes through
        ("UTC", "UTC"),
    ],
)
def test_resolve_tz_maps_windows_ids(windows_id: str, expected_iana: str):
    tz = resolve_tz_sync(windows_id)
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == expected_iana


def test_resolve_tz_preserves_existing_iana_ids():
    tz = resolve_tz_sync("Europe/London")
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "Europe/London"


# ---------------------------------------------------------------------------
# Unknown ids: fall back to the caller-supplied default + ONE warning
# ---------------------------------------------------------------------------


def test_resolve_tz_unknown_id_falls_back_to_default(caplog):
    london = ZoneInfo("Europe/London")
    caplog.set_level(logging.WARNING, logger=timing._LOGGER.name)

    tz = resolve_tz_sync("NotARealZone", default=london, source="location 1706229")
    assert tz is london

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "NotARealZone" in warnings[0].getMessage()
    assert "1706229" in warnings[0].getMessage()


def test_resolve_tz_warns_once_per_unknown_id(caplog):
    london = ZoneInfo("Europe/London")
    caplog.set_level(logging.WARNING, logger=timing._LOGGER.name)

    for _ in range(5):
        tz = resolve_tz_sync("NotARealZone", default=london)
        assert tz is london

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_resolve_tz_unknown_id_defaults_to_utc_when_no_default(caplog):
    caplog.set_level(logging.WARNING, logger=timing._LOGGER.name)
    tz = resolve_tz_sync("NotARealZone")
    assert tz is timezone.utc


def test_resolve_tz_empty_id_returns_default_without_warning(caplog):
    caplog.set_level(logging.WARNING, logger=timing._LOGGER.name)
    london = ZoneInfo("Europe/London")
    assert resolve_tz_sync(None, default=london) is london
    assert resolve_tz_sync("", default=london) is london
    # No warnings - empty/None is the normal "no tz on the account" case.
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


# ---------------------------------------------------------------------------
# BST: summer next_change must be the correct UTC instant
# ---------------------------------------------------------------------------


_BST_SCHEDULE = {
    "dailySchedules": [
        {
            "dayOfWeek": day,
            "switchpoints": [
                {"timeOfDay": "07:00:00", "heatSetpoint": 21.0},
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


def test_bst_next_change_is_in_utc_terms():
    """22:00 BST on a July day must serialise to 21:00Z, not 22:00Z.

    Reproduces the Colegates bug: when Resideo returns ``GMTStandardTime`` and
    the integration silently falls back to UTC, the switchpoint ends an hour
    late through the summer.
    """
    london = resolve_tz_sync("GMTStandardTime")
    # 2026-07-01 18:00 BST == 17:00 UTC - after the 07:00 switchpoint, before 22:00
    now_bst = datetime(2026, 7, 1, 18, 0, tzinfo=london)

    nxt = zone_next_switchpoint(_BST_SCHEDULE, tz=london, now=now_bst)

    assert nxt is not None
    # 22:00 BST == 21:00Z - the actual API timeUntil string:
    assert format_until(nxt) == "2026-07-01T21:00:00Z"


def test_bst_cross_midnight_wraps_in_local_time():
    """After the last BST switchpoint, next change is 07:00 next morning
    local - 06:00Z."""
    london = resolve_tz_sync("GMTStandardTime")
    now_bst = datetime(2026, 7, 1, 23, 30, tzinfo=london)

    nxt = zone_next_switchpoint(_BST_SCHEDULE, tz=london, now=now_bst)

    assert nxt is not None
    assert format_until(nxt) == "2026-07-02T06:00:00Z"


def test_gmt_winter_next_change_matches_local_hour():
    """In GMT (winter) the UTC string and local hour agree; sanity check."""
    london = resolve_tz_sync("GMTStandardTime")
    now_gmt = datetime(2026, 11, 16, 18, 0, tzinfo=london)

    nxt = zone_next_switchpoint(_BST_SCHEDULE, tz=london, now=now_gmt)

    assert nxt is not None
    assert format_until(nxt) == "2026-11-16T22:00:00Z"


# ---------------------------------------------------------------------------
# async_resolve_tz: runs off the event loop and falls back to HA default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_resolve_tz_uses_hass_executor_for_first_lookup():
    """The blocking ``ZoneInfo(...)`` call must be scheduled via
    ``hass.async_add_executor_job`` so HA doesn't flag a blocking call."""
    _reset_warned_ids_for_tests()  # ensure a real miss (bypass lru_cache)

    hass = MagicMock()
    executor_calls: list = []

    async def _fake_executor(func, *args):
        executor_calls.append((func, args))
        return func(*args)

    hass.async_add_executor_job = _fake_executor

    tz = await async_resolve_tz(hass, "GMTStandardTime")
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "Europe/London"
    assert len(executor_calls) == 1  # the resolve ran off the event loop


@pytest.mark.asyncio
async def test_async_resolve_tz_unknown_falls_back_to_ha_default(caplog):
    """An unknown id must fall back to HA's default time zone, not UTC."""
    _reset_warned_ids_for_tests()
    caplog.set_level(logging.WARNING, logger=timing._LOGGER.name)

    hass = MagicMock()

    async def _fake_executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = _fake_executor

    paris = ZoneInfo("Europe/Paris")
    tz = await async_resolve_tz(
        hass, "DefinitelyNotAZone", default=paris, source="location 999"
    )
    assert tz is paris

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "DefinitelyNotAZone" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Integration: coordinator loads LocationData with a resolved tz
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_resolves_windows_id_for_each_location():
    """The coordinator should attach a ``ZoneInfo('Europe/London')`` to a
    location whose Resideo ``timeZoneId`` is ``"GMTStandardTime"``."""
    from custom_components.evohome_control.coordinator import (
        EvohomeDataUpdateCoordinator,
    )

    hass = MagicMock()
    hass.data = {}

    async def _fake_executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = _fake_executor

    coord = EvohomeDataUpdateCoordinator.__new__(EvohomeDataUpdateCoordinator)
    coord.hass = hass
    coord.client = MagicMock()
    coord._installation = [
        {
            "locationInfo": {
                "locationId": "1706229",
                "name": "Colegates North",
                "city": "Surrey",
                "country": "UnitedKingdom",
                "timeZone": {"timeZoneId": "GMTStandardTime"},
            },
            "gateways": [],
        }
    ]
    coord._installation_fetched_at = datetime(2026, 11, 16, tzinfo=timezone.utc)

    async def _no_op(*_a, **_kw):
        return None

    async def _refresh_installation(self):
        pass

    coord._refresh_installation = _refresh_installation.__get__(coord, type(coord))
    coord._update_status = _no_op
    coord._update_schedules = _no_op

    data = await coord._async_update_data()
    loc = data.locations["1706229"]
    assert isinstance(loc.tz, ZoneInfo)
    assert str(loc.tz) == "Europe/London"
    assert loc.time_zone == "GMTStandardTime"  # raw id preserved too
