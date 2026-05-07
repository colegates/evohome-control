"""Unit tests for dhw_boost and dhw_clear_override service handlers."""
from __future__ import annotations

import sys
import os

# Ensure stubs are loaded before any integration import (conftest.py does this
# when collected by pytest, but guard here for direct invocation too).
import tests.conftest  # noqa: F401

# Put the repo root on sys.path so `custom_components` is importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.evohome_control.const import (
    ATTR_DHW_ID,
    ATTR_DURATION,
    ATTR_LOCATION_ID,
    ATTR_STATE,
    ATTR_UNTIL,
    DOMAIN,
)
from custom_components.evohome_control.coordinator import (
    DhwData,
    EvohomeData,
    LocationData,
)
from custom_components.evohome_control.services import (
    _DHW_BOOST_SCHEMA,
    _DHW_CLEAR_SCHEMA,
    _make_dhw_boost,
    _make_dhw_clear_override,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
DHW_ID = "3932871"
LOCATION_ID = "1706229"


def _make_hass(dhw_id: str = DHW_ID, location_id: str = LOCATION_ID):
    """Return a minimal mock hass with one coordinator/location/DHW."""
    dhw = DhwData(
        location_id=location_id,
        tcs_id="tcs1",
        dhw_id=dhw_id,
        temperature=55.0,
        is_available=True,
        state="Off",
        mode="FollowSchedule",
    )
    loc = LocationData(
        location_id=location_id,
        name="My Home",
        city=None,
        country=None,
        time_zone="Europe/London",
        dhw=dhw,
    )
    evohome_data = EvohomeData(locations={location_id: loc})

    coord = MagicMock()
    coord.data = evohome_data
    coord.client.async_set_dhw_state = AsyncMock()
    coord.async_request_refresh = AsyncMock()

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry1": coord}}
    return hass, coord


def _call(data: dict):
    c = MagicMock()
    c.data = data
    return c


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


def test_schema_rejects_both_duration_and_until():
    """Providing both duration and until must raise vol.Invalid."""
    with pytest.raises(vol.Invalid):
        _DHW_BOOST_SCHEMA(
            {
                ATTR_DHW_ID: DHW_ID,
                ATTR_DURATION: timedelta(hours=1),
                ATTR_UNTIL: datetime(2026, 5, 7, 14, 0, 0),
            }
        )


def test_schema_accepts_duration_only():
    result = _DHW_BOOST_SCHEMA({ATTR_DHW_ID: DHW_ID, ATTR_DURATION: timedelta(hours=2)})
    assert result[ATTR_DURATION] == timedelta(hours=2)
    assert result[ATTR_STATE] == "On"  # default applied


def test_schema_accepts_until_only():
    until = datetime(2026, 5, 7, 14, 30, 0)
    result = _DHW_BOOST_SCHEMA({ATTR_DHW_ID: DHW_ID, ATTR_UNTIL: until})
    assert result[ATTR_UNTIL] == until


def test_schema_accepts_neither_duration_nor_until():
    result = _DHW_BOOST_SCHEMA({ATTR_DHW_ID: DHW_ID})
    assert ATTR_DURATION not in result
    assert ATTR_UNTIL not in result


def test_schema_rejects_invalid_state():
    with pytest.raises(vol.Invalid):
        _DHW_BOOST_SCHEMA({ATTR_DHW_ID: DHW_ID, ATTR_STATE: "Warm"})


def test_dhw_clear_schema_requires_dhw_id():
    with pytest.raises(vol.Invalid):
        _DHW_CLEAR_SCHEMA({})


# ---------------------------------------------------------------------------
# Handler: dhw_boost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boost_with_duration_computes_correct_until():
    """duration=90min → time_until = FIXED_NOW + 90min, formatted as ISO UTC."""
    hass, coord = _make_hass()
    handler = _make_dhw_boost(hass)

    with patch(
        "custom_components.evohome_control.services.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = FIXED_NOW
        await handler(
            _call(
                {
                    ATTR_DHW_ID: DHW_ID,
                    ATTR_DURATION: timedelta(minutes=90),
                    ATTR_STATE: "On",
                }
            )
        )

    coord.client.async_set_dhw_state.assert_awaited_once_with(
        DHW_ID,
        mode="TemporaryOverride",
        state="On",
        time_until="2026-05-07T13:30:00Z",
    )
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_boost_with_until_passes_through():
    """until datetime is passed directly to the API (converted to UTC ISO string)."""
    hass, coord = _make_hass()
    handler = _make_dhw_boost(hass)

    until = datetime(2026, 5, 7, 15, 0, 0, tzinfo=timezone.utc)
    await handler(
        _call(
            {
                ATTR_DHW_ID: DHW_ID,
                ATTR_UNTIL: until,
                ATTR_STATE: "On",
            }
        )
    )

    coord.client.async_set_dhw_state.assert_awaited_once_with(
        DHW_ID,
        mode="TemporaryOverride",
        state="On",
        time_until="2026-05-07T15:00:00Z",
    )


@pytest.mark.asyncio
async def test_boost_default_duration_is_one_hour():
    """When neither duration nor until is given, boost defaults to 1 hour."""
    hass, coord = _make_hass()
    handler = _make_dhw_boost(hass)

    with patch(
        "custom_components.evohome_control.services.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = FIXED_NOW
        await handler(_call({ATTR_DHW_ID: DHW_ID}))

    coord.client.async_set_dhw_state.assert_awaited_once_with(
        DHW_ID,
        mode="TemporaryOverride",
        state="On",
        time_until="2026-05-07T13:00:00Z",
    )


@pytest.mark.asyncio
async def test_boost_state_off():
    """state='Off' is forwarded correctly."""
    hass, coord = _make_hass()
    handler = _make_dhw_boost(hass)

    with patch(
        "custom_components.evohome_control.services.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = FIXED_NOW
        await handler(
            _call(
                {
                    ATTR_DHW_ID: DHW_ID,
                    ATTR_DURATION: timedelta(hours=1),
                    ATTR_STATE: "Off",
                }
            )
        )

    coord.client.async_set_dhw_state.assert_awaited_once_with(
        DHW_ID,
        mode="TemporaryOverride",
        state="Off",
        time_until="2026-05-07T13:00:00Z",
    )


# ---------------------------------------------------------------------------
# Handler: dhw_clear_override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_override_sends_follow_schedule():
    """dhw_clear_override must call async_set_dhw_state with mode=FollowSchedule only."""
    hass, coord = _make_hass()
    handler = _make_dhw_clear_override(hass)

    await handler(_call({ATTR_DHW_ID: DHW_ID}))

    coord.client.async_set_dhw_state.assert_awaited_once_with(
        DHW_ID,
        mode="FollowSchedule",
    )
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_override_no_state_no_time_until():
    """Ensure no state or time_until keyword args are sent for clear_override."""
    hass, coord = _make_hass()
    handler = _make_dhw_clear_override(hass)

    await handler(_call({ATTR_DHW_ID: DHW_ID}))

    _, kwargs = coord.client.async_set_dhw_state.call_args
    assert "state" not in kwargs
    assert "time_until" not in kwargs


# ---------------------------------------------------------------------------
# Resolve helper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boost_raises_for_unknown_dhw_id():
    from tests.conftest import HomeAssistantError

    hass, _ = _make_hass()
    handler = _make_dhw_boost(hass)

    with pytest.raises(HomeAssistantError, match="Unknown evohome dhw_id"):
        await handler(_call({ATTR_DHW_ID: "nonexistent"}))


@pytest.mark.asyncio
async def test_boost_location_id_disambiguates():
    """Passing location_id restricts lookup to that location."""
    hass, coord = _make_hass()
    handler = _make_dhw_boost(hass)

    with patch(
        "custom_components.evohome_control.services.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = FIXED_NOW
        await handler(
            _call(
                {
                    ATTR_DHW_ID: DHW_ID,
                    ATTR_LOCATION_ID: LOCATION_ID,
                }
            )
        )

    coord.client.async_set_dhw_state.assert_awaited_once()
