"""Application credentials support for Tesla Fleet Telemetry."""

from __future__ import annotations

from homeassistant.components.application_credentials import (
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import OAUTH_AUTHORIZE_URL, OAUTH_TOKEN_URL
from .oauth2 import TeslaOAuth2Implementation


async def async_get_auth_implementation(
    hass: HomeAssistant,
    auth_domain: str,
    credential: ClientCredential,
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return the Tesla OAuth2 implementation for stored app credentials."""
    return TeslaOAuth2Implementation(
        hass,
        auth_domain,
        credential.client_id,
        credential.client_secret,
    )


async def async_get_authorization_server(
    hass: HomeAssistant,
) -> AuthorizationServer:
    """Return Tesla's OAuth2 authorization server."""
    return AuthorizationServer(
        authorize_url=OAUTH_AUTHORIZE_URL,
        token_url=OAUTH_TOKEN_URL,
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return placeholders for the application credentials dialog."""
    return {"console_url": "https://developer.tesla.com/"}
