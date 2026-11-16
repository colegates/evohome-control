# Changelog

All notable changes to the Evohome Control integration.

## 0.4.5 — 2026-11-16

### Fixed

- **Windows-style timezone ids are now recognised.** The Resideo API returns
  the location's `timeZoneId` as a Windows name - `"GMTStandardTime"` for
  Colegates, `"W. Europe Standard Time"`, `"Romance Standard Time"`, etc. The
  previous code tried `ZoneInfo(tz_id)` directly, which fails for Windows
  ids, and silently fell back to UTC. That was invisible in winter when the
  UK is on UTC anyway, but every "until next switchpoint" computation
  (`climate.set_temperature`, `dhw_boost`, `dhw_clear_override`) was computed
  **an hour late through BST**. We now map Windows ids to IANA (with a
  whitespace-stripped lookup, so both `"GMTStandardTime"` and
  `"GMT Standard Time"` work), fall back to HA's own default time zone if a
  name still won't resolve, and log one WARN per unknown id naming the
  location.

- **No more "blocking call" warnings from `ZoneInfo(...)` inside the event
  loop.** Each location's `tzinfo` is now resolved once, in the coordinator's
  installation-load step, via `hass.async_add_executor_job`. The sync helper
  underneath is `lru_cache`-memoised so subsequent polls and setpoint writes
  never touch the filesystem.

### Internal

- `timing.resolve_tz_sync` + `timing.async_resolve_tz` are the new entry
  points; the old `_resolve_tz` is gone. `schedule_state_now`,
  `dhw_schedule_state_now` and `zone_next_switchpoint` now accept a
  pre-resolved `tz` (preferred) or a raw `tz_id` (back-compat for tests).
- `LocationData.tz: tzinfo | None` holds the resolved zone; the raw
  `time_zone` string is preserved for diagnostics.
- 19 new unit tests: Windows → IANA mappings (Colegates's `GMTStandardTime`
  plus five European variants), BST next_change is the correct UTC instant,
  unknown id falls back to HA's default and logs exactly once,
  `async_resolve_tz` really uses `hass.async_add_executor_job`, and the
  coordinator attaches the right tz to each `LocationData`.

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
