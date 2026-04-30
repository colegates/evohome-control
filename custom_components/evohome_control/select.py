"""Select platform: one entity per location for the TCS system mode."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EvohomeDataUpdateCoordinator, LocationData

_LOGGER = logging.getLogger(__name__)


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
        for loc in coordinator.data.locations.values():
            if loc.location_id in known or loc.primary_tcs_id is None:
                continue
            known.add(loc.location_id)
            new.append(SystemModeSelect(coordinator, loc.location_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class SystemModeSelect(
    CoordinatorEntity[EvohomeDataUpdateCoordinator], SelectEntity
):
    """The Evohome system mode (Auto / Away / DayOff / HeatingOff / ...) for one location."""

    _attr_has_entity_name = True
    _attr_name = "System mode"

    def __init__(
        self, coordinator: EvohomeDataUpdateCoordinator, location_id: str
    ) -> None:
        super().__init__(coordinator)
        self._location_id = location_id
        self._attr_unique_id = f"{DOMAIN}_location_{location_id}_mode"

    @property
    def _location(self) -> LocationData | None:
        return self.coordinator.data.locations.get(self._location_id)

    @property
    def options(self) -> list[str]:
        loc = self._location
        return list(loc.allowed_system_modes) if loc else []

    @property
    def current_option(self) -> str | None:
        loc = self._location
        return loc.system_mode if loc else None

    @property
    def device_info(self) -> DeviceInfo | None:
        loc = self._location
        if not loc:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, f"location_{loc.location_id}")},
            name=loc.name,
            manufacturer=MANUFACTURER,
            model="Evohome Location",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = self._location
        if not loc:
            return {}
        return {
            "location_id": loc.location_id,
            "tcs_id": loc.primary_tcs_id,
            "is_permanent": loc.is_system_mode_permanent,
        }

    async def async_select_option(self, option: str) -> None:
        loc = self._location
        if not loc or not loc.primary_tcs_id:
            return
        if option not in loc.allowed_system_modes:
            _LOGGER.warning(
                "System mode %s not in allowed list %s for location %s",
                option,
                loc.allowed_system_modes,
                loc.location_id,
            )
            return
        await self.coordinator.client.async_set_system_mode(
            loc.primary_tcs_id, system_mode=option, permanent=True
        )
        await self.coordinator.async_request_refresh()
