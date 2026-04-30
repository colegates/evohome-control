"""Climate platform: one entity per heating zone, across every location."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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

from .const import DOMAIN, MANUFACTURER
from .coordinator import EvohomeDataUpdateCoordinator, ZoneData

_LOGGER = logging.getLogger(__name__)

# Resideo zone setpoint modes -> HA preset names.
_MODE_FOLLOW = "FollowSchedule"
_MODE_PERMANENT = "PermanentOverride"
_MODE_TEMPORARY = "TemporaryOverride"


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
        return {
            "location_id": zone.location_id,
            "location_name": zone.location_name,
            "tcs_id": zone.tcs_id,
            "zone_id": zone.zone_id,
            "zone_type": zone.zone_type,
            "schedule": zone.schedule,
        }

    # ---- commands -------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        target = kwargs.get(ATTR_TEMPERATURE)
        if target is None:
            return
        await self.coordinator.client.async_set_zone_heat_setpoint(
            self._zone_id,
            setpoint_mode=_MODE_PERMANENT,
            heat_setpoint_value=float(target),
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.AUTO:
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id, setpoint_mode=_MODE_FOLLOW
            )
        elif hvac_mode == HVACMode.OFF:
            zone = self._zone
            if not zone:
                return
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id,
                setpoint_mode=_MODE_PERMANENT,
                heat_setpoint_value=zone.min_setpoint,
            )
        elif hvac_mode == HVACMode.HEAT:
            zone = self._zone
            if not zone:
                return
            target = zone.target_setpoint or 21.0
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id,
                setpoint_mode=_MODE_PERMANENT,
                heat_setpoint_value=target,
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
        elif preset_mode == "permanent_override":
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id,
                setpoint_mode=_MODE_PERMANENT,
                heat_setpoint_value=zone.target_setpoint or 21.0,
            )
        elif preset_mode == "temporary_override":
            # Default to one hour from now if the caller doesn't supply
            # a custom time_until via the override service.
            now = datetime.now(timezone.utc).replace(microsecond=0)
            until = now.replace(minute=0, second=0) + _ONE_HOUR
            await self.coordinator.client.async_set_zone_heat_setpoint(
                self._zone_id,
                setpoint_mode=_MODE_TEMPORARY,
                heat_setpoint_value=zone.target_setpoint or 21.0,
                time_until=until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        await self.coordinator.async_request_refresh()


from datetime import timedelta as _td  # local import to keep top tidy

_ONE_HOUR = _td(hours=1)
