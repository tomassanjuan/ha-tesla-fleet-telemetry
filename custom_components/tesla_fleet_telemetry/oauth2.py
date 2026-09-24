"""OAuth2 helpers for Tesla Fleet API using Home Assistant native OAuth flow."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow
from tesla_fleet_api.const import Method, Scope
from tesla_fleet_api.exceptions import Forbidden, InvalidRequest, TeslaFleetError
from tesla_fleet_api.tesla.fleet import TeslaFleetApi

from .const import (
    DOMAIN,
    FLEET_API_BASE,
    FLEET_API_VERSION,
    OAUTH_AUTHORIZE_URL,
    OAUTH_SCOPES,
    OAUTH_TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

CONF_TOKEN = "token"
_OAUTH_SESSIONS_KEY = f"{DOMAIN}_oauth_sessions"

# Scopes used for partner (client_credentials) token requests.
_PARTNER_SCOPES: list[Scope] = [
    Scope.OPENID,
    Scope.OFFLINE_ACCESS,
    Scope.USER_DATA,
    Scope.VEHICLE_DEVICE_DATA,
    Scope.VEHICLE_LOCATION,
    Scope.VEHICLE_CMDS,
    Scope.VEHICLE_CHARGING_CMDS,
]


class PartnerDomainError(HomeAssistantError):
    """Tesla Fleet API rejected a partner domain registration request (4xx)."""


class TeslaOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce):
    """Tesla OAuth2 implementation based on Home Assistant local OAuth helper.

    Tesla's Fleet API OAuth requires PKCE (S256), so this uses HA's
    PKCE-capable implementation rather than plain LocalOAuth2Implementation.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        implementation_domain: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        super().__init__(
            hass,
            implementation_domain,
            client_id,
            OAUTH_AUTHORIZE_URL,
            OAUTH_TOKEN_URL,
            client_secret=client_secret,
        )

    @property
    def name(self) -> str:
        return "Tesla Fleet API"

    @property
    def redirect_uri(self) -> str:
        """Use Home Assistant redirect helper URI."""
        return "https://my.home-assistant.io/redirect/oauth"

    @property
    def extra_authorize_data(self) -> dict:
        return {
            **super().extra_authorize_data,
            "scope": OAUTH_SCOPES,
            "audience": FLEET_API_BASE,
            "prompt_missing_scopes": "true",
            "require_requested_scopes": "true",
            "show_keypair_step": "true",
        }

    @property
    def extra_token_resolve_data(self) -> dict:
        return {
            **super().extra_token_resolve_data,
            "audience": FLEET_API_BASE,
        }


def token_from_entry_data(entry_data: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract OAuth token from entry data."""
    token = entry_data.get(CONF_TOKEN)
    if isinstance(token, Mapping) and token.get("access_token"):
        return dict(token)
    return None


def _get_oauth_session(
    hass: HomeAssistant,
    entry: ConfigEntry,
    implementation: config_entry_oauth2_flow.AbstractOAuth2Implementation,
) -> config_entry_oauth2_flow.OAuth2Session:
    """Return one shared OAuth2Session per config entry.

    OAuth2Session serializes refreshes with a per-instance lock. Creating a new
    session per call let concurrent callers (e.g. two commands, or a command and
    a telemetry config push) refresh the same single-use Tesla refresh token in
    parallel; the loser got a 401 and forced a spurious reauth.
    """
    sessions: dict[str, config_entry_oauth2_flow.OAuth2Session] = hass.data.setdefault(
        _OAUTH_SESSIONS_KEY, {}
    )
    session = sessions.get(entry.entry_id)
    if (
        session is None
        or session.config_entry is not entry
        or session.implementation is not implementation
    ):
        session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
        sessions[entry.entry_id] = session
    return session


def async_drop_oauth_session(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the cached OAuth2Session for an unloaded entry."""
    hass.data.get(_OAUTH_SESSIONS_KEY, {}).pop(entry.entry_id, None)


async def async_get_valid_access_token(
    hass: HomeAssistant,
    entry: ConfigEntry,
    implementation: config_entry_oauth2_flow.AbstractOAuth2Implementation,
) -> tuple[str, dict[str, Any]]:
    """Ensure a valid access token and return (access_token, current_token)."""
    token = token_from_entry_data(entry.data)
    if token is None:
        raise ConfigEntryAuthFailed("No OAuth token found in config entry")

    session = _get_oauth_session(hass, entry, implementation)

    try:
        await session.async_ensure_token_valid()
    except aiohttp.ClientResponseError as err:
        if err.status in (400, 401, 403):
            err_str = str(err)[:200]
            raise ConfigEntryAuthFailed(f"Tesla token refresh failed: {err_str}") from err
        err_str = str(err)[:200]
        raise HomeAssistantError(
            f"Tesla token refresh failed (HTTP {err.status}): {err_str}"
        ) from err
    except Exception as err:
        err_str = str(err)[:200]
        raise HomeAssistantError(f"Tesla token refresh failed: {err_str}") from err

    current_token = token_from_entry_data(entry.data) or token
    access_token = str(current_token.get("access_token", ""))

    if not access_token:
        raise ConfigEntryAuthFailed("OAuth token does not contain access_token")

    return access_token, current_token


def _make_api(
    session: aiohttp.ClientSession,
    server: str,
    access_token: str | None = None,
) -> TeslaFleetApi:
    """Create a minimal TeslaFleetApi for partner/vehicle-list operations."""
    return TeslaFleetApi(
        session=session,
        server=server,
        access_token=access_token,
        charging_scope=False,
        energy_scope=False,
        user_scope=False,
        vehicle_scope=False,
        partner_scope=True,
    )


async def async_fetch_partner_token(
    client_id: str,
    client_secret: str,
    audience: str = FLEET_API_BASE,
) -> str:
    """Obtain a partner (client_credentials) access token from Tesla."""
    async with aiohttp.ClientSession() as session:
        api = _make_api(session, audience)
        try:
            result = await api.partner_login(client_id, client_secret, scopes=_PARTNER_SCOPES)
        except TeslaFleetError as err:
            raise HomeAssistantError(f"Partner token request failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError(f"Partner token request failed: {err}") from err

    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise HomeAssistantError("Partner token response missing access_token")
    return token


async def async_register_partner_domain(
    partner_token: str,
    domain: str,
    api_base: str = FLEET_API_BASE,
) -> dict[str, Any]:
    """Register the partner domain with Tesla Fleet API.

    Raises PartnerDomainError on 4xx (bad domain, not matching allowed origins, etc.)
    so the caller can surface this as a form validation error.
    Raises HomeAssistantError on 5xx or unexpected errors.
    """
    async with aiohttp.ClientSession() as session:
        api = _make_api(session, api_base, access_token=partner_token)
        try:
            result = await api.partner.register(domain)
            _LOGGER.debug("Partner domain registration response for %s: %s", domain, result)
            return cast(dict[str, Any], result)
        except (Forbidden, InvalidRequest) as err:
            raise PartnerDomainError(f"Partner domain registration failed: {err}") from err
        except TeslaFleetError as err:
            if err.status is not None and 400 <= err.status < 500:
                raise PartnerDomainError(
                    f"Partner domain registration failed ({err.status}): {err}"
                ) from err
            raise HomeAssistantError(f"Partner domain registration server error: {err}") from err


async def async_check_partner_public_key(
    partner_token: str,
    domain: str,
    api_base: str = FLEET_API_BASE,
) -> dict[str, Any]:
    """Verify the public key is registered for the domain."""
    async with aiohttp.ClientSession() as session:
        api = _make_api(session, api_base, access_token=partner_token)
        try:
            result = await api.partner.public_key(domain=domain)
            _LOGGER.debug("Partner public key for domain %s: %s", domain, result)
            return cast(dict[str, Any], result)
        except TeslaFleetError as err:
            raise HomeAssistantError(f"Partner public key check failed: {err}") from err


async def async_fetch_available_vins(access_token: str) -> list[str]:
    """Fetch available VINs for the authenticated account from Fleet API."""
    async with aiohttp.ClientSession() as session:
        api = TeslaFleetApi(
            session=session,
            server=FLEET_API_BASE,
            access_token=access_token,
            charging_scope=False,
            energy_scope=False,
            partner_scope=False,
            user_scope=False,
            vehicle_scope=False,
        )
        try:
            payload = await api._request(Method.GET, f"{FLEET_API_VERSION}/vehicles")
        except TeslaFleetError as err:
            raise HomeAssistantError(f"VIN fetch failed: {err}") from err

    vins: list[str] = []
    response = payload.get("response", payload)
    if isinstance(response, list):
        for vehicle in response:
            if not isinstance(vehicle, Mapping):
                continue
            vin = vehicle.get("vin")
            if isinstance(vin, str) and vin.strip():
                vins.append(vin.strip().upper())

    return list(dict.fromkeys(vins))
