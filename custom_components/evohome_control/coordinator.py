"""Data update coordinator for the Evohome Control integration.

The coordinator polls every location on the account and merges installation
info, status and schedule into a single per-zone view that the platforms
consume.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EvohomeApiClient, EvohomeApiError, EvohomeAuthError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class ZoneData:
    """Merged installation + status + schedule for one heating zone."""

    location_id: str
    location_name: str
    tcs_id: str
    zone_id: str
    name: str
    zone_type: str
    min_setpoint: float
    max_setpoint: float
    setpoint_resolution: float
    allowed_modes: list[str]
    temperature: float | None
    target_setpoint: float | None
    setpoint_mode: str | None
    is_available: bool
    schedule: dict[str, Any] | None = None  # {"dailySchedules": [...]}


@dataclass
class LocationData:
    """All data for one Resideo location."""

    location_id: str
    name: str
    city: str | None
    country: str | None
    time_zone: str | None
    tcs_ids: list[str] = field(default_factory=list)
    system_mode: str | None = None
    zones: dict[str, ZoneData] = field(default_factory=dict)


@dataclass
class EvohomeData:
    locations: dict[str, LocationData] = field(default_factory=dict)

    def iter_zones(self) -> list[ZoneData]:
        return [z for loc in self.locations.values() for z in loc.zones.values()]


class EvohomeDataUpdateCoordinator(DataUpdateCoordinator[EvohomeData]):
    """Polls every location and exposes a flat per-zone view."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EvohomeApiClient,
        scan_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval,
        )
        self.client = client
        self._installation: list[dict[str, Any]] | None = None

    @property
    def installation(self) -> list[dict[str, Any]]:
        if self._installation is None:
            raise RuntimeError("Installation info has not been loaded yet")
        return self._installation

    async def _async_update_data(self) -> EvohomeData:
        try:
            if self._installation is None:
                self._installation = await self.client.async_get_locations()

            data = EvohomeData()
            for loc_cfg in self._installation:
                info = loc_cfg["locationInfo"]
                loc = LocationData(
                    location_id=str(info["locationId"]),
                    name=info.get("name") or f"Location {info['locationId']}",
                    city=info.get("city"),
                    country=info.get("country"),
                    time_zone=(info.get("timeZone") or {}).get("timeZoneId"),
                )
                # Walk gateways/TCSs to populate zones from the static config.
                for gw in loc_cfg.get("gateways", []):
                    for tcs in gw.get("temperatureControlSystems", []):
                        tcs_id = str(tcs["systemId"])
                        loc.tcs_ids.append(tcs_id)
                        for z in tcs.get("zones", []):
                            caps = z.get("setpointCapabilities", {}) or {}
                            zone = ZoneData(
                                location_id=loc.location_id,
                                location_name=loc.name,
                                tcs_id=tcs_id,
                                zone_id=str(z["zoneId"]),
                                name=z.get("name") or f"Zone {z['zoneId']}",
                                zone_type=z.get("zoneType", "Unknown"),
                                min_setpoint=float(caps.get("minHeatSetpoint", 5)),
                                max_setpoint=float(caps.get("maxHeatSetpoint", 35)),
                                setpoint_resolution=float(
                                    caps.get("valueResolution", 0.5)
                                ),
                                allowed_modes=list(
                                    caps.get("allowedSetpointModes", [])
                                ),
                                temperature=None,
                                target_setpoint=None,
                                setpoint_mode=None,
                                is_available=False,
                            )
                            loc.zones[zone.zone_id] = zone
                data.locations[loc.location_id] = loc

            await self._update_status(data)
            await self._update_schedules(data)
            return data

        except EvohomeAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except EvohomeApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

    async def _update_status(self, data: EvohomeData) -> None:
        """Pull status for every location concurrently."""
        results = await asyncio.gather(
            *(
                self.client.async_get_location_status(loc_id)
                for loc_id in data.locations
            ),
            return_exceptions=True,
        )
        for loc_id, status in zip(data.locations, results):
            loc = data.locations[loc_id]
            if isinstance(status, Exception):
                _LOGGER.warning("Status fetch failed for %s: %s", loc_id, status)
                continue
            for gw in status.get("gateways", []):
                for tcs in gw.get("temperatureControlSystems", []):
                    mode = (tcs.get("systemModeStatus") or {}).get("mode")
                    if mode is not None:
                        loc.system_mode = mode
                    for z in tcs.get("zones", []):
                        zone = loc.zones.get(str(z["zoneId"]))
                        if not zone:
                            continue
                        temp = (z.get("temperatureStatus") or {}).get("temperature")
                        is_avail = bool(
                            (z.get("temperatureStatus") or {}).get("isAvailable", False)
                        )
                        sp = z.get("setpointStatus") or {}
                        zone.temperature = (
                            float(temp) if temp is not None else None
                        )
                        zone.target_setpoint = (
                            float(sp["targetHeatTemperature"])
                            if "targetHeatTemperature" in sp
                            else None
                        )
                        zone.setpoint_mode = sp.get("setpointMode")
                        zone.is_available = is_avail

    async def _update_schedules(self, data: EvohomeData) -> None:
        """Pull schedules for every zone concurrently."""
        zones = data.iter_zones()
        results = await asyncio.gather(
            *(self.client.async_get_zone_schedule(z.zone_id) for z in zones),
            return_exceptions=True,
        )
        for zone, sched in zip(zones, results):
            if isinstance(sched, Exception):
                _LOGGER.debug(
                    "Schedule fetch failed for zone %s: %s", zone.zone_id, sched
                )
                continue
            zone.schedule = sched
