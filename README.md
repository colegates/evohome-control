# Evohome Control

A Home Assistant custom integration (HACS-installable) for Resideo / Honeywell
**Evohome** heating systems, intended as a richer alternative to the official
[`evohome`][hass-evohome] integration.

## Why this exists

The official integration is solid but has two limitations that this project
addresses:

1. **Single-location only.** If your TCC account owns more than one Evohome
   location (e.g. a second home), the official integration only exposes one of
   them. **Evohome Control polls every location on the account** and creates a
   climate entity for every zone in every location.
2. **No schedule control.** You can read the schedule but not change it. This
   integration adds an `evohome_control.set_schedule` service that writes the
   weekly heating schedule for any zone, plus a companion
   `evohome_control.get_schedule` service for inspection.

It uses the same Resideo TCC v2 web API as the official integration.

## Status

Working against the live API:

  * multi-location enumeration (one climate entity per zone, across every
    location on the account)
  * weekly heating schedule read/write per zone
  * domestic hot water control (`water_heater` entity, schedule round-trip)
  * per-location system-mode selector (Auto / AutoWithEco / Away / DayOff /
    HeatingOff / ...)
  * temporary or permanent zone overrides via service call
  * copy a schedule from one zone onto another

## Installation (HACS)

1. In HACS, open *Integrations* and add this repository as a custom repository
   (category: *Integration*).
2. Install **Evohome Control**.
3. Restart Home Assistant.
4. *Settings -> Devices & Services -> Add Integration -> Evohome Control* and
   sign in with your Total Connect Comfort credentials.

## A note on the Resideo auth rate limit

The Resideo TCC token endpoint rate-limits **password** logins (it returns
``HTTP 429 attempt_limit_exceeded``) when too many fresh logins arrive in a
short window. The integration mitigates this in two ways:

  * Access + refresh tokens are persisted to ``<config>/.storage/`` and
    reused across HA restarts, so reloads don't re-authenticate.
  * If the rate limit does hit (e.g. on first install while another client
    is also logging in), HA marks the entry "not ready" and retries
    automatically with backoff - no manual intervention needed.

If you also have the official ``evohome`` integration loaded against the
**same Resideo account**, both clients will compete for fresh logins.
Disabling one is recommended.

## Sync model

The integration polls the API every `scan_interval` seconds (default 180,
configurable in the integration's options). On each poll it re-fetches:

  * **installation topology** (zone names, gateways, DHW), so renaming or
    adding a zone in the Resideo app shows up in HA at the next interval -
    no restart needed
  * **location status** (live setpoints, current temperatures, system mode,
    DHW state, **active faults / battery**)
  * **the full weekly schedule** for every zone (and DHW)

Service calls that patch part of a schedule (`apply_day_schedule`,
`copy_zone_schedule`) always GET-modify-PUT against the live API, so they
cannot clobber unrelated edits you made in the app on different days. After
every write HA force-refreshes immediately rather than waiting for the next
interval. Each climate entity exposes a `last_synced` attribute so you can
sanity-check that polling is healthy.

## Services

### Schedule management

| Service | Description |
|---|---|
| `evohome_control.get_schedule` | Returns the current weekly schedule for a zone (response service). |
| `evohome_control.set_schedule` | Replaces the weekly schedule for a zone. |
| `evohome_control.apply_day_schedule` | Replace one or more days' switchpoints across one or more zones in a single call. |
| `evohome_control.copy_zone_schedule` | Copies the schedule from one zone onto another. |
| `evohome_control.export_schedules` | Returns full schedules of every zone (or a subset) as a response - pipe to a file/notify for backups. |
| `evohome_control.import_schedules` | Bulk-restore schedules (e.g. from a previous `export_schedules`). |

### Schedule presets

| Service | Description |
|---|---|
| `evohome_control.save_preset` | Snapshot the current weekly schedule of every zone (or a subset) under a name (e.g. `comfort`, `eco`, `holiday`). |
| `evohome_control.apply_preset` | Restore a previously-saved preset. |
| `evohome_control.list_presets` | Return the names + metadata of saved presets (response service). |
| `evohome_control.delete_preset` | Remove a saved preset. |

### Overrides & system mode

| Service | Description |
|---|---|
| `evohome_control.set_zone_temperature_until` | Override one or more zones - permanent, for a duration, or until a time. |
| `evohome_control.clear_zone_override` | Return a zone to FollowSchedule. |
| `evohome_control.boost` | Sugar: temporary override at default 21°C for 1 hour. |
| `evohome_control.set_system_mode` | Change the system mode of a location (optionally until a time). |
| `evohome_control.away_until` | Sugar: set every (or one) location to Away mode until a specific time. |
| `evohome_control.refresh_schedules` | Force-poll every location. |

## Bulk schedule editing

To roll out a new weekday pattern to several zones in one call:

```yaml
service: evohome_control.apply_day_schedule
data:
  zone_ids:
    - "1909976"   # Dog Room
    - "1909978"   # Kitchen Dining
    - "1909980"   # Kitchen
  days_of_week: [Monday, Tuesday, Wednesday, Thursday, Friday]
  switchpoints:
    - { timeOfDay: "06:30", heatSetpoint: 21.0 }
    - { timeOfDay: "08:30", heatSetpoint: 18.0 }
    - { timeOfDay: "17:30", heatSetpoint: 21.0 }
    - { timeOfDay: "22:00", heatSetpoint: 16.0 }
```

Days not listed are left untouched, so the same call can be issued again with
`days_of_week: [Saturday, Sunday]` and a different switchpoint list to set a
weekend pattern.

## Low-battery alerts

Each zone exposes two binary sensors:

  * `binary_sensor.<zone>_battery` (device_class `battery`) - on whenever the
    zone reports a low-battery fault for its sensor or actuator
  * `binary_sensor.<zone>_fault` (device_class `problem`) - on whenever the
    zone reports any active fault (low battery, comms loss, etc.)

### Example: set a weekday-vs-weekend schedule

```yaml
service: evohome_control.set_schedule
data:
  zone_id: "1909976"          # the Resideo zone id (climate entity attribute)
  schedule:
    dailySchedules:
      - dayOfWeek: Monday
        switchpoints:
          - { timeOfDay: "06:30:00", heatSetpoint: 21.0 }
          - { timeOfDay: "08:30:00", heatSetpoint: 18.0 }
          - { timeOfDay: "17:30:00", heatSetpoint: 21.0 }
          - { timeOfDay: "22:00:00", heatSetpoint: 16.0 }
      - dayOfWeek: Saturday
        switchpoints:
          - { timeOfDay: "08:00:00", heatSetpoint: 21.0 }
          - { timeOfDay: "23:00:00", heatSetpoint: 16.0 }
```

If you supply only some days, the integration fills the rest by repeating the
preceding day - so the example above gives you a Mon-Fri schedule and a
Sat-Sun schedule with no duplication.

`zone_id` values are visible as an attribute of every climate entity created
by the integration. If two locations share the same id (very rare), pass
`location_id` as well.

## Local development

```bash
git clone https://github.com/colegates/evohome-control.git
cd evohome-control

# Sanity-check the client against your real account (creds via env vars):
EVOHOME_USER=you@example.com EVOHOME_PASS='...' python scripts/sanity_check.py
```

`.env` and any token files are gitignored - **never commit your TCC password**.

## Credits

The Resideo TCC v2 endpoint paths and quirks were learned from
[zxdavb/evohome-async][evohome-async], the upstream library used by the
official Home Assistant integration.

[hass-evohome]: https://www.home-assistant.io/integrations/evohome/
[evohome-async]: https://github.com/zxdavb/evohome-async
