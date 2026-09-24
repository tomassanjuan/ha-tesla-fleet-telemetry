"""Diagnostics support for Tesla Fleet Telemetry."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_DOMAIN,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_USERNAME,
    CONF_PROXY_URL,
    CONF_TELEMETRY_SERVER_HOSTNAME,
    CONF_VIN,
)

_REDACT_KEYS = {
    "authorization",
    "Authorization",
    "auth",
    "bearer",
    "cookie",
    "Cookie",
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_DOMAIN,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_USERNAME,
    CONF_PROXY_URL,
    CONF_TELEMETRY_SERVER_HOSTNAME,
    CONF_VIN,
    "access_token",
    "accessToken",
    "headers",
    "lat",
    "latitude",
    "lon",
    "longitude",
    "private_key",
    "password",
    "refresh_token",
    "refreshToken",
    "secret",
    "token",
    "vin",
}

_SENSITIVE_TELEMETRY_FIELDS = {
    "Connectivity",
    "DestinationLocation",
    "DestinationName",
    "GpsHeading",
    "GpsState",
    "Latitude",
    "Location",
    "Longitude",
    "OriginLocation",
    "RouteLine",
}


def _redact_push_result(result: object) -> object:
    """Return a diagnostics-safe fleet telemetry push result."""
    if not isinstance(result, dict):
        return result
    return async_redact_data(result, _REDACT_KEYS | {"response"})


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = getattr(config_entry, "runtime_data", None)

    telemetry_fields = {}
    policy_state_diag: dict[str, Any] = {}
    if entry_data is not None:
        values = entry_data.coordinator.data
        if isinstance(values, dict):
            telemetry_fields = async_redact_data(dict(values), _SENSITIVE_TELEMETRY_FIELDS)
        policy_state = entry_data.policy_state
        policy_state_diag = {
            "last_push_dt": policy_state.last_push_dt.isoformat()
            if policy_state.last_push_dt
            else None,
            "last_result": _redact_push_result(policy_state.last_result),
            "rate_limited": policy_state.rate_limited_until > asyncio.get_running_loop().time(),
        }

    return {
        "config_entry": async_redact_data(dict(config_entry.data), _REDACT_KEYS),
        "options": async_redact_data(
            dict(config_entry.options),
            _REDACT_KEYS | {"telemetry_server_ca"},
        ),
        "telemetry_fields": telemetry_fields,
        "policy_state": policy_state_diag,
    }
