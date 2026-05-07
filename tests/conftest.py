"""Minimal Home Assistant stubs so integration code can be imported without HA."""
from __future__ import annotations

import re
import sys
import types
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import voluptuous as vol


# ---------------------------------------------------------------------------
# Real classes that need to be raiseable / callable
# ---------------------------------------------------------------------------


class HomeAssistantError(Exception):
    pass


class ConfigEntryAuthFailed(HomeAssistantError):
    pass


class ConfigEntryNotReady(HomeAssistantError):
    pass


# ---------------------------------------------------------------------------
# homeassistant.helpers.config_validation  (cv) — real validators
# ---------------------------------------------------------------------------


def _cv_string(value: Any) -> str:
    return str(value)


def _cv_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "on", "1")
    return bool(value)


def _cv_ensure_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return [value]


def _cv_time_period(value: Any) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, dict):
        return timedelta(**value)
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) == 3:
            return timedelta(
                hours=int(parts[0]), minutes=int(parts[1]), seconds=int(parts[2])
            )
        if len(parts) == 2:
            return timedelta(hours=int(parts[0]), minutes=int(parts[1]))
    raise vol.Invalid(f"Cannot convert {value!r} to timedelta")


def _cv_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    raise vol.Invalid(f"Expected datetime, got {value!r}")


def _cv_matches_regex(pattern: str):
    compiled = re.compile(pattern)

    def validator(value: Any) -> str:
        s = str(value)
        if not compiled.match(s):
            raise vol.Invalid(f"{s!r} does not match pattern {pattern!r}")
        return s

    return validator


# ---------------------------------------------------------------------------
# Build stub modules
# ---------------------------------------------------------------------------


def _mod(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class _SupportsResponse:
    ONLY = "only"
    OPTIONAL = "optional"
    NONE = "none"


_cv_mod = _mod(
    "homeassistant.helpers.config_validation",
    string=_cv_string,
    boolean=_cv_boolean,
    ensure_list=_cv_ensure_list,
    time_period=_cv_time_period,
    datetime=_cv_datetime,
    matches_regex=_cv_matches_regex,
)

_core_mod = _mod(
    "homeassistant.core",
    HomeAssistant=object,
    ServiceCall=object,
    ServiceResponse=dict,
    SupportsResponse=_SupportsResponse,
    callback=lambda f: f,
)

_exceptions_mod = _mod(
    "homeassistant.exceptions",
    HomeAssistantError=HomeAssistantError,
    ConfigEntryAuthFailed=ConfigEntryAuthFailed,
    ConfigEntryNotReady=ConfigEntryNotReady,
)

class _DataUpdateCoordinator:
    """Stub base class; supports generic subscript (DataUpdateCoordinator[T])."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_interval):
        pass


class _CoordinatorEntity:
    def __class_getitem__(cls, item):
        return cls


_coordinator_mod = _mod(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_DataUpdateCoordinator,
    UpdateFailed=Exception,
    CoordinatorEntity=_CoordinatorEntity,
)

_storage_mod = _mod("homeassistant.helpers.storage", Store=MagicMock)
_aiohttp_client_mod = _mod(
    "homeassistant.helpers.aiohttp_client", async_get_clientsession=MagicMock()
)
_device_registry_mod = _mod("homeassistant.helpers.device_registry", DeviceInfo=dict)
_entity_platform_mod = _mod(
    "homeassistant.helpers.entity_platform", AddEntitiesCallback=object
)

_const_mod = _mod(
    "homeassistant.const",
    CONF_USERNAME="username",
    CONF_PASSWORD="password",
    CONF_SCAN_INTERVAL="scan_interval",
    Platform=MagicMock(),
    ATTR_TEMPERATURE="temperature",
    UnitOfTemperature=MagicMock(),
)

_config_entries_mod = _mod(
    "homeassistant.config_entries",
    ConfigEntry=object,
    ConfigFlow=object,
    OptionsFlow=object,
    config_entries=MagicMock(),
)

_data_entry_flow_mod = _mod("homeassistant.data_entry_flow", FlowResult=dict)

# Component stubs (entities)
_climate_mod = _mod(
    "homeassistant.components.climate",
    ClimateEntity=object,
    HVACMode=MagicMock(),
    ClimateEntityFeature=MagicMock(),
)
_water_heater_mod = _mod(
    "homeassistant.components.water_heater",
    WaterHeaterEntity=object,
    WaterHeaterEntityFeature=MagicMock(),
    STATE_ON=MagicMock(),
    STATE_OFF=MagicMock(),
    STATE_ELECTRIC=MagicMock(),
)
_binary_sensor_mod = _mod(
    "homeassistant.components.binary_sensor",
    BinarySensorEntity=object,
    BinarySensorDeviceClass=MagicMock(),
)
_sensor_mod = _mod(
    "homeassistant.components.sensor",
    SensorEntity=object,
    SensorDeviceClass=MagicMock(),
)
_select_mod = _mod(
    "homeassistant.components.select",
    SelectEntity=object,
)
_button_mod = _mod(
    "homeassistant.components.button",
    ButtonEntity=object,
)

_helpers_mod = _mod(
    "homeassistant.helpers",
    config_validation=_cv_mod,
)

# aiohttp stub (only the surface used by api.py at module level)
_aiohttp_mod = types.ModuleType("aiohttp")
_aiohttp_mod.ClientSession = MagicMock  # type: ignore[attr-defined]
_aiohttp_mod.ClientError = OSError  # type: ignore[attr-defined]
_aiohttp_mod.ClientResponseError = OSError  # type: ignore[attr-defined]
_aiohttp_mod.ContentTypeError = OSError  # type: ignore[attr-defined]
_aiohttp_mod.ClientTimeout = MagicMock(return_value=None)  # type: ignore[attr-defined]
_aiohttp_mod.ClientResponse = MagicMock  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Register all stubs before any integration import
# ---------------------------------------------------------------------------

_STUB_MODULES: dict[str, types.ModuleType] = {
    "aiohttp": _aiohttp_mod,
    "homeassistant": types.ModuleType("homeassistant"),
    "homeassistant.core": _core_mod,
    "homeassistant.exceptions": _exceptions_mod,
    "homeassistant.const": _const_mod,
    "homeassistant.config_entries": _config_entries_mod,
    "homeassistant.data_entry_flow": _data_entry_flow_mod,
    "homeassistant.helpers": _helpers_mod,
    "homeassistant.helpers.config_validation": _cv_mod,
    "homeassistant.helpers.update_coordinator": _coordinator_mod,
    "homeassistant.helpers.storage": _storage_mod,
    "homeassistant.helpers.aiohttp_client": _aiohttp_client_mod,
    "homeassistant.helpers.device_registry": _device_registry_mod,
    "homeassistant.helpers.entity_platform": _entity_platform_mod,
    "homeassistant.components": types.ModuleType("homeassistant.components"),
    "homeassistant.components.climate": _climate_mod,
    "homeassistant.components.water_heater": _water_heater_mod,
    "homeassistant.components.binary_sensor": _binary_sensor_mod,
    "homeassistant.components.sensor": _sensor_mod,
    "homeassistant.components.select": _select_mod,
    "homeassistant.components.button": _button_mod,
}

for _name, _mod_obj in _STUB_MODULES.items():
    sys.modules.setdefault(_name, _mod_obj)
