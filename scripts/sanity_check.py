"""Local sanity check for the Evohome Control API client.

Reads EVOHOME_USER / EVOHOME_PASS from the environment (or a local .env that
you don't commit) and exercises the same endpoints the integration uses:

  * authenticate
  * list every location and its zones
  * fetch a sample zone schedule
  * round-trip the same schedule back via PUT

This is purely a developer aid - no Home Assistant required. Run with:

    EVOHOME_USER=... EVOHOME_PASS=... python scripts/sanity_check.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Import the api module directly so we don't pull in the Home Assistant deps
# that the package's __init__.py needs.
import importlib.util  # noqa: E402

import aiohttp  # noqa: E402

_API_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "custom_components",
    "evohome_control",
    "api.py",
)
_spec = importlib.util.spec_from_file_location("_evohome_api", _API_PATH)
assert _spec and _spec.loader
_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_api)
EvohomeApiClient = _api.EvohomeApiClient


async def main() -> None:
    user = os.environ.get("EVOHOME_USER")
    password = os.environ.get("EVOHOME_PASS")
    if not user or not password:
        sys.exit("Set EVOHOME_USER and EVOHOME_PASS in the environment.")

    async with aiohttp.ClientSession() as session:
        client = EvohomeApiClient(user, password, session)
        await client.async_login()
        print(f"Logged in as user_id={await client.async_get_user_id()}")

        locations = await client.async_get_locations()
        print(f"\n{len(locations)} location(s):")
        for loc in locations:
            info = loc["locationInfo"]
            print(
                f"  - {info['name']!r} (id={info['locationId']}, "
                f"city={info.get('city')!r})"
            )
            for gw in loc.get("gateways", []):
                for tcs in gw.get("temperatureControlSystems", []):
                    print(
                        f"      tcs={tcs['systemId']} "
                        f"zones={len(tcs.get('zones', []))}"
                    )

        first_loc = locations[0]
        first_zone = first_loc["gateways"][0]["temperatureControlSystems"][0][
            "zones"
        ][0]
        zone_id = str(first_zone["zoneId"])
        print(f"\nGet schedule for zone {zone_id} ({first_zone['name']})...")
        sched = await client.async_get_zone_schedule(zone_id)
        days = [d["dayOfWeek"] for d in sched["dailySchedules"]]
        print(f"  -> {len(days)} day(s): {days}")

        print("PUT schedule back unchanged...")
        await client.async_set_zone_schedule(zone_id, sched)
        print("  -> OK")

        print("\nFirst day raw JSON:")
        print(json.dumps(sched["dailySchedules"][0], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
