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

## Services

| Service | Description |
|---|---|
| `evohome_control.get_schedule` | Returns the current weekly schedule for a zone (response service). |
| `evohome_control.set_schedule` | Replaces the weekly schedule for a zone. |
| `evohome_control.apply_day_schedule` | Replace one or more days' switchpoints across one or more zones in a single call. |
| `evohome_control.copy_zone_schedule` | Copies the schedule from one zone onto another. |
| `evohome_control.set_zone_temperature_until` | Override a zone's (or several zones') setpoint - permanent, for a duration, or until a time. |
| `evohome_control.clear_zone_override` | Return a zone to FollowSchedule. |
| `evohome_control.set_system_mode` | Change the system mode of a location (optionally until a time). |
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
