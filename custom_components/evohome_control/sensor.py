"""Sensor platform: surfaces the next scheduled switchpoint per zone."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DAYS_OF_WEEK, DOMAIN
from .coordinator import EvohomeDataUpdateCoordinator, ZoneData

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
        for zone in coordinator.data.iter_zones():
            if zone.zone_id in known:
                continue
            known.add(zone.zone_id)
            new.append(NextSwitchpointSensor(coordinator, zone.zone_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class NextSwitchpointSensor(
    CoordinatorEntity[EvohomeDataUpdateCoordinator], SensorEntity
):
    """The next scheduled heat setpoint for a zone (and when it kicks in)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EvohomeDataUpdateCoordinator, zone_id: str
    ) -> None:
        super().__init__(coordinator)
        self._zone_id = zone_id
        self._attr_unique_id = f"{DOMAIN}_zone_{zone_id}_next_switchpoint"
        self._attr_translation_key = "next_switchpoint"

    @property
    def _zone(self) -> ZoneData | None:
        for loc in self.coordinator.data.locations.values():
            if self._zone_id in loc.zones:
                return loc.zones[self._zone_id]
        return None

    @property
    def name(self) -> str | None:
        zone = self._zone
        return f"{zone.name} next switchpoint" if zone else None

    @property
    def native_value(self) -> float | None:
        sp = _next_switchpoint(self._zone)
        return sp[1] if sp else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sp = _next_switchpoint(self._zone)
        if not sp:
            return {}
        return {"changes_at": sp[0].isoformat(), "heat_setpoint": sp[1]}


def _next_switchpoint(zone: ZoneData | None) -> tuple[datetime, float] | None:
    if not zone or not zone.schedule:
        return None
    days = {d["dayOfWeek"]: d["switchpoints"] for d in zone.schedule.get("dailySchedules", [])}
    if not days:
        return None

    now = datetime.now().astimezone()
    for offset in range(0, 8):
        candidate_date = (now + timedelta(days=offset)).date()
        dow_name = DAYS_OF_WEEK[candidate_date.weekday()]
        for sp in days.get(dow_name, []):
            sp_time = time.fromisoformat(sp["timeOfDay"])
            sp_dt = datetime.combine(
                candidate_date, sp_time, tzinfo=now.tzinfo
            )
            if sp_dt > now:
                return sp_dt, float(sp["heatSetpoint"])
    return None
