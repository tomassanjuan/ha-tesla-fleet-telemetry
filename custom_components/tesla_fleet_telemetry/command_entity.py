"""Base class for entities that send vehicle commands."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import WAKE_UP_RETRY_DELAY_SECONDS
from .coordinator import TelemetryCoordinator
from .device import build_device_info
from .oauth2 import TeslaOAuth2Implementation, async_get_valid_access_token
from .proxy_client import RateLimitedError, TeslaProxyClient, TokenRevokedError

_LOGGER = logging.getLogger(__name__)

_REAUTH_MESSAGE = (
    "Tesla authentication failed — re-authentication required. "
    "Check the integration panel for a reauth prompt."
)

# Error substrings that indicate the vehicle is asleep or unreachable.
_ASLEEP_MARKERS = ("vehicle unavailable", "vehicle is offline", "vehicle is asleep")
_VEHICLE_STATE_REJECTIONS = (
    "vehicle must be parked",
    "vehicle must be in park",
    "vehicle is in gear",
    "vehicle is moving",
    "command not allowed in current vehicle state",
)


class CommandEntity(CoordinatorEntity[TelemetryCoordinator]):
    """Base for entities that issue vehicle commands through the proxy."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _state_fields: tuple[str, ...] = ()

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        vin: str,
        proxy_client: TeslaProxyClient,
        implementation: TeslaOAuth2Implementation,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._proxy_client = proxy_client
        self._implementation = implementation
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.coordinator, self._vin)

    @property
    def available(self) -> bool:
        """Available once the coordinator is healthy and backing state exists."""
        if not self.coordinator.last_update_success:
            return False
        if not self._proxy_client.ready:
            return False
        if not self._state_fields:
            return True
        return any(self.coordinator.get_field(field) is not None for field in self._state_fields)

    async def _get_token(self) -> str:
        """Return a valid access token, refreshing if needed.

        Triggers reauth and raises ServiceValidationError if the token is revoked.
        """
        try:
            access_token, _ = await async_get_valid_access_token(
                self.hass, self._entry, self._implementation
            )
        except ConfigEntryAuthFailed:
            self._entry.async_start_reauth(self.hass)
            raise ServiceValidationError(_REAUTH_MESSAGE) from None
        return access_token

    async def _send_command(  # noqa: C901
        self,
        endpoint: str,
        body: dict[str, Any] | None = None,
        *,
        _retrying: bool = False,
    ) -> dict:
        """Send a command; raises ServiceValidationError on rate-limit (shows UI banner).

        If the vehicle appears to be asleep and this is not already a retry, the method
        automatically sends a wake_up, waits WAKE_UP_RETRY_DELAY_SECONDS, then retries
        the original command once.
        """
        access_token = await self._get_token()
        try:
            if endpoint == "wake_up":
                return await self._proxy_client.wake_vehicle(access_token)
            return await self._proxy_client.send_command(endpoint, body, access_token)
        except RateLimitedError as err:
            _LOGGER.warning(
                "Tesla API rate limited for %s (%s): retry after %ss",
                self._vin,
                endpoint,
                err.retry_after,
            )
            raise ServiceValidationError(
                f"Tesla rate limited — retry after {err.retry_after}s"
            ) from err
        except TokenRevokedError as err:
            self._entry.async_start_reauth(self.hass)
            raise ServiceValidationError(_REAUTH_MESSAGE) from err
        except HomeAssistantError as err:
            msg = str(err).lower()
            # Auto-wake-and-retry when the vehicle is asleep, but only once.
            if (
                not _retrying
                and endpoint != "wake_up"
                and any(marker in msg for marker in _ASLEEP_MARKERS)
            ):
                _LOGGER.info(
                    "Vehicle %s appears asleep; waking up before retrying %s",
                    self._vin,
                    endpoint,
                )
                try:
                    await self._proxy_client.wake_vehicle(access_token)
                except Exception as e:  # noqa: S110
                    _LOGGER.debug("Wake-up command failed for %s: %s", self._vin, e)
                await asyncio.sleep(WAKE_UP_RETRY_DELAY_SECONDS)
                return await self._send_command(endpoint, body, _retrying=True)
            if "public key" in msg and "not been paired" in msg:
                raise ServiceValidationError(
                    "Vehicle rejected the command: virtual key not paired. "
                    "Open the Tesla app and pair the key at your proxy's pairing URL."
                ) from err
            if any(marker in msg for marker in _VEHICLE_STATE_REJECTIONS):
                raise ServiceValidationError(str(err)) from err
            raise
        except (TimeoutError, OSError) as err:
            if isinstance(err, ConnectionRefusedError):
                raise ServiceValidationError("Proxy is offline — connection refused") from err
            raise ServiceValidationError(
                str(err) or f"Proxy unreachable ({type(err).__name__})"
            ) from err
        except RuntimeError as err:
            msg = str(err)
            # "Integration not ready" should be a transient error with a user-friendly message
            if "Integration not ready" in msg:
                raise ServiceValidationError(msg) from err
            raise HomeAssistantError(msg) from err
        except Exception as err:
            msg = str(err)
            # Remove redundant wrapping from nested exceptions
            if msg.startswith("Error: "):
                msg = msg[7:]
            raise HomeAssistantError(msg) from err
