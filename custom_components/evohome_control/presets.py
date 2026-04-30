"""Persistent storage for named whole-house schedule presets.

A preset is a snapshot of one or more zones' weekly schedules under a
human-readable name (e.g. "comfort", "eco", "holiday"). It is stored in HA's
``.storage`` directory so it survives restarts and is easy to back up.

A preset's payload looks like::

    {
        "name": "comfort",
        "created": "2026-04-30T10:11:12+00:00",
        "zones": [
            {"zone_id": "1909976", "zone_name": "Dog Room",
             "location_id": "1706229",
             "schedule": {"dailySchedules": [...]}},
            ...
        ]
    }
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_PRESETS, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


class PresetStore:
    """Tiny wrapper around HA's Store that exposes named presets."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_PRESETS
        )
        self._cache: dict[str, dict[str, Any]] | None = None

    async def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            data = await self._store.async_load()
            self._cache = data or {}
        return self._cache

    async def list_names(self) -> list[str]:
        data = await self._load()
        return sorted(data.keys())

    async def get(self, name: str) -> dict[str, Any] | None:
        data = await self._load()
        return data.get(name)

    async def save(self, name: str, zones: list[dict[str, Any]]) -> None:
        data = await self._load()
        data[name] = {
            "name": name,
            "created": datetime.now(timezone.utc).isoformat(),
            "zones": zones,
        }
        await self._store.async_save(data)

    async def delete(self, name: str) -> bool:
        data = await self._load()
        if name not in data:
            return False
        del data[name]
        await self._store.async_save(data)
        return True
