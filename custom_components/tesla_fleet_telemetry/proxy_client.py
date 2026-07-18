"""HTTP clients for Tesla vehicle commands and Fleet API management calls."""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any, cast

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tesla_fleet_api.const import Method
from tesla_fleet_api.exceptions import (
    DeviceUnexpectedResponse,
    Forbidden,
    InvalidScope,
    Locked,
    LoginRequired,
    OAuthExpired,
    PaymentRequired,
    RateLimited,
    TeslaFleetError,
    VehicleOffline,
)
from tesla_fleet_api.tesla.fleet import TeslaFleetApi
from tesla_fleet_api.tesla.vehicle.fleet import VehicleFleet

from .const import (
    DEFAULT_CONNECTION_TIMEOUT,
    FLEET_API_VERSION,
    RATE_LIMIT_RETRY_AFTER_DEFAULT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# SSL context that skips cert verification for the local self-signed proxy cert.
_INSECURE_SSL = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_INSECURE_SSL.check_hostname = False
_INSECURE_SSL.verify_mode = ssl.CERT_NONE


class RateLimitedError(HomeAssistantError):
    """Raised when the Tesla API returns HTTP 429."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited — retry after {retry_after}s")
        self.retry_after = retry_after


class TokenRevokedError(HomeAssistantError):
    """Raised when Tesla reports the OAuth token has been revoked."""


def _translate_tesla_error(error_msg: str) -> str:
    """Translate Tesla API error codes into user-friendly messages."""
    error_lower = error_msg.lower()

    if "in_gear" in error_lower or "ingear" in error_lower:
        return "Vehicle is in gear"
    if "not_parked" in error_lower:
        return "Vehicle must be parked"
    if "not_in_park" in error_lower:
        return "Vehicle must be in park"
    if "speed" in error_lower and ("too fast" in error_lower or "moving" in error_lower):
        return "Vehicle is moving"
    if "invalid_command" in error_lower:
        return "Command not allowed in current vehicle state"
    if "not allowed" in error_lower or "not permitted" in error_lower:
        return "Command not allowed in current vehicle state"
    if error_msg.startswith("car could not execute command:"):
        return error_msg.replace("car could not execute command: ", "").title()
    if error_msg.startswith("vehicle unavailable"):
        return "Vehicle unavailable (offline or asleep)"

    return error_msg


def _format_fleet_error_data(data: Any) -> str | None:
    """Render structured Tesla error payloads into a compact string."""
    if data is None:
        return None
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("errors", "error", "reason", "message", "detail", "details"):
            value = data.get(key)
            if value is not None:
                if isinstance(value, str):
                    return value
                return f"{key}={json.dumps(value, default=str)[:200]}"
        return json.dumps(data, default=str)[:200]
    if isinstance(data, list):
        return json.dumps(data, default=str)[:200]
    return str(data)


def _map_rate_limited(err: RateLimited) -> RateLimitedError:
    data = getattr(err, "data", None) or {}
    retry_after = RATE_LIMIT_RETRY_AFTER_DEFAULT_SECONDS
    if isinstance(data, dict):
        try:
            retry_after = int(data.get("after") or retry_after)
        except (ValueError, TypeError):
            pass
    return RateLimitedError(retry_after)


def _map_auth_error(err: TeslaFleetError, data: Any) -> HomeAssistantError:
    # If Tesla rejected an interval (Tags in data), preserve the JSON so
    # extract_unsupported_interval can parse it from the exception message.
    if isinstance(data, dict) and "Tags" in data:
        safe_data = {k: v for k, v in data.items() if k in ("Tags", "Status")}
        return HomeAssistantError(json.dumps(safe_data, default=str)[:200])
    return TokenRevokedError(str(err))


def _map_known_fleet_error(err: TeslaFleetError) -> HomeAssistantError | None:
    """Map exception types with a fixed, user-friendly message. None if unmapped."""
    if isinstance(err, VehicleOffline):
        # Message must contain an _ASLEEP_MARKERS substring so CommandEntity's
        # wake-and-retry logic recognizes this as a real HTTP 408.
        return HomeAssistantError("Vehicle is offline (asleep) — waking up may help")
    if isinstance(err, Forbidden | InvalidScope):
        return HomeAssistantError(
            "Tesla rejected the request — missing OAuth scope. "
            "Re-authenticate and grant vehicle command permissions."
        )
    if isinstance(err, Locked):
        return HomeAssistantError("Tesla account is locked — contact Tesla support to unlock it")
    if isinstance(err, PaymentRequired):
        return HomeAssistantError(
            "Tesla Fleet API requires payment for this request (non-free account tier)"
        )
    if isinstance(err, DeviceUnexpectedResponse):
        return HomeAssistantError(
            "Vehicle responded with an error — it may need a reboot, OTA update, or service"
        )
    return None


def _map_unknown_fleet_error(err: TeslaFleetError, data: Any) -> HomeAssistantError:
    # Catch-all for unmapped exceptions: include type name for debugging (issue #39)
    err_type = type(err).__name__
    err_msg = str(err)[:100]  # Truncate long error messages for logs
    _LOGGER.warning("Unmapped Fleet API exception: %s: %s", err_type, err_msg)
    detail = _format_fleet_error_data(data)
    if detail and detail not in err_msg:
        return HomeAssistantError(f"Fleet API error ({err_type}): {err_msg} ({detail})")
    return HomeAssistantError(f"Fleet API error ({err_type}): {err_msg}")


def _map_fleet_exception(err: TeslaFleetError) -> HomeAssistantError:
    """Map a package exception to a HA-friendly error, preserving Tags for interval parsing."""
    data = getattr(err, "data", None)
    if isinstance(err, RateLimited):
        return _map_rate_limited(err)
    if isinstance(err, OAuthExpired | LoginRequired):
        return _map_auth_error(err, data)
    known = _map_known_fleet_error(err)
    if known is not None:
        return known
    return _map_unknown_fleet_error(err, data)


def _check_command_result(data: dict[str, Any]) -> None:
    """Raise HomeAssistantError when the response carries an application-level failure.

    The Tesla proxy returns HTTP 200 for commands even when the vehicle rejects
    them; failures are signalled by result=false in the body.
    """
    if data.get("result") is False:
        reason = str(data.get("reason") or "unknown error")
        raise HomeAssistantError(_translate_tesla_error(reason))
    response = data.get("response")
    if isinstance(response, dict) and response.get("result") is False:
        reason = str(response.get("reason") or "unknown error")
        raise HomeAssistantError(_translate_tesla_error(reason))


class TeslaProxyClient:
    """Sends vehicle commands via local tesla-http-proxy and Fleet API management calls."""

    def __init__(
        self,
        hass: HomeAssistant,
        proxy_url: str,
        vin: str,
        timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT,
    ) -> None:
        self._hass = hass
        self._proxy_url = proxy_url.rstrip("/")
        self._vin = vin
        self._timeout_seconds = timeout_seconds
        self._session: aiohttp.ClientSession | None = None
        self._api: TeslaFleetApi | None = None
        self._vehicle: VehicleFleet | None = None

    async def async_setup(self) -> None:
        """Create the shared HTTP session and Fleet API client pointed at the proxy."""
        connector = aiohttp.TCPConnector(ssl=_INSECURE_SSL)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
        )
        self._api = TeslaFleetApi(
            session=self._session,
            server=self._proxy_url,
            charging_scope=False,
            energy_scope=False,
            partner_scope=False,
            user_scope=False,
        )
        self._vehicle = self._api.vehicles.createFleet(self._vin)

    async def async_close(self) -> None:
        """Close the shared HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
        self._api = None
        self._vehicle = None

    def _is_session_closed(self) -> bool:
        return self._session is None or self._session.closed

    def _check_ready(self) -> None:
        if self._is_session_closed() or self._api is None or self._vehicle is None:
            raise RuntimeError("Integration not ready — please wait and try again")

    @property
    def ready(self) -> bool:
        """Return whether the proxy client can currently accept commands."""
        return not self._is_session_closed() and self._api is not None and self._vehicle is not None

    async def async_check_api_capabilities(self, access_token: str = "") -> bool:
        """Check if proxy supports required endpoints via a health check (issue #40).

        Attempts to verify basic API endpoint accessibility. This is called during
        setup to detect proxy connectivity issues early. If access_token is provided,
        performs a full health check; otherwise logs debug info only.

        Args:
            access_token: Optional OAuth token for authenticated health check.
                         If empty, performs minimal connectivity checks.

        Returns:
            True if health check passed or skipped gracefully, False on definite failure.
        """
        try:
            self._check_ready()
            # If we have a token, try a config fetch as a meaningful health check
            if access_token:
                try:
                    await self.proxy_fleet_telemetry_get(access_token)
                    _LOGGER.debug("Proxy API health check passed (config fetch successful)")
                except Exception as err:
                    # Auth errors or proxy errors — log but don't fail
                    err_msg = str(err)[:100]
                    _LOGGER.debug("Proxy API health check failed: %s", err_msg)
                    return False
            else:
                # Without token, just verify session is ready
                _LOGGER.debug("Proxy API available (session ready)")
            return True
        except RuntimeError:
            # Not ready — not a proxy capability issue, likely setup timing
            _LOGGER.debug("Proxy health check skipped (session not ready yet)")
            return True  # Don't report as failure since it's not a proxy issue

    def _set_token(self, access_token: str) -> None:
        if self._api is not None:
            # TODO: Replace private API access with public methods once tesla-fleet-api v2+ provides them
            self._api._access_token = access_token

    async def _dispatch_command(  # noqa: C901
        self, endpoint: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Route a command endpoint to its typed VehicleFleet method.

        Falls back to a raw POST for any endpoint not explicitly listed.
        """
        v = self._vehicle
        a = self._api
        if v is None:
            raise RuntimeError("Vehicle not initialized in _dispatch_command")
        if a is None:
            raise RuntimeError("API not initialized in _dispatch_command")
        match endpoint:
            case "actuate_trunk":
                which_trunk = body.get("which_trunk")
                if which_trunk is None:
                    raise HomeAssistantError("Missing which_trunk in actuate_trunk")
                return cast(dict[str, Any], await v.actuate_trunk(which_trunk))
            case "adjust_volume":
                volume = body.get("volume")
                if volume is None:
                    raise HomeAssistantError("Missing volume in adjust_volume")
                return cast(dict[str, Any], await v.adjust_volume(volume))
            case "auto_conditioning_start":
                return cast(dict[str, Any], await v.auto_conditioning_start())
            case "auto_conditioning_stop":
                return cast(dict[str, Any], await v.auto_conditioning_stop())
            case "cancel_software_update":
                return cast(dict[str, Any], await v.cancel_software_update())
            case "charge_max_range":
                return cast(dict[str, Any], await v.charge_max_range())
            case "charge_port_door_close":
                return cast(dict[str, Any], await v.charge_port_door_close())
            case "charge_port_door_open":
                return cast(dict[str, Any], await v.charge_port_door_open())
            case "charge_standard":
                return cast(dict[str, Any], await v.charge_standard())
            case "charge_start":
                return cast(dict[str, Any], await v.charge_start())
            case "charge_stop":
                return cast(dict[str, Any], await v.charge_stop())
            case "door_lock":
                return cast(dict[str, Any], await v.door_lock())
            case "door_unlock":
                return cast(dict[str, Any], await v.door_unlock())
            case "flash_lights":
                return cast(dict[str, Any], await v.flash_lights())
            case "honk_horn":
                return cast(dict[str, Any], await v.honk_horn())
            case "media_next_fav":
                return cast(dict[str, Any], await v.media_next_fav())
            case "media_next_track":
                return cast(dict[str, Any], await v.media_next_track())
            case "media_prev_fav":
                return cast(dict[str, Any], await v.media_prev_fav())
            case "media_prev_track":
                return cast(dict[str, Any], await v.media_prev_track())
            case "media_toggle_playback":
                return cast(dict[str, Any], await v.media_toggle_playback())
            case "media_volume_down":
                return cast(dict[str, Any], await v.media_volume_down())
            case "navigation_gps_request":
                lat = body.get("lat")
                lon = body.get("lon")
                if lat is None or lon is None:
                    raise HomeAssistantError("Missing lat/lon in navigation_gps_request")
                return cast(
                    dict[str, Any],
                    await v.navigation_gps_request(lat, lon, body.get("order")),
                )
            case "remote_boombox":
                sound = body.get("sound", body.get("sound_id", 0))
                return cast(dict[str, Any], await v.remote_boombox(sound))
            case (
                "remote_auto_seat_climate_request"
                | "remote_auto_steering_wheel_heat_climate_request"
                | "remote_seat_cooler_request"
                | "remote_seat_heater_request"
                | "remote_steering_wheel_heat_level_request"
                | "remote_steering_wheel_heater_request"
            ):
                # The signed Vehicle Command Protocol path (tesla-fleet-api's
                # wrapper methods) rejects these with InvalidRequest/InvalidCommand
                # on some vehicles — send plain JSON through the proxy's REST
                # passthrough instead, using Tesla's documented wire-format values.
                return cast(
                    dict[str, Any],
                    await a._request(
                        Method.POST,
                        f"{FLEET_API_VERSION}/vehicles/{self._vin}/command/{endpoint}",
                        json=body,
                    ),
                )
            case "remote_start_drive":
                return cast(dict[str, Any], await v.remote_start_drive())
            case "schedule_software_update":
                return cast(
                    dict[str, Any],
                    await v.schedule_software_update(body["offset_sec"]),
                )
            case "set_bioweapon_mode":
                return cast(
                    dict[str, Any],
                    await v.set_bioweapon_mode(body["on"], body.get("manual_override", False)),
                )
            case "set_cabin_overheat_protection":
                return cast(
                    dict[str, Any],
                    await v.set_cabin_overheat_protection(body["on"], body.get("fan_only", False)),
                )
            case "set_charge_limit":
                return cast(dict[str, Any], await v.set_charge_limit(body["percent"]))
            case "set_charging_amps":
                return cast(dict[str, Any], await v.set_charging_amps(body["charging_amps"]))
            case "set_climate_keeper_mode":
                return cast(
                    dict[str, Any],
                    await v.set_climate_keeper_mode(body["climate_keeper_mode"]),
                )
            case "set_cop_temp":
                return cast(dict[str, Any], await v.set_cop_temp(body["cop_temp"]))
            case "set_preconditioning_max":
                return cast(
                    dict[str, Any],
                    await v.set_preconditioning_max(body["on"], body.get("manual_override", False)),
                )
            case "set_scheduled_charging":
                return cast(
                    dict[str, Any],
                    await v.set_scheduled_charging(body["enable"], body["time"]),
                )
            case "set_sentry_mode":
                return cast(dict[str, Any], await v.set_sentry_mode(body["on"]))
            case "set_temps":
                return cast(
                    dict[str, Any],
                    await v.set_temps(body["driver_temp"], body["passenger_temp"]),
                )
            case "set_valet_mode":
                return cast(
                    dict[str, Any],
                    await v.set_valet_mode(body["on"], body.get("password")),
                )
            case "sun_roof_control":
                return cast(dict[str, Any], await v.sun_roof_control(body["state"]))
            case "trigger_homelink":
                return cast(
                    dict[str, Any],
                    await v.trigger_homelink(body.get("token"), body.get("lat"), body.get("lon")),
                )
            case "window_control":
                return cast(
                    dict[str, Any],
                    await v.window_control(body["command"], body.get("lat"), body.get("lon")),
                )
            case _:
                # TODO: Use public API methods once tesla-fleet-api exposes command endpoints
                return cast(
                    dict[str, Any],
                    await a._request(
                        Method.POST,
                        f"{FLEET_API_VERSION}/vehicles/{self._vin}/command/{endpoint}",
                        json=body,
                    ),
                )

    async def send_command(
        self,
        endpoint: str,
        body: dict[str, Any] | None,
        access_token: str,
    ) -> dict:
        """POST to proxy: /api/1/vehicles/{VIN}/command/{endpoint}.

        Raises HomeAssistantError on failure.
        """
        self._check_ready()
        self._set_token(access_token)
        try:
            data = await self._dispatch_command(endpoint, body or {})
        except TeslaFleetError as err:
            raise _map_fleet_exception(err) from err
        except aiohttp.ClientError as err:
            if "Session is closed" in str(err):
                raise RuntimeError("Integration not ready — please wait and try again") from err
            raise
        _check_command_result(data)
        return data

    async def wake_vehicle(self, access_token: str) -> dict:
        """POST wake_up through the local proxy's vehicle wake endpoint."""
        self._check_ready()
        self._set_token(access_token)
        try:
            data = cast(dict[str, Any], await self._vehicle.wake_up())  # type: ignore[union-attr]
        except TeslaFleetError as err:
            raise _map_fleet_exception(err) from err
        _check_command_result(data)
        return data

    async def proxy_fleet_telemetry_get(self, access_token: str) -> dict:
        """GET fleet_telemetry_config through vehicle-command proxy."""
        self._check_ready()
        self._set_token(access_token)
        try:
            return cast(
                dict[str, Any],
                await self._vehicle.fleet_telemetry_config_get(),  # type: ignore[union-attr]
            )
        except TeslaFleetError as err:
            raise _map_fleet_exception(err) from err

    async def proxy_fleet_telemetry_set(
        self,
        config: dict[str, Any],
        access_token: str,
    ) -> dict:
        """POST fleet_telemetry_config through the vehicle-command proxy."""
        self._check_ready()
        self._set_token(access_token)
        try:
            return cast(
                dict[str, Any],
                await self._vehicle.fleet_telemetry_config_create(  # type: ignore[union-attr]
                    {"vins": [self._vin], "config": config}
                ),
            )
        except TeslaFleetError as err:
            raise _map_fleet_exception(err) from err

    async def proxy_fleet_telemetry_delete(self, access_token: str) -> dict:
        """DELETE fleet_telemetry_config through vehicle-command proxy."""
        self._check_ready()
        self._set_token(access_token)
        try:
            return cast(
                dict[str, Any],
                await self._vehicle.fleet_telemetry_config_delete(),  # type: ignore[union-attr]
            )
        except TeslaFleetError as err:
            raise _map_fleet_exception(err) from err

    async def proxy_fleet_telemetry_errors_get(self, access_token: str) -> dict:
        """GET recent fleet_telemetry_errors through vehicle-command proxy."""
        self._check_ready()
        self._set_token(access_token)
        try:
            # TODO: Use public API methods once tesla-fleet-api exposes telemetry endpoints
            return cast(
                dict[str, Any],
                await self._api._request(  # type: ignore[union-attr]
                    Method.GET,
                    f"{FLEET_API_VERSION}/vehicles/{self._vin}/fleet_telemetry_errors",
                ),
            )
        except TeslaFleetError as err:
            raise _map_fleet_exception(err) from err
