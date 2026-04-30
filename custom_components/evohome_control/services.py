"""Services exposed by the Evohome Control integration.

The headline service here is ``evohome_control.set_schedule`` which lets a
user write a complete weekly schedule for a single zone, something the
official integration does not currently support.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_DAILY_SCHEDULES,
    ATTR_DAY_OF_WEEK,
    ATTR_DURATION,
    ATTR_FROM_ZONE_ID,
    ATTR_HEAT_SETPOINT,
    ATTR_LOCATION_ID,
    ATTR_PERMANENT,
    ATTR_SCHEDULE,
    ATTR_SWITCHPOINTS,
    ATTR_SYSTEM_MODE,
    ATTR_TEMPERATURE,
    ATTR_TIME_OF_DAY,
    ATTR_TO_ZONE_ID,
    ATTR_UNTIL,
    ATTR_ZONE_ID,
    DAYS_OF_WEEK,
    DOMAIN,
    SERVICE_CLEAR_ZONE_OVERRIDE,
    SERVICE_COPY_SCHEDULE,
    SERVICE_GET_SCHEDULE,
    SERVICE_REFRESH_SCHEDULES,
    SERVICE_SET_SCHEDULE,
    SERVICE_SET_SYSTEM_MODE,
    SERVICE_SET_ZONE_OVERRIDE,
)
from .coordinator import EvohomeDataUpdateCoordinator, LocationData, ZoneData

_LOGGER = logging.getLogger(__name__)

# A schedule may be passed in the API's native shape:
#   {"dailySchedules": [{"dayOfWeek": "Monday", "switchpoints": [...]}, ...]}
# or in the snake_case form used by HA and Resideo's library:
#   {"daily_schedules": [{"day_of_week": "Monday", "switchpoints": [...]}, ...]}
# We accept either and normalize when sending.

_SWITCHPOINT_SCHEMA = vol.Schema(
    {
        vol.Required(
            vol.Any("timeOfDay", ATTR_TIME_OF_DAY)
        ): cv.matches_regex(r"^\d{2}:\d{2}(?::\d{2})?$"),
        vol.Required(
            vol.Any("heatSetpoint", ATTR_HEAT_SETPOINT)
        ): vol.All(vol.Coerce(float), vol.Range(min=5, max=35)),
    },
    extra=vol.ALLOW_EXTRA,
)

_DAY_SCHEMA = vol.Schema(
    {
        vol.Required(vol.Any("dayOfWeek", ATTR_DAY_OF_WEEK)): vol.In(DAYS_OF_WEEK),
        vol.Required(vol.Any("switchpoints", ATTR_SWITCHPOINTS)): vol.All(
            cv.ensure_list, [_SWITCHPOINT_SCHEMA]
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

_SCHEDULE_SCHEMA = vol.Any(
    vol.Schema(
        {
            vol.Required(
                vol.Any("dailySchedules", ATTR_DAILY_SCHEDULES)
            ): vol.All(cv.ensure_list, [_DAY_SCHEMA])
        },
        extra=vol.ALLOW_EXTRA,
    ),
    vol.All(cv.ensure_list, [_DAY_SCHEMA]),
)

_GET_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ZONE_ID): cv.string,
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_SET_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ZONE_ID): cv.string,
        vol.Required(ATTR_SCHEDULE): _SCHEDULE_SCHEMA,
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_COPY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_FROM_ZONE_ID): cv.string,
        vol.Required(ATTR_TO_ZONE_ID): cv.string,
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_OVERRIDE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_ZONE_ID): cv.string,
            vol.Required(ATTR_TEMPERATURE): vol.All(
                vol.Coerce(float), vol.Range(min=5, max=35)
            ),
            vol.Exclusive(ATTR_DURATION, "until"): cv.time_period,
            vol.Exclusive(ATTR_UNTIL, "until"): cv.datetime,
            vol.Optional(ATTR_LOCATION_ID): cv.string,
        }
    ),
)
_CLEAR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ZONE_ID): cv.string,
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_SET_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LOCATION_ID): cv.string,
        vol.Required(ATTR_SYSTEM_MODE): cv.string,
        vol.Optional(ATTR_PERMANENT, default=True): cv.boolean,
        vol.Optional(ATTR_UNTIL): cv.datetime,
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        _make_get_schedule(hass),
        schema=_GET_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        _make_set_schedule(hass),
        schema=_SET_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_SCHEDULES,
        _make_refresh(hass),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_COPY_SCHEDULE,
        _make_copy_schedule(hass),
        schema=_COPY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ZONE_OVERRIDE,
        _make_set_override(hass),
        schema=_OVERRIDE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_ZONE_OVERRIDE,
        _make_clear_override(hass),
        schema=_CLEAR_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SYSTEM_MODE,
        _make_set_system_mode(hass),
        schema=_SET_MODE_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for svc in (
        SERVICE_GET_SCHEDULE,
        SERVICE_SET_SCHEDULE,
        SERVICE_REFRESH_SCHEDULES,
        SERVICE_COPY_SCHEDULE,
        SERVICE_SET_ZONE_OVERRIDE,
        SERVICE_CLEAR_ZONE_OVERRIDE,
        SERVICE_SET_SYSTEM_MODE,
    ):
        hass.services.async_remove(DOMAIN, svc)


def _all_coordinators(hass: HomeAssistant) -> list[EvohomeDataUpdateCoordinator]:
    return list(hass.data.get(DOMAIN, {}).values())


def _resolve_zone(
    hass: HomeAssistant, zone_id: str, location_id: str | None
) -> tuple[EvohomeDataUpdateCoordinator, ZoneData]:
    matches: list[tuple[EvohomeDataUpdateCoordinator, ZoneData]] = []
    for coord in _all_coordinators(hass):
        for loc in coord.data.locations.values():
            if location_id and loc.location_id != location_id:
                continue
            if zone_id in loc.zones:
                matches.append((coord, loc.zones[zone_id]))
    if not matches:
        raise HomeAssistantError(f"Unknown evohome zone_id: {zone_id}")
    if len(matches) > 1:
        raise HomeAssistantError(
            f"Ambiguous zone_id {zone_id} - specify location_id"
        )
    return matches[0]


def _normalize_schedule(schedule: Any) -> dict[str, Any]:
    """Convert any accepted shape into ``{"dailySchedules": [...]}`` (camelCase)."""
    if isinstance(schedule, list):
        days = schedule
    else:
        days = schedule.get("dailySchedules") or schedule.get(ATTR_DAILY_SCHEDULES)

    out_days = []
    for d in days:
        dow = d.get("dayOfWeek") or d.get(ATTR_DAY_OF_WEEK)
        sps = d.get("switchpoints") or d.get(ATTR_SWITCHPOINTS) or []
        out_sps = []
        for sp in sps:
            tod = sp.get("timeOfDay") or sp.get(ATTR_TIME_OF_DAY)
            if len(tod) == 5:  # HH:MM -> HH:MM:00
                tod = f"{tod}:00"
            out_sps.append(
                {
                    "heatSetpoint": float(
                        sp.get("heatSetpoint")
                        if "heatSetpoint" in sp
                        else sp.get(ATTR_HEAT_SETPOINT)
                    ),
                    "timeOfDay": tod,
                }
            )
        out_days.append({"dayOfWeek": dow, "switchpoints": out_sps})

    # The API requires all 7 days; fill any missing day by copying the
    # previous day so callers can submit a partial schedule for convenience.
    by_day = {d["dayOfWeek"]: d for d in out_days}
    completed = []
    last = None
    for day in DAYS_OF_WEEK:
        if day in by_day:
            last = by_day[day]
        elif last is not None:
            last = {"dayOfWeek": day, "switchpoints": last["switchpoints"]}
        else:
            raise HomeAssistantError(
                f"Schedule is missing day '{day}' and no preceding day to copy"
            )
        completed.append(last)
    return {"dailySchedules": completed}


def _make_get_schedule(hass: HomeAssistant):
    async def _get(call: ServiceCall) -> ServiceResponse:
        coord, zone = _resolve_zone(
            hass, call.data[ATTR_ZONE_ID], call.data.get(ATTR_LOCATION_ID)
        )
        schedule = await coord.client.async_get_zone_schedule(zone.zone_id)
        return {
            "location_id": zone.location_id,
            "zone_id": zone.zone_id,
            "zone_name": zone.name,
            "schedule": schedule,
        }

    return _get


def _make_set_schedule(hass: HomeAssistant):
    async def _set(call: ServiceCall) -> None:
        coord, zone = _resolve_zone(
            hass, call.data[ATTR_ZONE_ID], call.data.get(ATTR_LOCATION_ID)
        )
        body = _normalize_schedule(call.data[ATTR_SCHEDULE])
        await coord.client.async_set_zone_schedule(zone.zone_id, body)
        await coord.async_request_refresh()

    return _set


def _make_refresh(hass: HomeAssistant):
    async def _refresh(_call: ServiceCall) -> None:
        for coord in _all_coordinators(hass):
            await coord.async_request_refresh()

    return _refresh


def _resolve_location(
    hass: HomeAssistant, location_id: str
) -> tuple[EvohomeDataUpdateCoordinator, LocationData]:
    for coord in _all_coordinators(hass):
        if location_id in coord.data.locations:
            return coord, coord.data.locations[location_id]
    raise HomeAssistantError(f"Unknown evohome location_id: {location_id}")


def _format_until(value: datetime) -> str:
    """Format a datetime as the API-expected ISO 8601 string in UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_copy_schedule(hass: HomeAssistant):
    async def _copy(call: ServiceCall) -> None:
        loc_id = call.data.get(ATTR_LOCATION_ID)
        coord, src = _resolve_zone(hass, call.data[ATTR_FROM_ZONE_ID], loc_id)
        coord_to, dst = _resolve_zone(hass, call.data[ATTR_TO_ZONE_ID], loc_id)
        if coord is not coord_to:
            raise HomeAssistantError(
                "Cannot copy schedule across different evohome accounts"
            )
        sched = await coord.client.async_get_zone_schedule(src.zone_id)
        await coord.client.async_set_zone_schedule(dst.zone_id, sched)
        await coord.async_request_refresh()

    return _copy


def _make_set_override(hass: HomeAssistant):
    async def _override(call: ServiceCall) -> None:
        coord, zone = _resolve_zone(
            hass, call.data[ATTR_ZONE_ID], call.data.get(ATTR_LOCATION_ID)
        )
        temp = float(call.data[ATTR_TEMPERATURE])
        until_dt: datetime | None = None
        if ATTR_UNTIL in call.data:
            until_dt = call.data[ATTR_UNTIL]
        elif ATTR_DURATION in call.data:
            until_dt = datetime.now(timezone.utc) + call.data[ATTR_DURATION]

        if until_dt is None:
            await coord.client.async_set_zone_heat_setpoint(
                zone.zone_id,
                setpoint_mode="PermanentOverride",
                heat_setpoint_value=temp,
            )
        else:
            await coord.client.async_set_zone_heat_setpoint(
                zone.zone_id,
                setpoint_mode="TemporaryOverride",
                heat_setpoint_value=temp,
                time_until=_format_until(until_dt),
            )
        await coord.async_request_refresh()

    return _override


def _make_clear_override(hass: HomeAssistant):
    async def _clear(call: ServiceCall) -> None:
        coord, zone = _resolve_zone(
            hass, call.data[ATTR_ZONE_ID], call.data.get(ATTR_LOCATION_ID)
        )
        await coord.client.async_set_zone_heat_setpoint(
            zone.zone_id, setpoint_mode="FollowSchedule"
        )
        await coord.async_request_refresh()

    return _clear


def _make_set_system_mode(hass: HomeAssistant):
    async def _set_mode(call: ServiceCall) -> None:
        coord, loc = _resolve_location(hass, call.data[ATTR_LOCATION_ID])
        if loc.primary_tcs_id is None:
            raise HomeAssistantError(f"Location {loc.location_id} has no TCS")
        mode = call.data[ATTR_SYSTEM_MODE]
        if loc.allowed_system_modes and mode not in loc.allowed_system_modes:
            raise HomeAssistantError(
                f"Mode {mode!r} not in allowed list {loc.allowed_system_modes}"
            )
        permanent = call.data.get(ATTR_PERMANENT, True)
        until_dt: datetime | None = call.data.get(ATTR_UNTIL)
        if until_dt is not None:
            permanent = False
        await coord.client.async_set_system_mode(
            loc.primary_tcs_id,
            system_mode=mode,
            permanent=permanent,
            time_until=_format_until(until_dt) if until_dt else None,
        )
        await coord.async_request_refresh()

    return _set_mode
