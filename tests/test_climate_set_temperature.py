"""Regression tests for the Dining Room incident.

ClimateEntity.async_set_temperature used to send PermanentOverride, so every
climate.set_temperature from HA (dashboard thermostat card, voice assistants,
Assist, MCP clients) pinned the zone forever. It now sends TemporaryOverride
with timeUntil = next scheduled switchpoint, with a 1-hour fallback when the
schedule is unavailable.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tests.conftest  # noqa: F401

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from custom_components.evohome_control.climate import EvohomeZoneClimate
from custom_components.evohome_control.coordinator import (
    EvohomeData,
    LocationData,
    ZoneData,
)


ZONE_ID = "1909981"
LOCATION_ID = "1706229"

# Dining Room's actual (sparse) schedule from the incident.
_SCHEDULE = {
    "dailySchedules": [
        {
            "dayOfWeek": day,
            "switchpoints": [
                {"timeOfDay": "09:00:00", "heatSetpoint": 10.0},
                {"timeOfDay": "15:00:00", "heatSetpoint": 10.5},
                {"timeOfDay": "20:00:00", "heatSetpoint": 11.0},
                {"timeOfDay": "22:00:00", "heatSetpoint": 10.0},
            ],
        }
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday",
                    "Friday", "Saturday", "Sunday")
    ]
}


def _make_zone(schedule: dict | None = None) -> ZoneData:
    return ZoneData(
        location_id=LOCATION_ID,
        location_name="Colegates North",
        tcs_id="tcs1",
        zone_id=ZONE_ID,
        name="Dining Room",
        zone_type="RadiatorZone",
        min_setpoint=5.0,
        max_setpoint=35.0,
        setpoint_resolution=0.5,
        allowed_modes=["PermanentOverride", "FollowSchedule", "TemporaryOverride"],
        temperature=19.0,
        target_setpoint=20.0,
        setpoint_mode="FollowSchedule",
        is_available=True,
        schedule=schedule,
    )


def _make_coordinator(zone: ZoneData, time_zone: str = "UTC") -> MagicMock:
    loc = LocationData(
        location_id=LOCATION_ID,
        name="Colegates North",
        city=None,
        country=None,
        time_zone=time_zone,
        zones={ZONE_ID: zone},
    )
    data = EvohomeData(locations={LOCATION_ID: loc})
    coord = MagicMock()
    coord.data = data
    coord.client.async_set_zone_heat_setpoint = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


def _make_entity(coord: MagicMock) -> EvohomeZoneClimate:
    # Avoid CoordinatorEntity.__init__ side effects by instantiating via __new__
    entity = EvohomeZoneClimate.__new__(EvohomeZoneClimate)
    entity.coordinator = coord
    entity._zone_id = ZONE_ID
    entity._context = None
    entity.hass = MagicMock()
    entity.hass.auth.async_get_user = AsyncMock(return_value=None)
    return entity


@pytest.mark.asyncio
async def test_set_temperature_uses_temporary_override_until_next_switchpoint():
    """A plain climate.set_temperature call must NOT be a permanent hold.

    At 2026-11-16 (Monday) 10:00 UTC the Dining Room schedule's next
    switchpoint is 15:00. The write must be TemporaryOverride with timeUntil
    = 2026-11-16T15:00:00Z, never PermanentOverride.
    """
    zone = _make_zone(_SCHEDULE)
    coord = _make_coordinator(zone, time_zone="UTC")
    entity = _make_entity(coord)

    # Freeze "now" at Monday 10:00 UTC inside the timing module so the
    # entity's next-switchpoint lookup is deterministic.
    fixed = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    import datetime as dt_module
    with patch(
        "custom_components.evohome_control.timing.datetime",
        **{
            "now.return_value": fixed,
            "side_effect": lambda *a, **kw: dt_module.datetime(*a, **kw),
        },
    ):
        await entity.async_set_temperature(temperature=20.0)

    coord.client.async_set_zone_heat_setpoint.assert_awaited_once()
    _, kwargs = coord.client.async_set_zone_heat_setpoint.call_args
    assert kwargs["setpoint_mode"] == "TemporaryOverride"
    assert kwargs["heat_setpoint_value"] == 20.0
    assert kwargs["time_until"] == "2026-11-16T15:00:00Z"


@pytest.mark.asyncio
async def test_set_temperature_falls_back_to_one_hour_when_no_schedule():
    """When the schedule is missing we never pin the zone - fall back to a
    1-hour TemporaryOverride (same default as the temporary_override preset)."""
    zone = _make_zone(schedule=None)
    coord = _make_coordinator(zone)
    entity = _make_entity(coord)

    before = datetime.now(timezone.utc)
    await entity.async_set_temperature(temperature=21.5)
    after = datetime.now(timezone.utc)

    coord.client.async_set_zone_heat_setpoint.assert_awaited_once()
    _, kwargs = coord.client.async_set_zone_heat_setpoint.call_args
    assert kwargs["setpoint_mode"] == "TemporaryOverride"
    # The timeUntil should be ~1 hour from now, never PermanentOverride.
    assert kwargs["time_until"].endswith("Z")
    until_dt = datetime.strptime(
        kwargs["time_until"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    delta = until_dt - before
    assert timedelta(minutes=59) <= delta <= (after - before) + timedelta(hours=1, seconds=1)


@pytest.mark.asyncio
async def test_set_temperature_falls_back_when_schedule_is_empty():
    zone = _make_zone(schedule={"dailySchedules": []})
    coord = _make_coordinator(zone)
    entity = _make_entity(coord)
    await entity.async_set_temperature(temperature=19.0)
    _, kwargs = coord.client.async_set_zone_heat_setpoint.call_args
    assert kwargs["setpoint_mode"] == "TemporaryOverride"


@pytest.mark.asyncio
async def test_set_temperature_never_sends_permanent_override():
    """Belt and braces against regression."""
    for schedule in (_SCHEDULE, None, {"dailySchedules": []}):
        zone = _make_zone(schedule)
        coord = _make_coordinator(zone)
        entity = _make_entity(coord)
        await entity.async_set_temperature(temperature=22.0)
        _, kwargs = coord.client.async_set_zone_heat_setpoint.call_args
        assert kwargs["setpoint_mode"] != "PermanentOverride"


@pytest.mark.asyncio
async def test_set_hvac_mode_heat_stays_permanent():
    """Explicit hvac_mode=HEAT is still a permanent hold - unchanged behaviour."""
    from homeassistant.components.climate import HVACMode

    heat_mode = MagicMock()
    heat_mode.__eq__ = lambda self, other: other is HVACMode.HEAT

    zone = _make_zone(_SCHEDULE)
    coord = _make_coordinator(zone)
    entity = _make_entity(coord)

    # Easier: directly assert the body of async_set_hvac_mode by patching
    # the HVACMode sentinels used in the if-chain.
    with patch(
        "custom_components.evohome_control.climate.HVACMode"
    ) as mode_stub:
        mode_stub.AUTO = "AUTO"
        mode_stub.OFF = "OFF"
        mode_stub.HEAT = "HEAT"
        await entity.async_set_hvac_mode("HEAT")

    _, kwargs = coord.client.async_set_zone_heat_setpoint.call_args
    assert kwargs["setpoint_mode"] == "PermanentOverride"
