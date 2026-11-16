"""Caller-attribution logging for setpoint/mode writes.

The coordinator's poll cycle is what eventually surfaces a state change in HA's
history and logbook, which strips the original caller. So every code path that
writes a zone setpoint, DHW mode, or system mode logs here at INFO with the
originating user resolved from the service call / entity context:

  * ``context.user_id`` set   -> the user's name via ``hass.auth.async_get_user``
  * ``context.parent_id`` set -> ``automation/script`` (HA's internal pattern)
  * otherwise                 -> ``unknown``

Tokens, passwords and schedules are never included.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import Context, HomeAssistant


async def describe_caller(hass: HomeAssistant, context: Context | None) -> str:
    """Return a short human-readable string for whoever triggered this write."""
    if context is None:
        return "unknown"

    user_id = getattr(context, "user_id", None)
    if user_id:
        try:
            user = await hass.auth.async_get_user(user_id)
        except Exception:  # noqa: BLE001  - audit must never break the write
            user = None
        if user is not None:
            # Prefer the display name, fall back to user_id for traceability
            name = getattr(user, "name", None)
            return name or f"user:{user_id}"
        return f"user:{user_id}"

    if getattr(context, "parent_id", None):
        return "automation/script"

    return "unknown"


async def log_write(
    logger: logging.Logger,
    hass: HomeAssistant,
    context: Context | None,
    *,
    action: str,
    zone_name: str | None = None,
    zone_id: str | None = None,
    dhw_id: str | None = None,
    location_id: str | None = None,
    location_name: str | None = None,
    tcs_id: str | None = None,
    setpoint_mode: str | None = None,
    temperature: float | None = None,
    dhw_state: str | None = None,
    until: datetime | str | None = None,
    system_mode: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit one INFO-level audit line for a setpoint/mode write.

    All fields are optional; only the ones you pass appear in the line.
    """
    caller = await describe_caller(hass, context)
    parts: list[str] = [f"action={action}", f"caller={caller}"]
    if zone_name is not None:
        parts.append(f"zone={zone_name!r}")
    if zone_id is not None:
        parts.append(f"zone_id={zone_id}")
    if dhw_id is not None:
        parts.append(f"dhw_id={dhw_id}")
    if location_name is not None:
        parts.append(f"location={location_name!r}")
    if location_id is not None:
        parts.append(f"location_id={location_id}")
    if tcs_id is not None:
        parts.append(f"tcs_id={tcs_id}")
    if setpoint_mode is not None:
        parts.append(f"mode={setpoint_mode}")
    if temperature is not None:
        parts.append(f"temperature={temperature}")
    if dhw_state is not None:
        parts.append(f"state={dhw_state}")
    if system_mode is not None:
        parts.append(f"system_mode={system_mode}")
    if until is not None:
        if isinstance(until, datetime):
            until_str = until.isoformat()
        else:
            until_str = str(until)
        parts.append(f"until={until_str}")
    if extra:
        for k, v in extra.items():
            parts.append(f"{k}={v}")
    logger.info("evohome write: %s", " ".join(parts))
