"""Config flow for Tesla Fleet Telemetry integration."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Any, cast
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from tesla_fleet_api.exceptions import TeslaFleetError

from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_CONNECTION_TIMEOUT,
    CONF_DOMAIN,
    CONF_FLEET_API_BASE,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_TLS,
    CONF_MQTT_TOPIC_BASE,
    CONF_MQTT_USERNAME,
    CONF_PROXY_URL,
    CONF_PROXY_VERIFY_SSL,
    CONF_TELEMETRY_MODE,
    CONF_TELEMETRY_SERVER_CA,
    CONF_TELEMETRY_SERVER_HOSTNAME,
    CONF_TELEMETRY_SERVER_PORT,
    CONF_VIN,
    DEFAULT_CONNECTION_CHECK_TIMEOUT,
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_MQTT_TOPIC_BASE,
    DEFAULT_TELEMETRY_MODE,
    DEFAULT_TELEMETRY_SERVER_ENDPOINT,
    DOMAIN,
    FLEET_API_BASE,
    TELEMETRY_MODE_OPTIONS,
)
from .oauth2 import (
    PartnerDomainError,
    TeslaAuthError,
    TeslaOAuth2Implementation,
    async_check_partner_public_key,
    async_fetch_available_vins,
    async_fetch_partner_token,
    async_get_valid_access_token,
    async_register_partner_domain,
)
from .proxy_client import PROXY_REQUEST_ERRORS, TeslaProxyClient
from .utils import (
    async_fetch_server_ca,
    extract_first_fields_config,
    normalize_pem_cert_bundle,
)

_LOGGER = logging.getLogger(__name__)

# Failures a Tesla Fleet API call can surface: wrapped HomeAssistantErrors,
# unwrapped library errors, and transport failures.
_TESLA_HTTP_ERRORS: tuple[type[BaseException], ...] = (
    HomeAssistantError,
    TeslaFleetError,
    aiohttp.ClientError,
    TimeoutError,
)


async def _async_verify_mqtt_reachable(
    hass: HomeAssistant,
    host: str,
    port: int,
    timeout: int | None = None,
) -> tuple[bool, str | None]:
    """Check if MQTT broker is reachable. Returns (is_reachable, error_message)."""
    import socket  # noqa: PLC0415

    _timeout = timeout if timeout is not None else DEFAULT_CONNECTION_CHECK_TIMEOUT

    def _check_mqtt() -> tuple[bool, str | None]:
        try:
            sock = socket.create_connection((host, port), timeout=_timeout)
            sock.close()
            return True, None
        except TimeoutError:
            return False, "mqtt_timeout"
        except OSError as e:
            _LOGGER.debug("MQTT connection failed for %s:%s: %s", host, port, e)
            return False, "mqtt_unreachable"

    return cast(tuple[bool, str | None], await hass.async_add_executor_job(_check_mqtt))


async def _async_verify_proxy_reachable(
    hass: HomeAssistant,
    proxy_url: str,
    timeout: int | None = None,
    verify_ssl: bool = False,
) -> tuple[bool, str | None]:
    """Check if proxy is reachable. Returns (is_reachable, error_message)."""
    import aiohttp  # noqa: PLC0415

    _timeout = timeout if timeout is not None else DEFAULT_CONNECTION_CHECK_TIMEOUT
    proxy_url = proxy_url.strip()
    if not proxy_url:
        return False, "proxy_empty"

    parsed = urlparse(proxy_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return False, "proxy_invalid_url"
    if parsed.username or parsed.password:
        return False, "proxy_invalid_url"

    try:
        session = async_get_clientsession(hass, verify_ssl=verify_ssl)
        async with session.get(
            proxy_url,
            timeout=aiohttp.ClientTimeout(total=_timeout),
        ):
            # Proxy is reachable if we get any response, even 404
            return True, None
    except aiohttp.ClientConnectorCertificateError:
        _LOGGER.debug("Proxy certificate verification failed for %s", proxy_url)
        return False, "proxy_cert_invalid"
    except aiohttp.InvalidURL:
        _LOGGER.debug("Invalid proxy URL: %s", proxy_url)
        return False, "proxy_invalid_url"
    except aiohttp.ClientConnectorError:
        _LOGGER.debug("Proxy connection failed for %s", proxy_url)
        return False, "proxy_unreachable"
    except TimeoutError:
        _LOGGER.debug("Proxy timeout for %s", proxy_url)
        return False, "proxy_timeout"
    except (aiohttp.ClientError, OSError) as e:
        _LOGGER.debug("Proxy check failed for %s: %s", proxy_url, e)
        return False, "proxy_unreachable"


async def _async_verify_connection_endpoints(
    hass: HomeAssistant,
    mqtt_host: str,
    mqtt_port: int,
    proxy_url: str,
    timeout: int | None = None,
    verify_ssl: bool = False,
) -> dict[str, str]:
    """Check MQTT and proxy endpoints in parallel; return form errors."""
    mqtt_result, proxy_result = await asyncio.gather(
        _async_verify_mqtt_reachable(hass, mqtt_host, mqtt_port, timeout=timeout),
        _async_verify_proxy_reachable(hass, proxy_url, timeout=timeout, verify_ssl=verify_ssl),
        return_exceptions=True,
    )
    errors: dict[str, str] = {}
    if isinstance(mqtt_result, BaseException):
        _LOGGER.debug("MQTT verification error: %s", mqtt_result)
        errors[CONF_MQTT_ENDPOINT] = "mqtt_unreachable"
    else:
        mqtt_ok, mqtt_err = mqtt_result
        if not mqtt_ok:
            errors[CONF_MQTT_ENDPOINT] = mqtt_err or "mqtt_unreachable"

    if isinstance(proxy_result, BaseException):
        _LOGGER.debug("Proxy verification error: %s", proxy_result)
        errors[CONF_PROXY_URL] = "proxy_unreachable"
    else:
        proxy_ok, proxy_err = proxy_result
        if not proxy_ok:
            errors[CONF_PROXY_URL] = proxy_err or "proxy_unreachable"
    return errors


CONF_MQTT_ENDPOINT = "mqtt_endpoint"
CONF_TELEMETRY_SERVER_ENDPOINT = "telemetry_server_endpoint"
DEFAULT_HTTPS_PORT = 443


async def _maybe_autofetch_ca(
    hass: HomeAssistant,
    user_input: dict[str, Any],
    errors: dict[str, str],
) -> dict[str, Any]:
    """Auto-fetch the server CA cert; return updated user_input with CA filled in."""
    endpoint = str(user_input.get(CONF_TELEMETRY_SERVER_ENDPOINT, "")).strip()
    try:
        hostname, port = _parse_telemetry_endpoint(endpoint)
        hostname = hostname.strip().rstrip(".")
    except ValueError:
        return user_input  # Let the normal validator handle the bad format.

    if not hostname or "." not in hostname or not 1 <= port <= 65535:
        return user_input  # Let the normal validator raise the correct error.

    try:
        pem, hostname_ok = await async_fetch_server_ca(hass, hostname, port)
    except (OSError, ValueError) as err:
        _LOGGER.debug("CA auto-fetch failed for %s:%s: %s", hostname, port, err)
        errors[CONF_TELEMETRY_SERVER_CA] = "ca_fetch_failed"
        return user_input

    updated = dict(user_input)
    updated[CONF_TELEMETRY_SERVER_CA] = pem
    if not hostname_ok:
        _LOGGER.warning(
            "Auto-fetched CA cert from %s:%s but hostname validation failed — "
            "blocking configuration",
            hostname,
            port,
        )
        errors["base"] = "ca_hostname_mismatch"
    return updated


def _mqtt_endpoint_default(host: str, port: int) -> str:
    """Build the user-facing MQTT endpoint string."""
    return f"{host}:{port}"


def _parse_mqtt_endpoint(endpoint: str) -> tuple[str, int]:
    """Parse a host:port MQTT endpoint string."""
    host, separator, port_text = endpoint.rpartition(":")
    if separator != ":" or not host.strip():
        raise ValueError("missing_host_or_port")

    try:
        port = int(port_text)
    except ValueError as err:
        raise ValueError("invalid_port") from err

    if not 1 <= port <= 65535:
        raise ValueError("invalid_port")

    return host.strip(), port


def _parse_telemetry_endpoint(endpoint: str) -> tuple[str, int]:
    """Parse a telemetry endpoint as URL, host:port, or HTTPS host."""
    endpoint = endpoint.strip()
    if not endpoint:
        raise ValueError("missing_host")

    if "://" in endpoint:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("invalid_hostname")
        try:
            port = parsed.port or DEFAULT_HTTPS_PORT
        except ValueError as err:
            raise ValueError("invalid_port") from err
        return parsed.hostname, port

    try:
        host, port = _parse_mqtt_endpoint(endpoint)
    except ValueError:
        if ":" in endpoint or "/" in endpoint or "@" in endpoint:
            raise
        return endpoint, DEFAULT_HTTPS_PORT
    return host, port


def _telemetry_config_override_requires_confirmation(
    candidate: dict[str, Any],
    existing_car_config: dict[str, Any] | None,
) -> bool:
    """Return True when existing config has fields and candidate's server settings differ."""
    if not isinstance(existing_car_config, dict):
        return False
    existing_fields = existing_car_config.get("fields")
    if not isinstance(existing_fields, dict) or not existing_fields:
        return False
    return any(
        candidate.get(key) != existing_car_config.get(existing_key)
        for key, existing_key in (
            (CONF_TELEMETRY_SERVER_HOSTNAME, "hostname"),
            (CONF_TELEMETRY_SERVER_PORT, "port"),
            (CONF_TELEMETRY_SERVER_CA, "ca"),
        )
    )


def _normalize_proxy_url(proxy_url: str) -> str:
    """Normalize the command proxy URL; the proxy is always HTTPS."""
    proxy_url = proxy_url.strip().rstrip("/")
    if proxy_url and "://" not in proxy_url:
        proxy_url = f"https://{proxy_url}"
    return proxy_url


def _display_proxy_url(proxy_url: str) -> str:
    """Return the user-facing proxy value without the implicit HTTPS scheme."""
    proxy_url = proxy_url.strip().rstrip("/")
    parsed = urlparse(proxy_url)
    if parsed.scheme == "https" and parsed.netloc and not parsed.path:
        return parsed.netloc
    return proxy_url


_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _mqtt_auth_schema(values: dict[str, Any]) -> dict[Any, Any]:
    """Optional MQTT credentials/TLS fields shared by the setup and options forms."""
    return {
        vol.Optional(CONF_MQTT_USERNAME, default=values.get(CONF_MQTT_USERNAME, "")): str,
        vol.Optional(
            CONF_MQTT_PASSWORD, default=values.get(CONF_MQTT_PASSWORD, "")
        ): _PASSWORD_SELECTOR,
        vol.Optional(CONF_MQTT_TLS, default=bool(values.get(CONF_MQTT_TLS, False))): bool,
    }


def _build_user_schema(
    mqtt_endpoint: str = "",
    proxy_url: str = "",
    telemetry_endpoint: str = DEFAULT_TELEMETRY_SERVER_ENDPOINT,
    telemetry_mode: str = DEFAULT_TELEMETRY_MODE,
    domain: str = "",
    previous: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the setup form schema; `previous` re-fills fields after a failed submit."""
    return vol.Schema(
        {
            vol.Optional(CONF_DOMAIN, default=domain): str,
            vol.Required(CONF_TELEMETRY_SERVER_ENDPOINT, default=telemetry_endpoint): str,
            vol.Required(CONF_TELEMETRY_MODE, default=telemetry_mode): vol.In(
                TELEMETRY_MODE_OPTIONS
            ),
            vol.Required(CONF_MQTT_ENDPOINT, default=mqtt_endpoint): str,
            **_mqtt_auth_schema(previous or {}),
            vol.Optional(CONF_MQTT_TOPIC_BASE, default=DEFAULT_MQTT_TOPIC_BASE): str,
            vol.Required(CONF_PROXY_URL, default=proxy_url): str,
            vol.Optional(
                CONF_PROXY_VERIFY_SSL,
                default=bool((previous or {}).get(CONF_PROXY_VERIFY_SSL, False)),
            ): bool,
        }
    )


def _build_options_schema(merged: dict[str, Any]) -> vol.Schema:
    """Build the options form schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_DOMAIN,
                default=merged.get(CONF_DOMAIN, ""),
            ): str,
            vol.Required(
                CONF_TELEMETRY_SERVER_ENDPOINT,
                default=(
                    f"{merged[CONF_TELEMETRY_SERVER_HOSTNAME]}:{merged[CONF_TELEMETRY_SERVER_PORT]}"
                    if merged.get(CONF_TELEMETRY_SERVER_HOSTNAME)
                    else DEFAULT_TELEMETRY_SERVER_ENDPOINT
                ),
            ): str,
            vol.Required(
                CONF_TELEMETRY_MODE,
                default=merged.get(CONF_TELEMETRY_MODE, DEFAULT_TELEMETRY_MODE),
            ): vol.In(TELEMETRY_MODE_OPTIONS),
            vol.Required(
                CONF_MQTT_ENDPOINT,
                default=(
                    _mqtt_endpoint_default(
                        merged[CONF_MQTT_HOST],
                        merged[CONF_MQTT_PORT],
                    )
                    if merged.get(CONF_MQTT_HOST)
                    else ""
                ),
            ): str,
            **_mqtt_auth_schema(merged),
            vol.Optional(
                CONF_MQTT_TOPIC_BASE,
                default=merged.get(CONF_MQTT_TOPIC_BASE, DEFAULT_MQTT_TOPIC_BASE),
            ): str,
            vol.Required(
                CONF_PROXY_URL,
                default=_display_proxy_url(merged.get(CONF_PROXY_URL, "")),
            ): str,
            vol.Optional(
                CONF_PROXY_VERIFY_SSL,
                default=bool(merged.get(CONF_PROXY_VERIFY_SSL, False)),
            ): bool,
            vol.Optional(
                CONF_CONNECTION_TIMEOUT,
                default=merged.get(CONF_CONNECTION_TIMEOUT, DEFAULT_CONNECTION_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=60)),
        }
    )


def _normalize_connection_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Replace the flow-only MQTT endpoint with stored host/port keys."""
    host, port = _parse_mqtt_endpoint(user_input[CONF_MQTT_ENDPOINT])
    normalized = dict(user_input)
    normalized.pop(CONF_MQTT_ENDPOINT, None)
    normalized[CONF_MQTT_HOST] = host
    normalized[CONF_MQTT_PORT] = port
    normalized[CONF_DOMAIN] = str(normalized.get(CONF_DOMAIN, "") or "").strip().lower()
    return normalized


async def _async_try_partner_registration(
    hass: HomeAssistant,
    client_id: str,
    client_secret: str,
    domain: str,
    api_base: str,
) -> tuple[str, str] | None:
    """Attempt Tesla partner account registration and public key verification.

    Returns (error_key, api_error_detail) when Tesla rejects the domain (4xx)
    so the caller can surface the exact API message in the form.
    Returns None on success or transient network/server errors (non-blocking).
    """
    _LOGGER.debug(
        "Attempting partner domain registration with Tesla for domain %s on %s", domain, api_base
    )
    try:
        # Partner tokens and registrations are per region: use the account's host.
        token = await async_fetch_partner_token(hass, client_id, client_secret, api_base)
        _LOGGER.debug("Partner token obtained for domain: %s", domain)
    except _TESLA_HTTP_ERRORS as err:
        _LOGGER.warning(
            "Could not obtain partner token for domain registration of %s: %s",
            domain,
            err,
        )
        return None

    try:
        await async_register_partner_domain(hass, token, domain, api_base)
        _LOGGER.info("Partner domain registered with Tesla: %s", domain)
    except PartnerDomainError as err:
        _LOGGER.warning(
            "Tesla partner domain registration rejected for %s: %s",
            domain,
            err,
        )
        return ("partner_register_failed", str(err))
    except _TESLA_HTTP_ERRORS as err:
        _LOGGER.warning(
            "Tesla partner domain registration failed (transient) for %s: %s",
            domain,
            err,
        )
        return None

    try:
        await async_check_partner_public_key(hass, token, domain, api_base)
        _LOGGER.info("Partner public key verified for domain: %s", domain)
    except _TESLA_HTTP_ERRORS as err:
        _LOGGER.warning(
            "Partner public key check failed for %s (registration may still be pending): %s",
            domain,
            err,
        )
    return None


def _normalize_telemetry_server_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Parse telemetry endpoint string and validate hostname/port."""
    normalized = dict(user_input)
    endpoint = str(normalized.pop(CONF_TELEMETRY_SERVER_ENDPOINT, "")).strip()
    try:
        hostname, port = _parse_telemetry_endpoint(endpoint)
        hostname = hostname.strip().rstrip(".")
    except ValueError as e:
        raise ValueError("invalid_hostname") from e

    if not hostname:
        raise ValueError("invalid_hostname")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("invalid_hostname")
    if "." not in hostname:
        raise ValueError("invalid_hostname")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid_port")
    ca = normalize_pem_cert_bundle(normalized.get(CONF_TELEMETRY_SERVER_CA, ""))

    normalized[CONF_TELEMETRY_SERVER_HOSTNAME] = hostname
    normalized[CONF_TELEMETRY_SERVER_PORT] = port
    normalized[CONF_TELEMETRY_SERVER_CA] = ca
    return normalized


def _validate_proxy_url(proxy_url: str, errors: dict[str, str]) -> str:
    """Validate the command proxy URL before any network request."""
    proxy_url = _normalize_proxy_url(proxy_url)
    parsed = urlparse(proxy_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        errors[CONF_PROXY_URL] = "proxy_invalid_url"
        return proxy_url
    return proxy_url


def _validate_telemetry_endpoint(endpoint: str, errors: dict[str, str]) -> tuple[str, int]:
    """Validate telemetry endpoint hostname and port. Returns (hostname, port)."""
    hostname = ""
    port = 0
    try:
        hostname, port = _parse_telemetry_endpoint(endpoint)
        hostname = hostname.strip().rstrip(".")
    except ValueError:
        errors[CONF_TELEMETRY_SERVER_ENDPOINT] = "invalid_hostname"
        return hostname, port

    if not hostname or "." not in hostname:
        errors[CONF_TELEMETRY_SERVER_ENDPOINT] = "invalid_hostname"
    else:
        try:
            ipaddress.ip_address(hostname)
            errors[CONF_TELEMETRY_SERVER_ENDPOINT] = "invalid_hostname"
        except ValueError:
            pass  # Not an IP — fine.

    if not errors.get(CONF_TELEMETRY_SERVER_ENDPOINT) and (
        not isinstance(port, int) or not 1 <= port <= 65535
    ):
        errors[CONF_TELEMETRY_SERVER_ENDPOINT] = "invalid_port"

    return hostname, port


def _telemetry_endpoint_changed(current: dict[str, Any], user_input: dict[str, Any]) -> bool:
    """Return True when options point at a different telemetry server."""
    try:
        hostname, port = _parse_telemetry_endpoint(
            str(user_input.get(CONF_TELEMETRY_SERVER_ENDPOINT, "")).strip()
        )
    except ValueError:
        return False
    return hostname.strip().rstrip(".") != current.get(
        CONF_TELEMETRY_SERVER_HOSTNAME
    ) or port != current.get(CONF_TELEMETRY_SERVER_PORT)


class TeslaFleetTelemetryFlowHandler(  # type: ignore[call-arg]
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the Tesla Fleet Telemetry config flow."""

    DOMAIN = DOMAIN
    VERSION = 1
    reauth_entry: ConfigEntry | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> TeslaFleetTelemetryOptionsFlow:
        return TeslaFleetTelemetryOptionsFlow(config_entry)

    def __init__(self) -> None:
        super().__init__()
        self._user_input: dict[str, Any] = {}
        self._pending_oauth_data: dict[str, Any] | None = None
        self._available_vins: list[str] = []
        self._selected_vin: str | None = None
        self._existing_car_telemetry_config: dict[str, Any] | None = None
        self._pending_telemetry_input: dict[str, Any] | None = None
        self._fleet_api_base: str = FLEET_API_BASE

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: start OAuth so the Tesla fleet connection prompt appears only once."""
        return await super().async_step_user(user_input=None)

    async def _validate_connection_input(
        self,
        user_input: dict[str, Any],
        errors: dict[str, str],
    ) -> dict[str, Any] | None:
        """Validate MQTT, proxy, and telemetry endpoint. Returns normalized input or None."""
        try:
            normalized_input = _normalize_connection_input(user_input)
        except ValueError:
            errors[CONF_MQTT_ENDPOINT] = "invalid_mqtt_endpoint"
            return None

        proxy_url = _validate_proxy_url(user_input.get(CONF_PROXY_URL, ""), errors)
        telemetry_endpoint = str(user_input.get(CONF_TELEMETRY_SERVER_ENDPOINT, "")).strip()
        hostname, port = _validate_telemetry_endpoint(telemetry_endpoint, errors)
        if errors:
            return None

        normalized_input[CONF_PROXY_URL] = proxy_url
        normalized_input[CONF_TELEMETRY_SERVER_HOSTNAME] = hostname
        normalized_input[CONF_TELEMETRY_SERVER_PORT] = port

        errors.update(
            await _async_verify_connection_endpoints(
                self.hass,
                normalized_input[CONF_MQTT_HOST],
                normalized_input[CONF_MQTT_PORT],
                proxy_url,
                verify_ssl=bool(normalized_input.get(CONF_PROXY_VERIFY_SSL)),
            )
        )
        if errors:
            return None

        return normalized_input

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 (after OAuth): collect connection + telemetry server details, auto-fetch CA."""
        errors: dict[str, str] = {}
        if user_input is None:
            return self.async_show_form(
                step_id="connection",
                data_schema=_build_user_schema(
                    telemetry_mode=DEFAULT_TELEMETRY_MODE,
                ),
            )

        normalized_input = await self._validate_connection_input(user_input, errors)
        if normalized_input is None:
            return self.async_show_form(
                step_id="connection",
                data_schema=_build_user_schema(
                    mqtt_endpoint=user_input.get(
                        CONF_MQTT_ENDPOINT,
                        "",
                    ),
                    proxy_url=user_input.get(CONF_PROXY_URL, ""),
                    telemetry_endpoint=user_input.get(
                        CONF_TELEMETRY_SERVER_ENDPOINT,
                        DEFAULT_TELEMETRY_SERVER_ENDPOINT,
                    ),
                    telemetry_mode=user_input.get(
                        CONF_TELEMETRY_MODE,
                        DEFAULT_TELEMETRY_MODE,
                    ),
                    domain=user_input.get(CONF_DOMAIN, ""),
                    previous=user_input,
                ),
                errors=errors,
            )

        self._user_input = normalized_input
        hostname = normalized_input[CONF_TELEMETRY_SERVER_HOSTNAME]
        port = normalized_input[CONF_TELEMETRY_SERVER_PORT]
        proxy_url = normalized_input[CONF_PROXY_URL]

        # Auto-fetch CA certificate from the telemetry server (also verifies reachability).
        try:
            pem, hostname_ok = await async_fetch_server_ca(self.hass, hostname, port)
            ca = normalize_pem_cert_bundle(pem)
        except (OSError, ValueError) as err:
            _LOGGER.debug("CA auto-fetch failed for %s:%s: %s", hostname, port, err)
            return self.async_show_form(
                step_id="connection",
                data_schema=_build_user_schema(
                    mqtt_endpoint=user_input.get(CONF_MQTT_ENDPOINT, ""),
                    proxy_url=_display_proxy_url(proxy_url),
                    telemetry_endpoint=user_input.get(
                        CONF_TELEMETRY_SERVER_ENDPOINT,
                        DEFAULT_TELEMETRY_SERVER_ENDPOINT,
                    ),
                    telemetry_mode=user_input.get(
                        CONF_TELEMETRY_MODE,
                        DEFAULT_TELEMETRY_MODE,
                    ),
                    domain=normalized_input.get(CONF_DOMAIN, ""),
                    previous=normalized_input,
                ),
                errors={"base": "ca_fetch_failed"},
            )

        if not hostname_ok:
            _LOGGER.warning(
                "Auto-fetched CA from %s:%s but hostname validation failed",
                hostname,
                port,
            )
            return self.async_show_form(
                step_id="connection",
                data_schema=_build_user_schema(
                    mqtt_endpoint=user_input.get(CONF_MQTT_ENDPOINT, ""),
                    proxy_url=_display_proxy_url(proxy_url),
                    telemetry_endpoint=user_input.get(
                        CONF_TELEMETRY_SERVER_ENDPOINT,
                        DEFAULT_TELEMETRY_SERVER_ENDPOINT,
                    ),
                    telemetry_mode=user_input.get(
                        CONF_TELEMETRY_MODE,
                        DEFAULT_TELEMETRY_MODE,
                    ),
                    domain=normalized_input.get(CONF_DOMAIN, ""),
                    previous=normalized_input,
                ),
                errors={"base": "ca_hostname_mismatch"},
            )

        self._user_input[CONF_TELEMETRY_SERVER_CA] = ca
        return await self._async_finish_connection()

    async def _async_finish_connection(self) -> ConfigFlowResult:
        """Register partner domain when possible, then continue to VIN selection."""
        reg_result = await self._async_try_partner_registration_from_flow()
        if reg_result:
            reg_error_key, reg_error_detail = reg_result
            return self.async_show_form(
                step_id="connection",
                data_schema=_build_user_schema(
                    mqtt_endpoint=_mqtt_endpoint_default(
                        self._user_input[CONF_MQTT_HOST],
                        self._user_input[CONF_MQTT_PORT],
                    ),
                    proxy_url=_display_proxy_url(self._user_input.get(CONF_PROXY_URL, "")),
                    telemetry_endpoint=(
                        f"{self._user_input[CONF_TELEMETRY_SERVER_HOSTNAME]}:"
                        f"{self._user_input[CONF_TELEMETRY_SERVER_PORT]}"
                    ),
                    telemetry_mode=self._user_input.get(
                        CONF_TELEMETRY_MODE, DEFAULT_TELEMETRY_MODE
                    ),
                    domain=self._user_input.get(CONF_DOMAIN, ""),
                    previous=self._user_input,
                ),
                errors={CONF_DOMAIN: reg_error_key},
                description_placeholders={"partner_error": reg_error_detail},
            )
        return await self._async_proceed_to_vin_or_telemetry()

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle re-authentication without re-entering connection settings."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if self.reauth_entry is None:
            return self.async_abort(reason="reauth_not_possible")

        self._user_input = {
            CONF_MQTT_HOST: self.reauth_entry.data[CONF_MQTT_HOST],
            CONF_MQTT_PORT: self.reauth_entry.data[CONF_MQTT_PORT],
            CONF_PROXY_URL: self.reauth_entry.data[CONF_PROXY_URL],
            CONF_VIN: self.reauth_entry.data[CONF_VIN],
            CONF_TELEMETRY_SERVER_HOSTNAME: self.reauth_entry.data.get(
                CONF_TELEMETRY_SERVER_HOSTNAME,
                "",
            ),
            CONF_TELEMETRY_SERVER_PORT: self.reauth_entry.data[CONF_TELEMETRY_SERVER_PORT],
            CONF_TELEMETRY_SERVER_CA: self.reauth_entry.data.get(
                CONF_TELEMETRY_SERVER_CA,
                "",
            ),
        }
        if CONF_CLIENT_ID in self.reauth_entry.data:
            self._user_input[CONF_CLIENT_ID] = self.reauth_entry.data[CONF_CLIENT_ID]
        if CONF_CLIENT_SECRET in self.reauth_entry.data:
            self._user_input[CONF_CLIENT_SECRET] = self.reauth_entry.data[CONF_CLIENT_SECRET]
        return await self.async_step_reauth_confirm(user_input={})

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and launch OAuth authorization for reauth."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")

        return await super().async_step_user(user_input=None)

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create or update config entry after native OAuth token exchange."""
        token = data["token"]
        auth_implementation = data["auth_implementation"]
        if self.source == config_entries.SOURCE_REAUTH and self.reauth_entry is not None:
            # Validate the new token before committing it. A successful VIN fetch
            # confirms the token has the right scopes and the account is accessible.
            # Transient network errors are ignored so they don't block reauth.
            reauth_vins: list[str] | None = None
            try:
                reauth_vins, self._fleet_api_base = await async_fetch_available_vins(
                    self.hass, token["access_token"]
                )
            except TeslaAuthError as err:
                _LOGGER.warning("Reauth token validation failed: %s", err)
                return self.async_show_form(
                    step_id="reauth_confirm",
                    errors={"base": "invalid_auth"},
                )
            except _TESLA_HTTP_ERRORS as err:
                # Non-auth error (network, 5xx) — proceed and let the integration retry
                _LOGGER.debug("Reauth VIN validation skipped due to transient error: %s", err)
            if reauth_vins is not None and self.reauth_entry.data[CONF_VIN] not in reauth_vins:
                # Signed in with a Tesla account that can't see this vehicle.
                return self.async_abort(reason="wrong_account")

            merged_data = {
                **self._user_input,
                "auth_implementation": auth_implementation,
                "token": token,
            }
            if reauth_vins is not None:
                merged_data[CONF_FLEET_API_BASE] = self._fleet_api_base
            # The entry has no update listener (routine token refreshes must not
            # reload it), so reauth schedules its own reload here.
            return self.async_update_reload_and_abort(
                self.reauth_entry,
                data_updates=merged_data,
            )

        try:
            vins, self._fleet_api_base = await async_fetch_available_vins(
                self.hass, token["access_token"]
            )
        except _TESLA_HTTP_ERRORS as err:
            _LOGGER.error("Failed to fetch VINs from Fleet API: %s", err)
            return self.async_abort(reason="vin_fetch_failed")

        if not vins:
            return self.async_abort(reason="no_vehicles")

        # Drop vehicles that already have an entry now, rather than letting the
        # user fill in the whole connection form before the unique_id check aborts.
        configured = {entry.unique_id for entry in self._async_current_entries()}
        vins = [vin for vin in vins if vin not in configured]
        if not vins:
            return self.async_abort(reason="already_configured")

        self._pending_oauth_data = data
        self._available_vins = vins

        return await self.async_step_connection()

    async def _async_try_partner_registration_from_flow(
        self,
    ) -> tuple[str, str] | None:
        """Register the partner domain with Tesla if credentials and domain are available.

        Returns (error_key, api_detail) if Tesla rejected the domain (4xx), None otherwise.
        """
        domain = self._user_input.get(CONF_DOMAIN, "")
        if not domain:
            _LOGGER.debug(
                "Partner domain registration skipped: no domain value provided in user input"
            )
            return None
        impl: TeslaOAuth2Implementation | None = getattr(self, "flow_impl", None)
        if impl is None:
            _LOGGER.debug("Partner domain registration skipped: no OAuth implementation available")
            return None
        client_id = getattr(impl, "client_id", None)
        client_secret = getattr(impl, "client_secret", None)
        if not client_id or not client_secret:
            _LOGGER.debug(
                "Partner domain registration skipped: OAuth credentials not available "
                "(client_id=%s client_secret=%s)",
                bool(client_id),
                bool(client_secret),
            )
            return None
        return await _async_try_partner_registration(
            self.hass, client_id, client_secret, domain, self._fleet_api_base
        )

    async def _async_proceed_to_vin_or_telemetry(self) -> ConfigFlowResult:
        """Route to VIN selection or directly to telemetry prep, depending on vehicle count."""
        if len(self._available_vins) == 1:
            return await self._async_prepare_telemetry_step(self._available_vins[0])
        return await self.async_step_select_vin()

    async def async_step_select_vin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step to choose VIN when account has multiple vehicles."""
        if not self._available_vins or self._pending_oauth_data is None:
            return self.async_abort(reason="vin_fetch_failed")

        if user_input is None:
            schema = vol.Schema({vol.Required(CONF_VIN): vol.In(self._available_vins)})
            return self.async_show_form(step_id="select_vin", data_schema=schema)

        return await self._async_prepare_telemetry_step(user_input[CONF_VIN])

    async def _async_prepare_telemetry_step(self, vin: str) -> ConfigFlowResult:
        """Fetch existing car telemetry config and check for override."""
        if self._pending_oauth_data is None:
            return self.async_abort(reason="vin_fetch_failed")

        vin = vin.strip().upper()
        self._selected_vin = vin
        self._existing_car_telemetry_config = None

        token = self._pending_oauth_data["token"]
        proxy_client = TeslaProxyClient(
            self.hass,
            self._user_input[CONF_PROXY_URL],
            vin,
            verify_ssl=bool(self._user_input.get(CONF_PROXY_VERIFY_SSL)),
        )
        try:
            await proxy_client.async_setup()
            try:
                payload = await proxy_client.proxy_fleet_telemetry_get(token["access_token"])
            finally:
                await proxy_client.async_close()
            existing_config = extract_first_fields_config(payload)
            if isinstance(existing_config, dict):
                self._existing_car_telemetry_config = existing_config
        except PROXY_REQUEST_ERRORS as err:
            _LOGGER.debug("Unable to fetch existing fleet telemetry config for %s: %s", vin, err)

        # Build telemetry input from settings already gathered in step 1.
        self._pending_telemetry_input = {
            CONF_TELEMETRY_SERVER_HOSTNAME: self._user_input.get(
                CONF_TELEMETRY_SERVER_HOSTNAME, ""
            ),
            CONF_TELEMETRY_SERVER_PORT: self._user_input[CONF_TELEMETRY_SERVER_PORT],
            CONF_TELEMETRY_SERVER_CA: self._user_input.get(CONF_TELEMETRY_SERVER_CA, ""),
        }

        if self._car_config_override_requires_confirmation(self._pending_telemetry_input):
            return await self.async_step_confirm_override()

        return await self._async_create_entry_for_vin()

    async def async_step_confirm_override(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm overwriting an existing car fleet telemetry config."""
        if user_input is not None:
            if self._pending_telemetry_input is None or self._selected_vin is None:
                return self.async_abort(reason="vin_fetch_failed")
            return await self._async_create_entry_for_vin()

        existing_hostname = ""
        if self._existing_car_telemetry_config is not None:
            existing_hostname = str(self._existing_car_telemetry_config.get("hostname", ""))
        requested_hostname = ""
        if self._pending_telemetry_input is not None:
            requested_hostname = str(
                self._pending_telemetry_input.get(CONF_TELEMETRY_SERVER_HOSTNAME, "")
            )

        return self.async_show_form(
            step_id="confirm_override",
            data_schema=vol.Schema({}),
            description_placeholders={
                "existing_hostname": existing_hostname,
                "requested_hostname": requested_hostname,
            },
        )

    def _car_config_override_requires_confirmation(
        self,
        telemetry_input: dict[str, Any],
    ) -> bool:
        return _telemetry_config_override_requires_confirmation(
            telemetry_input, self._existing_car_telemetry_config
        )

    async def _async_create_entry_for_vin(self) -> ConfigFlowResult:
        """Finalize config entry creation for selected VIN."""
        if self._pending_oauth_data is None or self._selected_vin is None:
            return self.async_abort(reason="vin_fetch_failed")

        vin = self._selected_vin
        token = self._pending_oauth_data["token"]
        auth_implementation = self._pending_oauth_data["auth_implementation"]
        # _user_input already contains all settings
        # (MQTT, proxy, telemetry hostname/port/CA, domain).
        merged_data = {
            **self._user_input,
            CONF_VIN: vin,
            CONF_FLEET_API_BASE: self._fleet_api_base,
            "auth_implementation": auth_implementation,
            "token": token,
        }

        # Store credentials for use in the options flow (partner re-registration).
        impl = cast(TeslaOAuth2Implementation, getattr(self, "flow_impl", None))
        if impl is not None:
            client_id = getattr(impl, "client_id", None)
            client_secret = getattr(impl, "client_secret", None)
            if client_id:
                merged_data[CONF_CLIENT_ID] = client_id
            if client_secret:
                merged_data[CONF_CLIENT_SECRET] = client_secret

        await self.async_set_unique_id(vin)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=vin,
            data=merged_data,
        )


class TeslaFleetTelemetryOptionsFlow(config_entries.OptionsFlowWithReload):
    """Allow reconfiguring connection and data rate settings without re-adding."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._existing_car_telemetry_config: dict[str, Any] | None = None
        self._pending_input: dict[str, Any] | None = None

    async def _async_get_oauth_implementation(self) -> TeslaOAuth2Implementation:
        return cast(
            TeslaOAuth2Implementation,
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                self.hass,
                self._entry,
            ),
        )

    async def _async_fetch_existing_car_telemetry_config(self) -> dict[str, Any] | None:
        implementation = await self._async_get_oauth_implementation()
        access_token, _ = await async_get_valid_access_token(
            self.hass,
            self._entry,
            implementation,
        )
        merged = {**self._entry.data, **self._entry.options}
        proxy_client = TeslaProxyClient(
            self.hass,
            merged[CONF_PROXY_URL],
            self._entry.data[CONF_VIN],
            verify_ssl=bool(merged.get(CONF_PROXY_VERIFY_SSL)),
        )
        await proxy_client.async_setup()
        try:
            payload = await proxy_client.proxy_fleet_telemetry_get(access_token)
        finally:
            await proxy_client.async_close()
        return extract_first_fields_config(payload)

    def _options_override_requires_confirmation(
        self,
        normalized_input: dict[str, Any],
    ) -> bool:
        return _telemetry_config_override_requires_confirmation(
            normalized_input, self._existing_car_telemetry_config
        )

    async def _validate_options_input(
        self,
        user_input: dict[str, Any],
        errors: dict[str, str],
    ) -> bool:
        """Validate MQTT, proxy, and telemetry settings. Returns True if valid."""
        # Get user-defined timeout or use default
        timeout = user_input.get(CONF_CONNECTION_TIMEOUT, DEFAULT_CONNECTION_TIMEOUT)
        try:
            timeout = max(5, min(60, int(timeout)))
        except (TypeError, ValueError):
            timeout = DEFAULT_CONNECTION_TIMEOUT

        # Validate MQTT endpoint format and reachability.
        try:
            mqtt_host, mqtt_port = _parse_mqtt_endpoint(user_input.get(CONF_MQTT_ENDPOINT, ""))
        except ValueError:
            errors[CONF_MQTT_ENDPOINT] = "invalid_mqtt_endpoint"

        # Validate proxy and reachability.
        proxy_url = _validate_proxy_url(user_input.get(CONF_PROXY_URL, ""), errors)
        if not errors and proxy_url:
            user_input[CONF_PROXY_URL] = proxy_url
            errors.update(
                await _async_verify_connection_endpoints(
                    self.hass,
                    mqtt_host,
                    mqtt_port,
                    proxy_url,
                    timeout=timeout,
                    verify_ssl=bool(user_input.get(CONF_PROXY_VERIFY_SSL)),
                )
            )

        return not bool(errors)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        merged = {**self._entry.data, **self._entry.options}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            submitted_input = dict(user_input)
            # Validate connection settings.
            if await self._validate_options_input(user_input, errors):
                if (
                    _telemetry_endpoint_changed(merged, user_input)
                    or not str(merged.get(CONF_TELEMETRY_SERVER_CA, "") or "").strip()
                ):
                    user_input = await _maybe_autofetch_ca(self.hass, user_input, errors)
                else:
                    user_input[CONF_TELEMETRY_SERVER_CA] = merged.get(
                        CONF_TELEMETRY_SERVER_CA,
                        "",
                    )

            if not errors:
                normalized_input = await self._process_options_input(user_input, errors)
                if normalized_input and not errors:
                    # Re-register with Tesla if the domain was added or changed.
                    reg_result = await self._async_try_partner_registration_if_domain_changed(
                        normalized_input
                    )
                    if reg_result:
                        reg_error_key, reg_error_detail = reg_result
                        errors[CONF_DOMAIN] = reg_error_key
                        description_placeholders["partner_error"] = reg_error_detail
                    elif self._options_override_requires_confirmation(normalized_input):
                        return await self.async_step_confirm_override()
                    else:
                        return self.async_create_entry(title="", data=normalized_input)

            merged = {**merged, **user_input}
            merged[CONF_PROXY_URL] = submitted_input.get(
                CONF_PROXY_URL,
                merged.get(CONF_PROXY_URL, ""),
            )

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_schema(merged),
            errors=errors,
            description_placeholders=description_placeholders or None,
        )

    async def _async_try_partner_registration_if_domain_changed(
        self, normalized_input: dict[str, Any]
    ) -> tuple[str, str] | None:
        """Re-register partner domain with Tesla when the domain is set or changed.

        Returns (error_key, api_detail) if Tesla rejected the domain (4xx), None otherwise.
        """
        new_domain = str(normalized_input.get(CONF_DOMAIN, "") or "").strip().lower()
        if not new_domain:
            _LOGGER.debug("Partner re-registration skipped: no domain in options input")
            return None
        merged = {**self._entry.data, **self._entry.options}
        old_domain = str(merged.get(CONF_DOMAIN, "") or "").strip().lower()
        if new_domain == old_domain:
            _LOGGER.debug("Partner re-registration skipped: domain unchanged (%s)", new_domain)
            return None
        client_id = self._entry.data.get(CONF_CLIENT_ID)
        client_secret = self._entry.data.get(CONF_CLIENT_SECRET)
        if not client_id or not client_secret:
            _LOGGER.warning(
                "Cannot re-register domain %s: application credentials not stored in entry",
                new_domain,
            )
            return None
        return await _async_try_partner_registration(
            self.hass,
            client_id,
            client_secret,
            new_domain,
            self._entry.data.get(CONF_FLEET_API_BASE, FLEET_API_BASE),
        )

    async def _process_options_input(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> dict[str, Any] | None:
        """Process and validate options input."""
        try:
            normalized_input = _normalize_connection_input(user_input)
            normalized_input[CONF_PROXY_URL] = _normalize_proxy_url(
                normalized_input.get(CONF_PROXY_URL, "")
            )
            normalized_input = _normalize_telemetry_server_input(normalized_input)
        except ValueError as err:
            errors["base"] = str(err)
            return None

        self._pending_input = normalized_input
        current = {**self._entry.data, **self._entry.options}
        if all(
            normalized_input.get(key) == current.get(key)
            for key in (
                CONF_TELEMETRY_SERVER_HOSTNAME,
                CONF_TELEMETRY_SERVER_PORT,
                CONF_TELEMETRY_SERVER_CA,
            )
        ):
            self._existing_car_telemetry_config = None
            return normalized_input

        try:
            self._existing_car_telemetry_config = (
                await self._async_fetch_existing_car_telemetry_config()
            )
        except PROXY_REQUEST_ERRORS as err:
            _LOGGER.debug(
                "Unable to fetch existing fleet telemetry config for options flow: %s",
                err,
            )
            self._existing_car_telemetry_config = None

        return normalized_input

    async def async_step_confirm_override(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm overwriting an existing car fleet telemetry config from options."""
        if user_input is not None:
            if self._pending_input is None:
                return self.async_abort(reason="reauth_not_possible")
            return self.async_create_entry(title="", data=self._pending_input)

        existing_hostname = ""
        if self._existing_car_telemetry_config is not None:
            existing_hostname = str(self._existing_car_telemetry_config.get("hostname", ""))
        requested_hostname = ""
        if self._pending_input is not None:
            requested_hostname = str(self._pending_input.get(CONF_TELEMETRY_SERVER_HOSTNAME, ""))

        return self.async_show_form(
            step_id="confirm_override",
            data_schema=vol.Schema({}),
            description_placeholders={
                "existing_hostname": existing_hostname,
                "requested_hostname": requested_hostname,
            },
        )
