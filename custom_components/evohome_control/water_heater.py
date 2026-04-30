"""Water heater platform: surfaces DHW where the system has it."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_OFF,
    STATE_ON,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DhwData, EvohomeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

_OP_AUTO = "auto"
_OP_ON = STATE_ON
_OP_OFF = STATE_OFF


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
            if loc.dhw is None or loc.dhw.dhw_id in known:
                continue
            known.add(loc.dhw.dhw_id)
            new.append(EvohomeDhwEntity(coordinator, loc.dhw.dhw_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class EvohomeDhwEntity(
    CoordinatorEntity[EvohomeDataUpdateCoordinator], WaterHeaterEntity
):
    """Resideo Evohome domestic hot water control."""

    _attr_supported_features = WaterHeaterEntityFeature.OPERATION_MODE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = [_OP_AUTO, _OP_ON, _OP_OFF]
    _attr_has_entity_name = True
    _attr_name = "Hot water"

    def __init__(
        self, coordinator: EvohomeDataUpdateCoordinator, dhw_id: str
    ) -> None:
        super().__init__(coordinator)
        self._dhw_id = dhw_id
        self._attr_unique_id = f"{DOMAIN}_dhw_{dhw_id}"

    @property
    def _dhw(self) -> DhwData | None:
        for loc in self.coordinator.data.locations.values():
            if loc.dhw and loc.dhw.dhw_id == self._dhw_id:
                return loc.dhw
        return None

    @property
    def available(self) -> bool:
        dhw = self._dhw
        return super().available and dhw is not None and dhw.is_available

    @property
    def device_info(self) -> DeviceInfo | None:
        dhw = self._dhw
        if not dhw:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, f"location_{dhw.location_id}")},
            name=self.coordinator.data.locations[dhw.location_id].name,
            manufacturer=MANUFACTURER,
            model="Evohome Location",
        )

    @property
    def current_temperature(self) -> float | None:
        return self._dhw.temperature if self._dhw else None

    @property
    def current_operation(self) -> str | None:
        dhw = self._dhw
        if not dhw or dhw.mode is None:
            return None
        if dhw.mode == "FollowSchedule":
            return _OP_AUTO
        return _OP_ON if dhw.state == "On" else _OP_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dhw = self._dhw
        if not dhw:
            return {}
        return {
            "location_id": dhw.location_id,
            "tcs_id": dhw.tcs_id,
            "dhw_id": dhw.dhw_id,
            "mode": dhw.mode,
            "state": dhw.state,
            "schedule": dhw.schedule,
        }

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode == _OP_AUTO:
            await self.coordinator.client.async_set_dhw_state(
                self._dhw_id, mode="FollowSchedule"
            )
        elif operation_mode == _OP_ON:
            await self.coordinator.client.async_set_dhw_state(
                self._dhw_id, mode="PermanentOverride", state="On"
            )
        elif operation_mode == _OP_OFF:
            await self.coordinator.client.async_set_dhw_state(
                self._dhw_id, mode="PermanentOverride", state="Off"
            )
        else:
            _LOGGER.warning("Unknown DHW operation mode: %s", operation_mode)
            return
        await self.coordinator.async_request_refresh()
