"""Services exposed by the Evohome Control integration.

The headline service here is ``evohome_control.set_schedule`` which lets a
user write a complete weekly schedule for a single zone, something the
official integration does not currently support.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_DAILY_SCHEDULES,
    ATTR_DAY_OF_WEEK,
    ATTR_DAYS_OF_WEEK,
    ATTR_DHW_ID,
    ATTR_DURATION,
    ATTR_FROM_ZONE_ID,
    ATTR_HEAT_SETPOINT,
    ATTR_LOCATION_ID,
    ATTR_NAME,
    ATTR_PERMANENT,
    ATTR_PRESET_NAME,
    ATTR_SCHEDULE,
    ATTR_SCHEDULES,
    ATTR_STATE,
    ATTR_SWITCHPOINTS,
    ATTR_SYSTEM_MODE,
    ATTR_TEMPERATURE,
    ATTR_TIME_OF_DAY,
    ATTR_TO_ZONE_ID,
    ATTR_UNTIL,
    ATTR_ZONE_ID,
    ATTR_ZONE_IDS,
    DAYS_OF_WEEK,
    DOMAIN,
    SERVICE_APPLY_DAY_SCHEDULE,
    SERVICE_APPLY_PRESET,
    SERVICE_AWAY_UNTIL,
    SERVICE_BOOST,
    SERVICE_CLEAR_ZONE_OVERRIDE,
    SERVICE_COPY_SCHEDULE,
    SERVICE_DELETE_PRESET,
    SERVICE_DHW_BOOST,
    SERVICE_DHW_CLEAR_OVERRIDE,
    SERVICE_EXPORT_SCHEDULES,
    SERVICE_GET_SCHEDULE,
    SERVICE_IMPORT_SCHEDULES,
    SERVICE_LIST_PRESETS,
    SERVICE_REFRESH_SCHEDULES,
    SERVICE_SAVE_PRESET,
    SERVICE_SET_SCHEDULE,
    SERVICE_SET_SYSTEM_MODE,
    SERVICE_SET_ZONE_OVERRIDE,
)
from .coordinator import DhwData, EvohomeDataUpdateCoordinator, LocationData, ZoneData
from .presets import PresetStore

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
            vol.Exclusive(ATTR_ZONE_ID, "zone"): cv.string,
            vol.Exclusive(ATTR_ZONE_IDS, "zone"): vol.All(
                cv.ensure_list, [cv.string]
            ),
            vol.Required(ATTR_TEMPERATURE): vol.All(
                vol.Coerce(float), vol.Range(min=5, max=35)
            ),
            vol.Exclusive(ATTR_DURATION, "until"): cv.time_period,
            vol.Exclusive(ATTR_UNTIL, "until"): cv.datetime,
            vol.Optional(ATTR_LOCATION_ID): cv.string,
        }
    ),
    lambda v: v if (ATTR_ZONE_ID in v or ATTR_ZONE_IDS in v) else _raise(
        "set_zone_temperature_until requires zone_id or zone_ids"
    ),
)


def _raise(msg: str):
    raise vol.Invalid(msg)

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
_APPLY_DAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ZONE_IDS): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1)),
        vol.Required(ATTR_DAYS_OF_WEEK): vol.All(
            cv.ensure_list, [vol.In(DAYS_OF_WEEK)], vol.Length(min=1)
        ),
        vol.Required(ATTR_SWITCHPOINTS): vol.All(
            cv.ensure_list, [_SWITCHPOINT_SCHEMA], vol.Length(min=1)
        ),
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_AWAY_UNTIL_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_LOCATION_ID): cv.string,
        vol.Required(ATTR_UNTIL): cv.datetime,
    }
)
_BOOST_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_ZONE_ID, "zone"): cv.string,
        vol.Exclusive(ATTR_ZONE_IDS, "zone"): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(ATTR_TEMPERATURE, default=21.0): vol.All(
            vol.Coerce(float), vol.Range(min=5, max=35)
        ),
        vol.Optional(ATTR_DURATION, default={"hours": 1}): cv.time_period,
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_PRESET_NAME_SCHEMA = vol.Schema(
    {vol.Required(ATTR_NAME): cv.string}
)
_PRESET_SAVE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(ATTR_ZONE_IDS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_PRESET_APPLY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(ATTR_ZONE_IDS): vol.All(cv.ensure_list, [cv.string]),
    }
)
_EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ZONE_IDS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCHEDULES): vol.All(cv.ensure_list, [dict]),
    }
)
_DHW_BOOST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DHW_ID): cv.string,
        vol.Exclusive(ATTR_DURATION, "until"): cv.time_period,
        vol.Exclusive(ATTR_UNTIL, "until"): cv.datetime,
        vol.Optional(ATTR_STATE, default="On"): vol.In(["On", "Off"]),
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)
_DHW_CLEAR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DHW_ID): cv.string,
        vol.Optional(ATTR_LOCATION_ID): cv.string,
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        return

    presets = PresetStore(hass)

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
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_DAY_SCHEDULE,
        _make_apply_day_schedule(hass),
        schema=_APPLY_DAY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_AWAY_UNTIL,
        _make_away_until(hass),
        schema=_AWAY_UNTIL_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BOOST,
        _make_boost(hass),
        schema=_BOOST_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_PRESET,
        _make_save_preset(hass, presets),
        schema=_PRESET_SAVE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_PRESET,
        _make_apply_preset(hass, presets),
        schema=_PRESET_APPLY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_PRESETS,
        _make_list_presets(presets),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_PRESET,
        _make_delete_preset(presets),
        schema=_PRESET_NAME_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_SCHEDULES,
        _make_export_schedules(hass),
        schema=_EXPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_SCHEDULES,
        _make_import_schedules(hass),
        schema=_IMPORT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DHW_BOOST,
        _make_dhw_boost(hass),
        schema=_DHW_BOOST_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DHW_CLEAR_OVERRIDE,
        _make_dhw_clear_override(hass),
        schema=_DHW_CLEAR_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for svc in (
        SERVICE_GET_SCHEDULE,
        SERVICE_SET_SCHEDULE,
        SERVICE_REFRESH_SCHEDULES,
        SERVICE_COPY_SCHEDULE,
        SERVICE_APPLY_DAY_SCHEDULE,
        SERVICE_SET_ZONE_OVERRIDE,
        SERVICE_CLEAR_ZONE_OVERRIDE,
        SERVICE_SET_SYSTEM_MODE,
        SERVICE_AWAY_UNTIL,
        SERVICE_BOOST,
        SERVICE_SAVE_PRESET,
        SERVICE_APPLY_PRESET,
        SERVICE_LIST_PRESETS,
        SERVICE_DELETE_PRESET,
        SERVICE_EXPORT_SCHEDULES,
        SERVICE_IMPORT_SCHEDULES,
        SERVICE_DHW_BOOST,
        SERVICE_DHW_CLEAR_OVERRIDE,
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
        zone_ids: list[str]
        if ATTR_ZONE_IDS in call.data:
            zone_ids = list(call.data[ATTR_ZONE_IDS])
        else:
            zone_ids = [call.data[ATTR_ZONE_ID]]

        loc_id = call.data.get(ATTR_LOCATION_ID)
        targets: list[tuple[EvohomeDataUpdateCoordinator, ZoneData]] = [
            _resolve_zone(hass, zid, loc_id) for zid in zone_ids
        ]

        temp = float(call.data[ATTR_TEMPERATURE])
        until_dt: datetime | None = None
        if ATTR_UNTIL in call.data:
            until_dt = call.data[ATTR_UNTIL]
        elif ATTR_DURATION in call.data:
            until_dt = datetime.now(timezone.utc) + call.data[ATTR_DURATION]

        for coord, zone in targets:
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

        for coord in {c for c, _ in targets}:
            await coord.async_request_refresh()

    return _override


def _normalize_switchpoint(sp: dict[str, Any]) -> dict[str, Any]:
    tod_raw = sp.get("timeOfDay") or sp.get(ATTR_TIME_OF_DAY)
    parts = str(tod_raw).split(":")
    if len(parts) < 2 or len(parts) > 3:
        raise HomeAssistantError(f"Invalid timeOfDay: {tod_raw!r}")
    h, m = parts[0], parts[1]
    s = parts[2] if len(parts) == 3 else "00"
    tod = f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
    setpoint = (
        sp["heatSetpoint"]
        if "heatSetpoint" in sp
        else sp[ATTR_HEAT_SETPOINT]
    )
    return {"timeOfDay": tod, "heatSetpoint": float(setpoint)}


def _make_apply_day_schedule(hass: HomeAssistant):
    """Apply one set of switchpoints to several zones x several days at once.

    For each target zone we GET the current schedule, replace the switchpoints
    on each requested day with the supplied ones (sorted by time), and PUT
    the result back. Days not listed are left untouched.
    """

    async def _apply(call: ServiceCall) -> None:
        loc_id = call.data.get(ATTR_LOCATION_ID)
        zone_ids = list(call.data[ATTR_ZONE_IDS])
        days = list(call.data[ATTR_DAYS_OF_WEEK])
        new_switchpoints = sorted(
            (_normalize_switchpoint(sp) for sp in call.data[ATTR_SWITCHPOINTS]),
            key=lambda sp: sp["timeOfDay"],
        )

        targets = [_resolve_zone(hass, zid, loc_id) for zid in zone_ids]
        for coord, zone in targets:
            current = await coord.client.async_get_zone_schedule(zone.zone_id)
            updated_days = []
            for d in current.get("dailySchedules", []):
                if d.get("dayOfWeek") in days:
                    updated_days.append(
                        {
                            "dayOfWeek": d["dayOfWeek"],
                            "switchpoints": [dict(sp) for sp in new_switchpoints],
                        }
                    )
                else:
                    updated_days.append(d)
            await coord.client.async_set_zone_schedule(
                zone.zone_id, {"dailySchedules": updated_days}
            )

        for coord in {c for c, _ in targets}:
            await coord.async_request_refresh()

    return _apply


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


# ---- sugar services -------------------------------------------------


def _make_away_until(hass: HomeAssistant):
    async def _away(call: ServiceCall) -> None:
        until_dt = call.data[ATTR_UNTIL]
        loc_id = call.data.get(ATTR_LOCATION_ID)
        targets: list[tuple[EvohomeDataUpdateCoordinator, LocationData]]
        if loc_id is not None:
            targets = [_resolve_location(hass, loc_id)]
        else:
            targets = [
                (coord, loc)
                for coord in _all_coordinators(hass)
                for loc in coord.data.locations.values()
            ]
        for coord, loc in targets:
            if loc.primary_tcs_id is None:
                continue
            await coord.client.async_set_system_mode(
                loc.primary_tcs_id,
                system_mode="Away",
                permanent=False,
                time_until=_format_until(until_dt),
            )
        for coord in {c for c, _ in targets}:
            await coord.async_request_refresh()

    return _away


def _make_boost(hass: HomeAssistant):
    async def _boost(call: ServiceCall) -> None:
        zone_ids: list[str]
        if ATTR_ZONE_IDS in call.data:
            zone_ids = list(call.data[ATTR_ZONE_IDS])
        elif ATTR_ZONE_ID in call.data:
            zone_ids = [call.data[ATTR_ZONE_ID]]
        else:
            raise HomeAssistantError("boost requires zone_id or zone_ids")
        loc_id = call.data.get(ATTR_LOCATION_ID)
        targets = [_resolve_zone(hass, zid, loc_id) for zid in zone_ids]
        temp = float(call.data[ATTR_TEMPERATURE])
        until_dt = datetime.now(timezone.utc) + call.data[ATTR_DURATION]
        for coord, zone in targets:
            await coord.client.async_set_zone_heat_setpoint(
                zone.zone_id,
                setpoint_mode="TemporaryOverride",
                heat_setpoint_value=temp,
                time_until=_format_until(until_dt),
            )
        for coord in {c for c, _ in targets}:
            await coord.async_request_refresh()

    return _boost


# ---- DHW services ---------------------------------------------------


def _resolve_dhw(
    hass: HomeAssistant, dhw_id: str, location_id: str | None
) -> tuple[EvohomeDataUpdateCoordinator, DhwData]:
    matches: list[tuple[EvohomeDataUpdateCoordinator, DhwData]] = []
    for coord in _all_coordinators(hass):
        for loc in coord.data.locations.values():
            if location_id and loc.location_id != location_id:
                continue
            if loc.dhw is not None and loc.dhw.dhw_id == dhw_id:
                matches.append((coord, loc.dhw))
    if not matches:
        raise HomeAssistantError(f"Unknown evohome dhw_id: {dhw_id}")
    if len(matches) > 1:
        raise HomeAssistantError(
            f"Ambiguous dhw_id {dhw_id} - specify location_id"
        )
    return matches[0]


class _DhwScheduledState(NamedTuple):
    state: str       # "On" | "Off"
    next_change: datetime  # tz-aware datetime of the next scheduled switchpoint


def _dhw_schedule_state_now(
    coord: EvohomeDataUpdateCoordinator,
    dhw: DhwData,
) -> _DhwScheduledState | None:
    """Return the current scheduled DHW state and when it next changes.

    Reads the cached weekly programme, locates the most recent switchpoint
    at-or-before now (in the location's timezone), and returns both that
    state and the absolute datetime of the following switchpoint.
    Returns None when the schedule is absent or entirely empty.
    """
    loc = coord.data.locations.get(dhw.location_id)
    tz_id = loc.time_zone if loc is not None else None
    try:
        tz: Any = ZoneInfo(tz_id) if tz_id else timezone.utc
    except (ZoneInfoNotFoundError, KeyError):
        tz = timezone.utc

    now = datetime.now(tz)

    schedule = dhw.schedule
    if not schedule:
        return None

    daily_schedules = schedule.get("dailySchedules", [])
    if not daily_schedules:
        return None

    # Build day_name -> sorted [(HH:MM:SS, state), ...]; skip days with no
    # valid switchpoints so the fallback logic handles sparse schedules.
    day_sps: dict[str, list[tuple[str, str]]] = {}
    for day_sched in daily_schedules:
        dow = day_sched.get("dayOfWeek")
        if not dow:
            continue
        sps: list[tuple[str, str]] = []
        for sp in day_sched.get("switchpoints", []):
            tod = sp.get("timeOfDay", "")
            sp_state = sp.get("state", "")
            if not tod or not sp_state:
                continue
            parts = tod.split(":")
            if len(parts) == 2:
                tod = f"{tod}:00"
            elif len(parts) != 3:
                continue
            sps.append((tod, sp_state))
        if sps:
            day_sps[dow] = sorted(sps)

    if not day_sps:
        return None

    today_idx = now.weekday()  # 0 = Monday, matches DAYS_OF_WEEK index
    today_name = DAYS_OF_WEEK[today_idx]
    now_str = now.strftime("%H:%M:%S")

    # --- current state: last switchpoint at-or-before now today, else the
    #     last switchpoint of the most recent prior day with any entries. ---
    today_sps = day_sps.get(today_name, [])
    before_now = [(t, s) for t, s in today_sps if t <= now_str]

    current_state: str | None = None
    if before_now:
        _, current_state = before_now[-1]
    else:
        for i in range(1, 8):
            prev_name = DAYS_OF_WEEK[(today_idx - i) % 7]
            if day_sps.get(prev_name):
                _, current_state = day_sps[prev_name][-1]
                break

    if current_state is None:
        return None

    # --- next change: first switchpoint strictly after now today, or the
    #     first one of the next day (wrapping up to 7 days forward). ---
    after_now = [(t, s) for t, s in today_sps if t > now_str]

    next_change: datetime | None = None
    if after_now:
        p = after_now[0][0].split(":")
        next_change = datetime(
            now.year, now.month, now.day,
            int(p[0]), int(p[1]), int(p[2]),
            tzinfo=tz,
        )
    else:
        for i in range(1, 8):
            next_name = DAYS_OF_WEEK[(today_idx + i) % 7]
            next_sps = day_sps.get(next_name, [])
            if next_sps:
                p = next_sps[0][0].split(":")
                next_date = now.date() + timedelta(days=i)
                next_change = datetime(
                    next_date.year, next_date.month, next_date.day,
                    int(p[0]), int(p[1]), int(p[2]),
                    tzinfo=tz,
                )
                break

    if next_change is None:
        return None

    return _DhwScheduledState(state=current_state, next_change=next_change)


def _make_dhw_boost(hass: HomeAssistant):
    async def _dhw_boost(call: ServiceCall) -> None:
        coord, dhw = _resolve_dhw(
            hass, call.data[ATTR_DHW_ID], call.data.get(ATTR_LOCATION_ID)
        )
        state = call.data.get(ATTR_STATE, "On")
        if ATTR_UNTIL in call.data:
            until_dt = call.data[ATTR_UNTIL]
        elif ATTR_DURATION in call.data:
            until_dt = datetime.now(timezone.utc) + call.data[ATTR_DURATION]
        else:
            until_dt = datetime.now(timezone.utc) + timedelta(hours=1)
        await coord.client.async_set_dhw_state(
            dhw.dhw_id,
            mode="TemporaryOverride",
            state=state,
            time_until=_format_until(until_dt),
        )
        await coord.async_request_refresh()

    return _dhw_boost


def _make_dhw_clear_override(hass: HomeAssistant):
    async def _dhw_clear(call: ServiceCall) -> None:
        coord, dhw = _resolve_dhw(
            hass, call.data[ATTR_DHW_ID], call.data.get(ATTR_LOCATION_ID)
        )
        scheduled = _dhw_schedule_state_now(coord, dhw)
        if scheduled is not None and scheduled.state == "Off":
            await coord.client.async_set_dhw_state(
                dhw.dhw_id,
                mode="TemporaryOverride",
                state="Off",
                time_until=_format_until(scheduled.next_change),
            )
        else:
            await coord.client.async_set_dhw_state(
                dhw.dhw_id,
                mode="FollowSchedule",
            )
        await coord.async_request_refresh()

    return _dhw_clear


# ---- preset services ------------------------------------------------


def _zones_in_scope(
    hass: HomeAssistant,
    zone_ids: list[str] | None,
    location_id: str | None,
) -> list[tuple[EvohomeDataUpdateCoordinator, ZoneData]]:
    """Resolve a list of zones, defaulting to every zone (optionally filtered to a location)."""
    if zone_ids:
        return [_resolve_zone(hass, zid, location_id) for zid in zone_ids]
    out: list[tuple[EvohomeDataUpdateCoordinator, ZoneData]] = []
    for coord in _all_coordinators(hass):
        for loc in coord.data.locations.values():
            if location_id and loc.location_id != location_id:
                continue
            for zone in loc.zones.values():
                out.append((coord, zone))
    return out


def _make_save_preset(hass: HomeAssistant, presets: PresetStore):
    async def _save(call: ServiceCall) -> None:
        targets = _zones_in_scope(
            hass,
            call.data.get(ATTR_ZONE_IDS),
            call.data.get(ATTR_LOCATION_ID),
        )
        if not targets:
            raise HomeAssistantError("No zones matched the requested scope")
        # Always use a fresh GET so the preset captures the current truth
        # rather than whatever the coordinator last polled.
        snapshots: list[dict[str, Any]] = []
        for coord, zone in targets:
            sched = await coord.client.async_get_zone_schedule(zone.zone_id)
            snapshots.append(
                {
                    "zone_id": zone.zone_id,
                    "zone_name": zone.name,
                    "location_id": zone.location_id,
                    "schedule": sched,
                }
            )
        await presets.save(call.data[ATTR_NAME], snapshots)

    return _save


def _make_apply_preset(hass: HomeAssistant, presets: PresetStore):
    async def _apply(call: ServiceCall) -> None:
        name = call.data[ATTR_NAME]
        preset = await presets.get(name)
        if preset is None:
            raise HomeAssistantError(f"Unknown preset {name!r}")
        wanted = call.data.get(ATTR_ZONE_IDS)
        coords: set[EvohomeDataUpdateCoordinator] = set()
        for entry in preset["zones"]:
            zid = entry["zone_id"]
            if wanted and zid not in wanted:
                continue
            try:
                coord, zone = _resolve_zone(hass, zid, entry.get("location_id"))
            except HomeAssistantError:
                _LOGGER.warning(
                    "Preset %s references unknown zone %s; skipping", name, zid
                )
                continue
            await coord.client.async_set_zone_schedule(zone.zone_id, entry["schedule"])
            coords.add(coord)
        for coord in coords:
            await coord.async_request_refresh()

    return _apply


def _make_list_presets(presets: PresetStore):
    async def _list(_call: ServiceCall) -> ServiceResponse:
        names = await presets.list_names()
        out = []
        for name in names:
            p = await presets.get(name)
            if p is None:
                continue
            out.append(
                {
                    "name": name,
                    "created": p.get("created"),
                    "zone_count": len(p.get("zones", [])),
                }
            )
        return {"presets": out}

    return _list


def _make_delete_preset(presets: PresetStore):
    async def _delete(call: ServiceCall) -> None:
        name = call.data[ATTR_NAME]
        if not await presets.delete(name):
            raise HomeAssistantError(f"Unknown preset {name!r}")

    return _delete


# ---- export / import ------------------------------------------------


def _make_export_schedules(hass: HomeAssistant):
    async def _export(call: ServiceCall) -> ServiceResponse:
        targets = _zones_in_scope(
            hass,
            call.data.get(ATTR_ZONE_IDS),
            call.data.get(ATTR_LOCATION_ID),
        )
        if not targets:
            raise HomeAssistantError("No zones matched the requested scope")
        schedules: list[dict[str, Any]] = []
        for coord, zone in targets:
            sched = await coord.client.async_get_zone_schedule(zone.zone_id)
            schedules.append(
                {
                    "zone_id": zone.zone_id,
                    "zone_name": zone.name,
                    "location_id": zone.location_id,
                    "location_name": zone.location_name,
                    "schedule": sched,
                }
            )
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "schedules": schedules,
        }

    return _export


def _make_import_schedules(hass: HomeAssistant):
    async def _import(call: ServiceCall) -> None:
        coords: set[EvohomeDataUpdateCoordinator] = set()
        for entry in call.data[ATTR_SCHEDULES]:
            zid = str(entry.get("zone_id", "")).strip()
            sched = entry.get("schedule")
            if not zid or sched is None:
                raise HomeAssistantError(
                    "Each item in `schedules` requires zone_id and schedule"
                )
            try:
                coord, zone = _resolve_zone(hass, zid, entry.get("location_id"))
            except HomeAssistantError:
                _LOGGER.warning("Import skipping unknown zone %s", zid)
                continue
            await coord.client.async_set_zone_schedule(
                zone.zone_id, _normalize_schedule(sched)
            )
            coords.add(coord)
        for coord in coords:
            await coord.async_request_refresh()

    return _import
