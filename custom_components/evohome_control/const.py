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
SERVICE_REFRESH_SCHEDULES: Final = "refresh_schedules"

# Service / attribute keys.
ATTR_LOCATION_ID: Final = "location_id"
ATTR_ZONE_ID: Final = "zone_id"
ATTR_SCHEDULE: Final = "schedule"
ATTR_DAILY_SCHEDULES: Final = "daily_schedules"
ATTR_DAY_OF_WEEK: Final = "day_of_week"
ATTR_SWITCHPOINTS: Final = "switchpoints"
ATTR_TIME_OF_DAY: Final = "time_of_day"
ATTR_HEAT_SETPOINT: Final = "heat_setpoint"
