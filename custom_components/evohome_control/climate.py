"""Climate platform: one entity per heating zone, across every location."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import audit, timing
from .const import DOMAIN, MANUFACTURER
from .coordinator import EvohomeDataUpdateCoordinator, ZoneData

_LOGGER = logging.getLogger(__name__)

# Resideo zone setpoint modes -> HA preset names.
_MODE_FOLLOW = "FollowSchedule"
_MODE_PERMANENT = "PermanentOverride"
_MODE_TEMPORARY = "TemporaryOverride"

_ONE_HOUR = timedelta(hours=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EvohomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = []
        for zone in coordinator.data.iter_zones():
            if zone.zone_id in known:
                continue
            known.add(zone.zone_id)
            new.append(EvohomeZoneClimate(coordinator, zone.zone_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class EvohomeZoneClimate(CoordinatorEntity[EvohomeDataUpdateCoordinator], ClimateEntity):
    """A single heating zone (radiator/UFH) on a TCS."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = ["follow_schedule", "permanent_override", "temporary_override"]
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EvohomeDataUpdateCoordinator, zone_id: str
    ) -> None:
        super().__init__(coordinator)
        self._zone_id = zone_id
        self._attr_unique_id = f"{DOMAIN}_zone_{zone_id}"

    # ---- helpers --------------------------------------------------------

    @property
    def _zone(self) -> ZoneData | None:
        for loc in self.coordinator.data.locations.values():
            if self._zone_id in loc.zones:
                return loc.zones[self._zone_id]
        return None

    # ---- entity surface -------------------------------------------------

    @property
    def available(self) -> bool:
        return super().available and self._zone is not None and self._zone.is_available

    @property
    def name(self) -> str | None:
        zone = self._zone
        return zone.name if zone else None

    @property
    def device_info(self) -> DeviceInfo | None:
        zone = self._zone
        if not zone:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, f"location_{zone.location_id}")},
            name=zone.location_name,
            manufacturer=MANUFACTURER,
            model="Evohome Location",
        )

    @property
    def current_temperature(self) -> float | None:
        return self._zone.temperature if self._zone else None

    @property
    def target_temperature(self) -> float | None:
        return self._zone.target_setpoint if self._zone else None

    @property
    def min_temp(self) -> float:
        return self._zone.min_setpoint if self._zone else 5.0

    @property
    def max_temp(self) -> float:
        return self._zone.max_setpoint if self._zone else 35.0

    @property
    def target_temperature_step(self) -> float:
        return self._zone.setpoint_resolution if self._zone else 0.5

    @property
    def hvac_mode(self) -> HVACMode:
        zone = self._zone
        if not zone or zone.target_setpoint is None:
            return HVACMode.AUTO
        if zone.target_setpoint <= zone.min_setpoint:
            return HVACMode.OFF
        return HVACMode.AUTO if zone.setpoint_mode == _MODE_FOLLOW else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        zone = self._zone
        if not zone or zone.temperature is None or zone.target_setpoint is None:
            return None
        if zone.target_setpoint <= zone.min_setpoint:
            return HVACAction.OFF
        return (
            HVACAction.HEATING
            if zone.temperature < zone.target_setpoint
            else HVACAction.IDLE
        )

    @property
    def preset_mode(self) -> str | None:
        zone = self._zone
        if not zone or zone.setpoint_mode is None:
            return None
        return {
            _MODE_FOLLOW: "follow_schedule",
            _MODE_PERMANENT: "permanent_override",
            _MODE_TEMPORARY: "temporary_override",
        }.get(zone.setpoint_mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self._zone
        if not zone:
            return {}
        last = self.coordinator.data.last_updated
        return {
            "location_id": zone.location_id,
            "location_name": zone.location_name,
            "tcs_id": zone.tcs_id,
            "zone_id": zone.zone_id,
            "zone_type": zone.zone_type,
            "schedule": zone.schedule,
            "is_overridden": (
                zone.setpoint_mode is not None
                and zone.setpoint_mode != "FollowSchedule"
            ),
            "last_synced": last.isoformat() if last else None,
        }

    # ---- commands -------------------------------------------------------

    def _next_switchpoint_until(self, zone: ZoneData) -> datetime:
        """Return the datetime to use for a TemporaryOverride.

        Prefers the zone's next scheduled switchpoint (so a plain temperature
        change behaves like one made on the Evohome controller itself: it
        ends at the next schedule change). Falls back to one hour from now
        when the schedule is unavailable or has no switchpoints.
        """
        loc = self.coordinator.data.locations.get(zone.location_id)
        # LocationData.tz is pre-resolved by the coordinator off the event
        # loop; .time_zone is kept as a fallback for unit-test paths that
        # don't populate .tz.
        tz = loc.tz if loc is not None else None
        tz_id = loc.time_zone if (loc is not None and tz is None) else None
        nxt = timing.zone_next_switchpoint(zone.schedule, tz=tz, tz_id=tz_id)
        if nxt is not None:
            return nxt
        return datetime.now(timezone.utc) + _ONE_HOUR

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Change the heat setpoint.

        A plain ``climate.set_temperature`` call (from the HA thermostat card,
        voice assistants, Assist, MCP clients, automations) becomes a
        TemporaryOverride that ends at the next scheduled switchpoint - the
        same behaviour the Evohome controller applies to a local change.
        Use the ``set_zone_temperature_until`` service or the
        ``permanent_override`` preset to pin a value indefinitely.
        """
        target = kwargs.get(ATTR_TEMPERATURE)
        if target is None:
            return
        zone = self._zone
        if not zone:
            return
        until_dt = self._next_switchpoint_until(zone)
        until_str = timing.format_until(until_dt)
        await self.coordinator.client.async_set_zone_heat_setpoint(
            self._zone_id,
            setpoint_mode=_MODE_TEMPORARY,
            heat_setpoint_value=float(target),
            time_until=until_str,
        )
        await audit.log_write(
            _LOGGER,
            self.hass,
            self._context,
            action="climate.set_temperature",
            zone_name=zone.name,
            zone_id=zone.zone_id,
            location_id=zone.location_id,
            setpoint_mode=_MODE_TEMPORARY,
            temperature=float(target),
            until=until_dt,
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        zone = self._zone
        if hvac_mode == HVACMode.AUTO:
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id, setpoint_mode=_MODE_FOLLOW
            )
            await audit.log_write(
                _LOGGER,
                self.hass,
                self._context,
                action="climate.set_hvac_mode(auto)",
                zone_name=zone.name if zone else None,
                zone_id=self._zone_id,
                location_id=zone.location_id if zone else None,
                setpoint_mode=_MODE_FOLLOW,
            )
        elif hvac_mode == HVACMode.OFF:
            if not zone:
                return
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id,
                setpoint_mode=_MODE_PERMANENT,
                heat_setpoint_value=zone.min_setpoint,
            )
            await audit.log_write(
                _LOGGER,
                self.hass,
                self._context,
                action="climate.set_hvac_mode(off)",
                zone_name=zone.name,
                zone_id=zone.zone_id,
                location_id=zone.location_id,
                setpoint_mode=_MODE_PERMANENT,
                temperature=zone.min_setpoint,
            )
        elif hvac_mode == HVACMode.HEAT:
            if not zone:
                return
            target = zone.target_setpoint or 21.0
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id,
                setpoint_mode=_MODE_PERMANENT,
                heat_setpoint_value=target,
            )
            await audit.log_write(
                _LOGGER,
                self.hass,
                self._context,
                action="climate.set_hvac_mode(heat)",
                zone_name=zone.name,
                zone_id=zone.zone_id,
                location_id=zone.location_id,
                setpoint_mode=_MODE_PERMANENT,
                temperature=target,
            )
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        zone = self._zone
        if not zone:
            return
        if preset_mode == "follow_schedule":
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id, setpoint_mode=_MODE_FOLLOW
            )
            await audit.log_write(
                _LOGGER,
                self.hass,
                self._context,
                action="climate.set_preset(follow_schedule)",
                zone_name=zone.name,
                zone_id=zone.zone_id,
                location_id=zone.location_id,
                setpoint_mode=_MODE_FOLLOW,
            )
        elif preset_mode == "permanent_override":
            temp = zone.target_setpoint or 21.0
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id,
                setpoint_mode=_MODE_PERMANENT,
                heat_setpoint_value=temp,
            )
            await audit.log_write(
                _LOGGER,
                self.hass,
                self._context,
                action="climate.set_preset(permanent_override)",
                zone_name=zone.name,
                zone_id=zone.zone_id,
                location_id=zone.location_id,
                setpoint_mode=_MODE_PERMANENT,
                temperature=temp,
            )
        elif preset_mode == "temporary_override":
            # Default to one hour from now if the caller doesn't supply
            # a custom time_until via the override service.
            now = datetime.now(timezone.utc).replace(microsecond=0)
            until = now.replace(minute=0, second=0) + _ONE_HOUR
            temp = zone.target_setpoint or 21.0
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id,
                setpoint_mode=_MODE_TEMPORARY,
                heat_setpoint_value=temp,
                time_until=timing.format_until(until),
            )
            await audit.log_write(
                _LOGGER,
                self.hass,
                self._context,
                action="climate.set_preset(temporary_override)",
                zone_name=zone.name,
                zone_id=zone.zone_id,
                location_id=zone.location_id,
                setpoint_mode=_MODE_TEMPORARY,
                temperature=temp,
                until=until,
            )
        await self.coordinator.async_request_refresh()
