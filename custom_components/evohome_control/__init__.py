"""Evohome Control - enhanced HACS integration for Resideo Evohome systems.

Improves on the official `evohome` integration by:
  * supporting multiple locations on a single TCC account
  * exposing each zone's heating schedule and allowing it to be modified
    via Home Assistant services
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import (
    EvohomeApiClient,
    EvohomeApiError,
    EvohomeAuthError,
    EvohomeRateLimitError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import EvohomeDataUpdateCoordinator
from .services import async_register_services, async_unregister_services

_TOKEN_STORE_VERSION = 1

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.WATER_HEATER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Evohome Control from a config entry."""
    session = async_get_clientsession(hass)

    # Persist auth tokens between restarts so we don't trigger the Resideo
    # auth rate-limit (HTTP 429 attempt_limit_exceeded) every time HA reloads.
    token_store: Store[dict] = Store(
        hass,
        _TOKEN_STORE_VERSION,
        f"evohome_control_tokens_{entry.entry_id}",
        private=True,
    )

    async def _load_tokens() -> dict | None:
        return await token_store.async_load()

    async def _save_tokens(tokens: dict) -> None:
        await token_store.async_save(tokens)

    client = EvohomeApiClient(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        session=session,
        token_loader=_load_tokens,
        token_saver=_save_tokens,
    )

    try:
        await client.async_login()
    except EvohomeAuthError as err:
        _LOGGER.error("Authentication with Evohome/Resideo failed: %s", err)
        raise ConfigEntryAuthFailed(str(err)) from err
    except EvohomeRateLimitError as err:
        _LOGGER.warning(
            "Resideo auth rate-limited - HA will retry automatically in a "
            "few minutes (%s)",
            err,
        )
        raise ConfigEntryNotReady(
            "Resideo auth rate-limited (HTTP 429). Will retry shortly."
        ) from err
    except EvohomeApiError as err:
        raise ConfigEntryNotReady(f"Cannot reach Resideo: {err}") from err

    scan_interval = timedelta(
        seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    coordinator = EvohomeDataUpdateCoordinator(hass, client, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change (e.g. scan interval)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)
    return unloaded
