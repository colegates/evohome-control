# Changelog

All notable changes to the Evohome Control integration.

## 0.4.4 — 2026-11-16

### Changed (behaviour)

- **`climate.set_temperature` is no longer a permanent hold.** A plain
  temperature change from HA (the dashboard thermostat card, voice assistants,
  Assist, MCP clients, automations, scripts) is now applied as a
  `TemporaryOverride` that ends at the zone's next scheduled switchpoint -
  matching the behaviour of a local setpoint change on the Evohome controller
  itself. If the schedule is missing or empty, we fall back to a one-hour
  `TemporaryOverride` rather than a permanent override.

  Permanent holds remain available for the cases where they are explicit:
  - the `permanent_override` preset
  - `evohome_control.set_zone_temperature_until` with no `duration`/`until`
  - `hvac_mode` set to `heat` or `off`

### Added

- **Caller-attribution logging on every setpoint/mode write.** Each write
  path (climate, water heater, select, button, and every service in
  `services.py`) emits a single `INFO` line to
  `custom_components.evohome_control.*` with the zone/DHW id, mode,
  temperature, until, and the caller resolved from the service call or
  entity context:
    - `context.user_id` set → resolved via `hass.auth.async_get_user`
    - `context.parent_id` set → `automation/script`
    - otherwise → `unknown`

  Tokens and credentials are never included.

### Internal

- New `timing.py` module centralises timezone-aware "what does the schedule
  say now, and when does it change next?" logic so DHW and heating zones
  share one implementation.
- New `audit.py` module for consistent caller-attribution log lines.
- 25 new unit tests covering same-day/cross-day/cross-weekday wraps, sparse
  schedules, the empty-schedule fallback, and the fix for the "set_temperature
  became a permanent hold" regression.
