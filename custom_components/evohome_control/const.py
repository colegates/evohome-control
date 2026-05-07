"""Constants for the Evohome Control integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "evohome_control"
MANUFACTURER: Final = "Resideo / Honeywell Home"

DEFAULT_SCAN_INTERVAL: Final = 180  # seconds; matches the official integration
MIN_SCAN_INTERVAL: Final = 60

# Days of the week, in the order the API expects them.
DAYS_OF_WEEK: Final = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# Service names.
SERVICE_GET_SCHEDULE: Final = "get_schedule"
SERVICE_SET_SCHEDULE: Final = "set_schedule"
SERVICE_COPY_SCHEDULE: Final = "copy_zone_schedule"
SERVICE_APPLY_DAY_SCHEDULE: Final = "apply_day_schedule"
SERVICE_REFRESH_SCHEDULES: Final = "refresh_schedules"
SERVICE_SET_SYSTEM_MODE: Final = "set_system_mode"
SERVICE_SET_ZONE_OVERRIDE: Final = "set_zone_temperature_until"
SERVICE_CLEAR_ZONE_OVERRIDE: Final = "clear_zone_override"
SERVICE_AWAY_UNTIL: Final = "away_until"
SERVICE_BOOST: Final = "boost"
SERVICE_SAVE_PRESET: Final = "save_preset"
SERVICE_APPLY_PRESET: Final = "apply_preset"
SERVICE_LIST_PRESETS: Final = "list_presets"
SERVICE_DELETE_PRESET: Final = "delete_preset"
SERVICE_EXPORT_SCHEDULES: Final = "export_schedules"
SERVICE_IMPORT_SCHEDULES: Final = "import_schedules"
SERVICE_DHW_BOOST: Final = "dhw_boost"
SERVICE_DHW_CLEAR_OVERRIDE: Final = "dhw_clear_override"

# Storage keys.
STORAGE_VERSION: Final = 1
STORAGE_KEY_PRESETS: Final = "evohome_control_presets"

# Service / attribute keys.
ATTR_LOCATION_ID: Final = "location_id"
ATTR_ZONE_ID: Final = "zone_id"
ATTR_ZONE_IDS: Final = "zone_ids"
ATTR_DAYS_OF_WEEK: Final = "days_of_week"
ATTR_FROM_ZONE_ID: Final = "from_zone_id"
ATTR_TO_ZONE_ID: Final = "to_zone_id"
ATTR_SCHEDULE: Final = "schedule"
ATTR_DAILY_SCHEDULES: Final = "daily_schedules"
ATTR_DAY_OF_WEEK: Final = "day_of_week"
ATTR_SWITCHPOINTS: Final = "switchpoints"
ATTR_TIME_OF_DAY: Final = "time_of_day"
ATTR_HEAT_SETPOINT: Final = "heat_setpoint"
ATTR_TEMPERATURE: Final = "temperature"
ATTR_DURATION: Final = "duration"
ATTR_UNTIL: Final = "until"
ATTR_SYSTEM_MODE: Final = "system_mode"
ATTR_PERMANENT: Final = "permanent"
ATTR_NAME: Final = "name"
ATTR_PRESET_NAME: Final = "preset_name"
ATTR_SCHEDULES: Final = "schedules"
ATTR_DHW_ID: Final = "dhw_id"
ATTR_STATE: Final = "state"
