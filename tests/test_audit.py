"""Tests for the setpoint-write audit log helper."""
from __future__ import annotations

import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.conftest  # noqa: F401

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from homeassistant.core import Context

from custom_components.evohome_control import audit


@pytest.mark.asyncio
async def test_describe_caller_named_user():
    hass = MagicMock()
    user = MagicMock()
    user.name = "Mike"
    hass.auth.async_get_user = AsyncMock(return_value=user)
    ctx = Context(user_id="abc123")
    assert await audit.describe_caller(hass, ctx) == "Mike"


@pytest.mark.asyncio
async def test_describe_caller_falls_back_to_user_id_when_no_name():
    hass = MagicMock()
    user = MagicMock()
    user.name = None
    hass.auth.async_get_user = AsyncMock(return_value=user)
    ctx = Context(user_id="abc123")
    assert await audit.describe_caller(hass, ctx) == "user:abc123"


@pytest.mark.asyncio
async def test_describe_caller_user_not_found():
    hass = MagicMock()
    hass.auth.async_get_user = AsyncMock(return_value=None)
    ctx = Context(user_id="ghost")
    assert await audit.describe_caller(hass, ctx) == "user:ghost"


@pytest.mark.asyncio
async def test_describe_caller_automation_or_script():
    hass = MagicMock()
    ctx = Context(parent_id="chained-from-automation")
    assert await audit.describe_caller(hass, ctx) == "automation/script"


@pytest.mark.asyncio
async def test_describe_caller_unknown_when_context_has_no_attribution():
    hass = MagicMock()
    ctx = Context()
    assert await audit.describe_caller(hass, ctx) == "unknown"


@pytest.mark.asyncio
async def test_describe_caller_none_context():
    hass = MagicMock()
    assert await audit.describe_caller(hass, None) == "unknown"


@pytest.mark.asyncio
async def test_log_write_contains_caller_and_fields(caplog):
    hass = MagicMock()
    user = MagicMock()
    user.name = "Mike"
    hass.auth.async_get_user = AsyncMock(return_value=user)
    ctx = Context(user_id="abc")

    logger = logging.getLogger("custom_components.evohome_control.audit")
    caplog.set_level(logging.INFO, logger=logger.name)
    await audit.log_write(
        logger,
        hass,
        ctx,
        action="set_zone_temperature_until",
        zone_name="Dining Room",
        zone_id="1909981",
        location_id="1706229",
        setpoint_mode="PermanentOverride",
        temperature=20.0,
    )
    text = "\n".join(r.message for r in caplog.records)
    assert "action=set_zone_temperature_until" in text
    assert "caller=Mike" in text
    assert "zone='Dining Room'" in text
    assert "zone_id=1909981" in text
    assert "mode=PermanentOverride" in text
    assert "temperature=20.0" in text


@pytest.mark.asyncio
async def test_log_write_hides_optional_fields_when_absent(caplog):
    hass = MagicMock()
    hass.auth.async_get_user = AsyncMock(return_value=None)
    logger = logging.getLogger("custom_components.evohome_control.audit")
    caplog.set_level(logging.INFO, logger=logger.name)
    await audit.log_write(
        logger, hass, None, action="clear_zone_override", zone_id="1"
    )
    text = "\n".join(r.message for r in caplog.records)
    assert "zone=" not in text  # no zone_name supplied
    assert "temperature=" not in text
    assert "until=" not in text
    assert "caller=unknown" in text


@pytest.mark.asyncio
async def test_log_write_never_logs_tokens_or_credentials(caplog):
    """Belt-and-braces: log_write has no kwarg surface for secrets."""
    hass = MagicMock()
    logger = logging.getLogger("custom_components.evohome_control.audit")
    caplog.set_level(logging.INFO, logger=logger.name)
    await audit.log_write(
        logger,
        hass,
        None,
        action="set_schedule",
        zone_id="x",
        extra={"days": "Mon"},
    )
    text = "\n".join(r.message for r in caplog.records)
    for forbidden in ("access_token", "refresh_token", "password", "Bearer "):
        assert forbidden not in text
