"""Lightweight async client for the Resideo TCC v2 web API.

This is the same API used by the official `evohome` integration via
`evohome-async`, but trimmed to just what this integration needs and
extended to:

  * iterate over all locations on the account (the official integration
    only exposes one location at a time);
  * read and write zone heating schedules.

Endpoints (host: ``https://tccna.resideo.com``):

  POST /Auth/OAuth/Token                                          - obtain bearer token
  GET  /WebAPI/emea/api/v1/userAccount                            - logged-in user
  GET  /WebAPI/emea/api/v1/location/installationInfo
        ?userId={uid}&includeTemperatureControlSystems=True       - all locations
  GET  /WebAPI/emea/api/v1/location/{loc}/status
        ?includeTemperatureControlSystems=True                    - location status
  GET  /WebAPI/emea/api/v1/temperatureZone/{zone}/schedule        - zone schedule
  PUT  /WebAPI/emea/api/v1/temperatureZone/{zone}/schedule        - set zone schedule
  PUT  /WebAPI/emea/api/v1/temperatureZone/{zone}/heatSetpoint    - override setpoint
  PUT  /WebAPI/emea/api/v1/temperatureControlSystem/{tcs}/mode    - set system mode

The API keys/values are camelCase; we keep them as-is in our request and
response payloads to avoid an extra translation layer.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

TokenCache = dict[str, Any]
TokenLoader = Callable[[], Awaitable[TokenCache | None]]
TokenSaver = Callable[[TokenCache], Awaitable[None]]

_HOST = "https://tccna.resideo.com"
_TOKEN_URL = f"{_HOST}/Auth/OAuth/Token"
_API_BASE = f"{_HOST}/WebAPI/emea/api/v1"

# This client_id/secret pair is the well-known public app id used by the
# Resideo TCC web/app clients and by the upstream evohome-async library.
_APPLICATION_ID = base64.b64encode(
    b"4a231089-d2b6-41bd-a5eb-16a0a422b999:"
    b"1a15cdb8-42de-407b-add0-059f92c530cb"
).decode()

_SCOPE = "EMEA-V1-Basic EMEA-V1-Anonymous"
_TOKEN_LEEWAY = timedelta(seconds=30)
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


class EvohomeApiError(Exception):
    """Generic API error."""


class EvohomeAuthError(EvohomeApiError):
    """Authentication failed (bad credentials or rejected token)."""


class EvohomeRateLimitError(EvohomeApiError):
    """The vendor returned HTTP 429."""


class EvohomeApiClient:
    """Thin async wrapper around the Resideo TCC v2 API."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        *,
        token_loader: TokenLoader | None = None,
        token_saver: TokenSaver | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._session = session
        self._token_loader = token_loader
        self._token_saver = token_saver

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires: datetime = datetime.min.replace(tzinfo=timezone.utc)
        self._user_id: str | None = None
        self._lock = asyncio.Lock()
        self._cache_loaded = False

    # ---- auth -----------------------------------------------------------

    async def async_login(self) -> None:
        """Bring up an authenticated session.

        Reuses cached tokens if they are still valid - critical for not
        triggering Resideo's auth rate limit (HTTP 429 attempt_limit_exceeded)
        on every HA restart.
        """
        await self._load_cached_tokens()
        await self._ensure_token()
        if self._user_id is None:
            account = await self._request("GET", "userAccount")
            self._user_id = str(account["userId"])

    async def _load_cached_tokens(self) -> None:
        if self._cache_loaded or self._token_loader is None:
            self._cache_loaded = True
            return
        self._cache_loaded = True
        try:
            cached = await self._token_loader()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Token cache load failed - will reauthenticate")
            return
        if not cached:
            return
        try:
            self._access_token = cached.get("access_token") or None
            self._refresh_token = cached.get("refresh_token") or None
            expires = cached.get("token_expires")
            if expires:
                self._token_expires = datetime.fromisoformat(expires)
        except (TypeError, ValueError):
            _LOGGER.warning("Cached token data malformed; ignoring")
            self._access_token = None
            self._refresh_token = None

    async def _persist_tokens(self) -> None:
        if self._token_saver is None or self._access_token is None:
            return
        try:
            await self._token_saver(
                {
                    "access_token": self._access_token,
                    "refresh_token": self._refresh_token,
                    "token_expires": self._token_expires.isoformat(),
                }
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Persisting Evohome tokens failed - non-fatal")

    async def _ensure_token(self, *, force: bool = False) -> str:
        async with self._lock:
            now = datetime.now(timezone.utc)
            if (
                not force
                and self._access_token
                and self._token_expires - _TOKEN_LEEWAY > now
            ):
                return self._access_token

            if self._refresh_token and not force:
                try:
                    await self._fetch_token(
                        {
                            "grant_type": "refresh_token",
                            "scope": _SCOPE,
                            "refresh_token": self._refresh_token,
                        }
                    )
                    return self._access_token  # type: ignore[return-value]
                except EvohomeAuthError:
                    self._refresh_token = None  # fall back to user/pass

            await self._fetch_token(
                {
                    "grant_type": "password",
                    "scope": _SCOPE,
                    "Username": self._username,
                    "Password": self._password,
                }
            )
            return self._access_token  # type: ignore[return-value]

    async def _fetch_token(self, data: dict[str, str]) -> None:
        headers = {
            "Accept": "application/json",
            "Authorization": "Basic " + _APPLICATION_ID,
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
        }
        try:
            async with self._session.post(
                _TOKEN_URL, headers=headers, data=data, timeout=_DEFAULT_TIMEOUT
            ) as resp:
                payload = await _safe_json(resp)
                if resp.status >= 400:
                    msg = (payload or {}).get("error") or resp.reason or "auth failed"
                    if resp.status == 429:
                        raise EvohomeRateLimitError(
                            f"Resideo auth rate-limit hit ({msg}); waiting "
                            "before next attempt"
                        )
                    if resp.status in (400, 401):
                        raise EvohomeAuthError(f"{resp.status}: {msg}")
                    raise EvohomeApiError(f"{resp.status}: {msg}")
        except aiohttp.ClientError as err:
            raise EvohomeApiError(f"Network error during auth: {err}") from err

        try:
            self._access_token = payload["access_token"]
            self._refresh_token = payload.get("refresh_token")
            self._token_expires = datetime.now(timezone.utc) + timedelta(
                seconds=int(payload["expires_in"])
            )
        except (KeyError, TypeError, ValueError) as err:
            raise EvohomeApiError(f"Malformed token response: {payload}") from err

        await self._persist_tokens()

    # ---- core HTTP ------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        _retry_on_401: bool = True,
    ) -> Any:
        token = await self._ensure_token()
        url = f"{_API_BASE}/{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": "bearer " + token,
        }
        if json is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
                timeout=_DEFAULT_TIMEOUT,
            ) as resp:
                if resp.status == 401 and _retry_on_401:
                    self._access_token = None
                    return await self._request(
                        method, path, json=json, params=params, _retry_on_401=False
                    )
                if resp.status == 429:
                    raise EvohomeRateLimitError("Rate limited (HTTP 429)")
                if resp.status >= 400:
                    body = await resp.text()
                    raise EvohomeApiError(
                        f"{method} {path} -> {resp.status}: {body[:300]}"
                    )
                if resp.status == 204 or resp.content_length == 0:
                    return None
                return await _safe_json(resp)
        except aiohttp.ClientError as err:
            raise EvohomeApiError(f"Network error on {method} {path}: {err}") from err

    # ---- account / topology --------------------------------------------

    async def async_get_user_id(self) -> str:
        if self._user_id is None:
            account = await self._request("GET", "userAccount")
            self._user_id = str(account["userId"])
        return self._user_id

    async def async_get_locations(self) -> list[dict[str, Any]]:
        """Return the full installation info for every location on the account."""
        user_id = await self.async_get_user_id()
        return await self._request(
            "GET",
            "location/installationInfo",
            params={
                "userId": user_id,
                "includeTemperatureControlSystems": "True",
            },
        )

    async def async_get_location_status(self, location_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"location/{location_id}/status",
            params={"includeTemperatureControlSystems": "True"},
        )

    # ---- zone schedules -------------------------------------------------

    async def async_get_zone_schedule(self, zone_id: str) -> dict[str, Any]:
        """Return the schedule for a heating zone (camelCase keys preserved)."""
        return await self._request("GET", f"temperatureZone/{zone_id}/schedule")

    async def async_set_zone_schedule(
        self, zone_id: str, schedule: dict[str, Any] | list[Any]
    ) -> None:
        """Replace the schedule for a heating zone.

        ``schedule`` may either be the full ``{"dailySchedules": [...]}`` dict
        as returned by ``async_get_zone_schedule`` or just the list of
        dailySchedules - both forms are accepted for convenience.
        """
        body = (
            {"dailySchedules": schedule}
            if isinstance(schedule, list)
            else schedule
        )
        await self._request(
            "PUT", f"temperatureZone/{zone_id}/schedule", json=body
        )

    # ---- zone overrides / system mode ----------------------------------

    async def async_set_zone_heat_setpoint(
        self,
        zone_id: str,
        *,
        setpoint_mode: str,
        heat_setpoint_value: float | None = None,
        time_until: str | None = None,
    ) -> None:
        """Override the current heat setpoint of a zone.

        ``setpoint_mode`` is one of ``"FollowSchedule"``, ``"PermanentOverride"``
        or ``"TemporaryOverride"``. ``time_until`` (ISO 8601) is required for
        TemporaryOverride. ``heat_setpoint_value`` is required for both
        override modes.
        """
        body: dict[str, Any] = {"setpointMode": setpoint_mode}
        if heat_setpoint_value is not None:
            body["heatSetpointValue"] = heat_setpoint_value
        if time_until is not None:
            body["timeUntil"] = time_until
        await self._request(
            "PUT", f"temperatureZone/{zone_id}/heatSetpoint", json=body
        )

    async def async_set_system_mode(
        self,
        tcs_id: str,
        *,
        system_mode: str,
        permanent: bool = True,
        time_until: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"systemMode": system_mode, "permanent": permanent}
        if time_until is not None:
            body["timeUntil"] = time_until
        await self._request(
            "PUT", f"temperatureControlSystem/{tcs_id}/mode", json=body
        )

    # ---- domestic hot water --------------------------------------------

    async def async_get_dhw_schedule(self, dhw_id: str) -> dict[str, Any]:
        return await self._request("GET", f"domesticHotWater/{dhw_id}/schedule")

    async def async_set_dhw_schedule(
        self, dhw_id: str, schedule: dict[str, Any] | list[Any]
    ) -> None:
        body = (
            {"dailySchedules": schedule}
            if isinstance(schedule, list)
            else schedule
        )
        await self._request(
            "PUT", f"domesticHotWater/{dhw_id}/schedule", json=body
        )

    async def async_set_dhw_state(
        self,
        dhw_id: str,
        *,
        mode: str,
        state: str | None = None,
        time_until: str | None = None,
    ) -> None:
        """Set DHW mode/state.

        ``mode`` is one of FollowSchedule / PermanentOverride / TemporaryOverride.
        ``state`` (On/Off) is required for the override modes.
        """
        body: dict[str, Any] = {"mode": mode}
        if state is not None:
            body["state"] = state
        if time_until is not None:
            body["untilTime"] = time_until
        await self._request("PUT", f"domesticHotWater/{dhw_id}/state", json=body)


async def _safe_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError):
        return None
