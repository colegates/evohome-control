"""Button platform: per-location quick actions.

Adds three convenience buttons to every Evohome location:

  * ``resume_schedule``  - sets the system back to ``Auto``
  * ``heating_off``      - sets the system to ``HeatingOff``
  * ``away``             - sets the system to ``Away`` (permanent)

These are sugar over the ``set_system_mode`` service for the most common
operations.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EvohomeDataUpdateCoordinator, LocationData

_LOGGER = logging.getLogger(__name__)


_BUTTONS = (
    ("resume_schedule", "Resume schedule", "Auto"),
    ("heating_off", "Heating off", "HeatingOff"),
    ("away", "Away", "Away"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EvohomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        new = []
        for loc in coordinator.data.locations.values():
            if loc.primary_tcs_id is None:
                continue
            for key, label, mode in _BUTTONS:
                if (loc.location_id, key) in known:
                    continue
                if loc.allowed_system_modes and mode not in loc.allowed_system_modes:
                    continue
                known.add((loc.location_id, key))
                new.append(
                    ModeButton(coordinator, loc.location_id, key, label, mode)
                )
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class ModeButton(
    CoordinatorEntity[EvohomeDataUpdateCoordinator], ButtonEntity
):
    """A button that sets a specific system mode on its location."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EvohomeDataUpdateCoordinator,
        location_id: str,
        key: str,
        label: str,
        mode: str,
    ) -> None:
        super().__init__(coordinator)
        self._location_id = location_id
        self._mode = mode
        self._attr_name = label
        self._attr_unique_id = f"{DOMAIN}_location_{location_id}_button_{key}"
        self._attr_translation_key = key

    @property
    def _location(self) -> LocationData | None:
        return self.coordinator.data.locations.get(self._location_id)

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

    async def async_press(self) -> None:
        loc = self._location
        if not loc or loc.primary_tcs_id is None:
            return
        await self.coordinator.client.async_set_system_mode(
            loc.primary_tcs_id, system_mode=self._mode, permanent=True
        )
        await self.coordinator.async_request_refresh()
