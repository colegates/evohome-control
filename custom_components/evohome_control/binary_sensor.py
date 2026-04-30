"""Binary sensors: low TRV battery and active fault per zone.

The Resideo TCC v2 API surfaces zone-level problems (including TRV/actuator
low battery) via the ``activeFaults`` array on each zone in the location
status response. Fault entries look like::

    {"faultType": "TempZoneActuatorLowBattery", "since": "2026-04-15T08:23:00"}

We expose two binary sensors per zone:
  * ``low_battery`` (device_class=battery) - True when any active fault
    contains "battery" in its type
  * ``problem`` (device_class=problem) - True when the zone has any active
    fault at all
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EvohomeDataUpdateCoordinator, ZoneData

_LOGGER = logging.getLogger(__name__)


def _is_low_battery_fault(fault: dict[str, Any]) -> bool:
    return "battery" in str(fault.get("faultType", "")).lower()


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
            new.append(LowBatteryBinarySensor(coordinator, zone.zone_id))
            new.append(FaultBinarySensor(coordinator, zone.zone_id))
            new.append(OverriddenBinarySensor(coordinator, zone.zone_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class _ZoneBinarySensorBase(
    CoordinatorEntity[EvohomeDataUpdateCoordinator], BinarySensorEntity
):
    """Common base for zone-level binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EvohomeDataUpdateCoordinator, zone_id: str
    ) -> None:
        super().__init__(coordinator)
        self._zone_id = zone_id

    @property
    def _zone(self) -> ZoneData | None:
        for loc in self.coordinator.data.locations.values():
            if self._zone_id in loc.zones:
                return loc.zones[self._zone_id]
        return None

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


class LowBatteryBinarySensor(_ZoneBinarySensorBase):
    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_translation_key = "low_battery"

    def __init__(
        self, coordinator: EvohomeDataUpdateCoordinator, zone_id: str
    ) -> None:
        super().__init__(coordinator, zone_id)
        self._attr_unique_id = f"{DOMAIN}_zone_{zone_id}_low_battery"

    @property
    def name(self) -> str | None:
        zone = self._zone
        return f"{zone.name} battery" if zone else None

    @property
    def is_on(self) -> bool | None:
        zone = self._zone
        if not zone:
            return None
        return any(_is_low_battery_fault(f) for f in zone.active_faults)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self._zone
        if not zone:
            return {}
        battery_faults = [
            f for f in zone.active_faults if _is_low_battery_fault(f)
        ]
        if not battery_faults:
            return {"zone_id": zone.zone_id}
        return {
            "zone_id": zone.zone_id,
            "fault_type": battery_faults[0].get("faultType"),
            "since": battery_faults[0].get("since"),
        }


class OverriddenBinarySensor(_ZoneBinarySensorBase):
    """On whenever the live setpoint deviates from the schedule."""

    _attr_translation_key = "overridden"

    def __init__(
        self, coordinator: EvohomeDataUpdateCoordinator, zone_id: str
    ) -> None:
        super().__init__(coordinator, zone_id)
        self._attr_unique_id = f"{DOMAIN}_zone_{zone_id}_overridden"

    @property
    def name(self) -> str | None:
        zone = self._zone
        return f"{zone.name} overridden" if zone else None

    @property
    def is_on(self) -> bool | None:
        zone = self._zone
        if not zone or zone.setpoint_mode is None:
            return None
        return zone.setpoint_mode != "FollowSchedule"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self._zone
        if not zone:
            return {}
        return {
            "zone_id": zone.zone_id,
            "setpoint_mode": zone.setpoint_mode,
            "target_setpoint": zone.target_setpoint,
        }


class FaultBinarySensor(_ZoneBinarySensorBase):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "fault"

    def __init__(
        self, coordinator: EvohomeDataUpdateCoordinator, zone_id: str
    ) -> None:
        super().__init__(coordinator, zone_id)
        self._attr_unique_id = f"{DOMAIN}_zone_{zone_id}_fault"

    @property
    def name(self) -> str | None:
        zone = self._zone
        return f"{zone.name} fault" if zone else None

    @property
    def is_on(self) -> bool | None:
        zone = self._zone
        return bool(zone.active_faults) if zone else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self._zone
        if not zone or not zone.active_faults:
            return {}
        return {
            "zone_id": zone.zone_id,
            "active_faults": zone.active_faults,
        }
