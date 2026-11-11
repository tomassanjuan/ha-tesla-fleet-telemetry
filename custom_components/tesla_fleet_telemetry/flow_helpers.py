"""Validation, normalization and connectivity helpers for the config and options flows."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from typing import Any, cast
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from tesla_fleet_api.exceptions import TeslaFleetError

from .const import (
    CONF_DOMAIN,
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
    DEFAULT_CONNECTION_CHECK_TIMEOUT,
    DEFAULT_MQTT_TOPIC_BASE,
    DEFAULT_TELEMETRY_MODE,
    DEFAULT_TELEMETRY_SERVER_ENDPOINT,
    TELEMETRY_MODE_OPTIONS,
)
from .oauth2 import (
    PartnerDomainError,
    async_check_partner_public_key,
    async_fetch_partner_token,
    async_register_partner_domain,
)
from .utils import (
    async_fetch_server_ca,
    normalize_pem_cert_bundle,
)

_LOGGER = logging.getLogger(__name__)


# Failures a Tesla Fleet API call can surface: wrapped HomeAssistantErrors,
# unwrapped library errors, and transport failures.
TESLA_HTTP_ERRORS: tuple[type[BaseException], ...] = (
    HomeAssistantError,
    TeslaFleetError,
    aiohttp.ClientError,
    TimeoutError,
)


def _classify_mqtt_result(finished: bool, rc: list[int], tls: bool) -> tuple[bool, str | None]:
    """Map the outcome of an MQTT connect attempt to (ok, error_key)."""
    if not finished:
        return False, "mqtt_timeout"
    if not rc:  # on_connect_fail: TCP/TLS handshake error
        return False, "mqtt_tls_failed" if tls else "mqtt_unreachable"
    if rc[0] in (134, 135):  # bad user name or password / not authorized
        return False, "mqtt_auth_failed"
    return (True, None) if rc[0] == 0 else (False, "mqtt_unreachable")


async def async_verify_mqtt_reachable(
    hass: HomeAssistant,
    host: str,
    port: int,
    timeout: int | None = None,
    username: str | None = None,
    password: str | None = None,
    tls: bool = False,
) -> tuple[bool, str | None]:
    """Do a real MQTT handshake (credentials + TLS). Returns (ok, error_key)."""
    import ssl  # noqa: PLC0415
    import threading  # noqa: PLC0415

    import paho.mqtt.client as mqtt  # noqa: PLC0415
    from homeassistant.util.ssl import client_context  # noqa: PLC0415

    _timeout = timeout if timeout is not None else DEFAULT_CONNECTION_CHECK_TIMEOUT

    def _check_mqtt() -> tuple[bool, str | None]:
        done = threading.Event()
        rc: list[int] = []

        def on_connect(client, userdata, flags, reason_code, properties) -> None:
            rc.append(reason_code.value if hasattr(reason_code, "value") else int(reason_code))
            done.set()

        client = mqtt.Client(
            protocol=mqtt.MQTTv5, callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )
        client.on_connect = on_connect
        client.on_connect_fail = lambda *_: done.set()
        if username:
            client.username_pw_set(username, password or "")
        if tls:
            client.tls_set_context(client_context())
        try:
            client.connect(host, port, keepalive=10)
            client.loop_start()
            finished = done.wait(_timeout)
        except ssl.SSLError as e:
            _LOGGER.debug("MQTT TLS failed for %s:%s: %s", host, port, e)
            return False, "mqtt_tls_failed"
        except TimeoutError:
            return False, "mqtt_timeout"
        except OSError as e:
            _LOGGER.debug("MQTT connection failed for %s:%s: %s", host, port, e)
            return False, "mqtt_unreachable"
        finally:
            client.disconnect()
            client.loop_stop()
        return _classify_mqtt_result(finished, rc, tls)

    return cast(tuple[bool, str | None], await hass.async_add_executor_job(_check_mqtt))


async def async_verify_proxy_reachable(
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


async def async_verify_connection_endpoints(
    hass: HomeAssistant,
    mqtt_host: str,
    mqtt_port: int,
    proxy_url: str,
    timeout: int | None = None,
    verify_ssl: bool = False,
    mqtt_username: str | None = None,
    mqtt_password: str | None = None,
    mqtt_tls: bool = False,
) -> dict[str, str]:
    """Check MQTT and proxy endpoints in parallel; return form errors."""
    mqtt_result, proxy_result = await asyncio.gather(
        async_verify_mqtt_reachable(
            hass,
            mqtt_host,
            mqtt_port,
            timeout=timeout,
            username=mqtt_username,
            password=mqtt_password,
            tls=mqtt_tls,
        ),
        async_verify_proxy_reachable(hass, proxy_url, timeout=timeout, verify_ssl=verify_ssl),
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


async def maybe_autofetch_ca(
    hass: HomeAssistant,
    user_input: dict[str, Any],
    errors: dict[str, str],
) -> dict[str, Any]:
    """Auto-fetch the server CA cert; return updated user_input with CA filled in."""
    endpoint = str(user_input.get(CONF_TELEMETRY_SERVER_ENDPOINT, "")).strip()
    try:
        hostname, port = parse_telemetry_endpoint(endpoint)
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


def mqtt_endpoint_default(host: str, port: int) -> str:
    """Build the user-facing MQTT endpoint string."""
    return f"{host}:{port}"


def parse_mqtt_endpoint(endpoint: str) -> tuple[str, int]:
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


def parse_telemetry_endpoint(endpoint: str) -> tuple[str, int]:
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
        host, port = parse_mqtt_endpoint(endpoint)
    except ValueError:
        if ":" in endpoint or "/" in endpoint or "@" in endpoint:
            raise
        return endpoint, DEFAULT_HTTPS_PORT
    return host, port


def telemetry_config_override_requires_confirmation(
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


def normalize_proxy_url(proxy_url: str) -> str:
    """Normalize the command proxy URL; the proxy is always HTTPS."""
    proxy_url = proxy_url.strip().rstrip("/")
    if proxy_url and "://" not in proxy_url:
        proxy_url = f"https://{proxy_url}"
    return proxy_url


def display_proxy_url(proxy_url: str) -> str:
    """Return the user-facing proxy value without the implicit HTTPS scheme."""
    proxy_url = proxy_url.strip().rstrip("/")
    parsed = urlparse(proxy_url)
    if parsed.scheme == "https" and parsed.netloc and not parsed.path:
        return parsed.netloc
    return proxy_url


_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


_MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=list(TELEMETRY_MODE_OPTIONS),
        mode=SelectSelectorMode.DROPDOWN,
        translation_key=CONF_TELEMETRY_MODE,
    )
)


def mqtt_auth_schema(values: dict[str, Any]) -> dict[Any, Any]:
    """Optional MQTT credentials shared by the setup and options forms."""
    return {
        vol.Optional(CONF_MQTT_USERNAME, default=values.get(CONF_MQTT_USERNAME, "")): str,
        vol.Optional(
            CONF_MQTT_PASSWORD, default=values.get(CONF_MQTT_PASSWORD, "")
        ): _PASSWORD_SELECTOR,
    }


def build_user_schema(
    mqtt_endpoint: str = "",
    proxy_url: str = "",
    telemetry_endpoint: str = DEFAULT_TELEMETRY_SERVER_ENDPOINT,
    telemetry_mode: str = DEFAULT_TELEMETRY_MODE,
    domain: str = "",
    previous: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the setup form schema; `previous` re-fills fields after a failed submit."""
    previous = previous or {}
    return vol.Schema(
        {
            vol.Optional(CONF_DOMAIN, default=domain): str,
            vol.Required(CONF_TELEMETRY_SERVER_ENDPOINT, default=telemetry_endpoint): str,
            vol.Required(CONF_PROXY_URL, default=proxy_url): str,
            vol.Required(CONF_MQTT_ENDPOINT, default=mqtt_endpoint): str,
            **mqtt_auth_schema(previous),
            vol.Required(CONF_TELEMETRY_MODE, default=telemetry_mode): _MODE_SELECTOR,
            vol.Optional(CONF_MQTT_TOPIC_BASE, default=DEFAULT_MQTT_TOPIC_BASE): str,
            vol.Optional(
                CONF_PROXY_VERIFY_SSL, default=bool(previous.get(CONF_PROXY_VERIFY_SSL, False))
            ): bool,
            vol.Optional(CONF_MQTT_TLS, default=bool(previous.get(CONF_MQTT_TLS, False))): bool,
        }
    )


def build_options_schema(merged: dict[str, Any]) -> vol.Schema:
    """Build the options form schema (same field order as the setup form)."""
    return vol.Schema(
        {
            vol.Optional(CONF_DOMAIN, default=merged.get(CONF_DOMAIN, "")): str,
            vol.Required(
                CONF_TELEMETRY_SERVER_ENDPOINT,
                default=(
                    f"{merged[CONF_TELEMETRY_SERVER_HOSTNAME]}:{merged[CONF_TELEMETRY_SERVER_PORT]}"
                    if merged.get(CONF_TELEMETRY_SERVER_HOSTNAME)
                    else DEFAULT_TELEMETRY_SERVER_ENDPOINT
                ),
            ): str,
            vol.Required(
                CONF_PROXY_URL, default=display_proxy_url(merged.get(CONF_PROXY_URL, ""))
            ): str,
            vol.Required(
                CONF_MQTT_ENDPOINT,
                default=(
                    mqtt_endpoint_default(merged[CONF_MQTT_HOST], merged[CONF_MQTT_PORT])
                    if merged.get(CONF_MQTT_HOST)
                    else ""
                ),
            ): str,
            **mqtt_auth_schema(merged),
            vol.Required(
                CONF_TELEMETRY_MODE,
                default=merged.get(CONF_TELEMETRY_MODE, DEFAULT_TELEMETRY_MODE),
            ): _MODE_SELECTOR,
            vol.Optional(
                CONF_MQTT_TOPIC_BASE,
                default=merged.get(CONF_MQTT_TOPIC_BASE, DEFAULT_MQTT_TOPIC_BASE),
            ): str,
            vol.Optional(
                CONF_PROXY_VERIFY_SSL, default=bool(merged.get(CONF_PROXY_VERIFY_SSL, False))
            ): bool,
            vol.Optional(CONF_MQTT_TLS, default=bool(merged.get(CONF_MQTT_TLS, False))): bool,
        }
    )


def normalize_connection_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Replace the flow-only MQTT endpoint with stored host/port keys."""
    host, port = parse_mqtt_endpoint(user_input[CONF_MQTT_ENDPOINT])
    normalized = dict(user_input)
    normalized.pop(CONF_MQTT_ENDPOINT, None)
    normalized[CONF_MQTT_HOST] = host
    normalized[CONF_MQTT_PORT] = port
    normalized[CONF_DOMAIN] = str(normalized.get(CONF_DOMAIN, "") or "").strip().lower()
    return normalized


async def async_try_partner_registration(
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
    except TESLA_HTTP_ERRORS as err:
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
    except TESLA_HTTP_ERRORS as err:
        _LOGGER.warning(
            "Tesla partner domain registration failed (transient) for %s: %s",
            domain,
            err,
        )
        return None

    try:
        await async_check_partner_public_key(hass, token, domain, api_base)
        _LOGGER.info("Partner public key verified for domain: %s", domain)
    except TESLA_HTTP_ERRORS as err:
        _LOGGER.warning(
            "Partner public key check failed for %s (registration may still be pending): %s",
            domain,
            err,
        )
    return None


def normalize_telemetry_server_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Parse telemetry endpoint string and validate hostname/port."""
    normalized = dict(user_input)
    endpoint = str(normalized.pop(CONF_TELEMETRY_SERVER_ENDPOINT, "")).strip()
    try:
        hostname, port = parse_telemetry_endpoint(endpoint)
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


_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def validate_domain(domain: str, errors: dict[str, str]) -> str:
    """Validate the optional Tesla app domain locally (bare DNS name, no scheme/port/path)."""
    domain = domain.strip().lower().rstrip(".")
    if domain and not _DOMAIN_RE.match(domain):
        errors[CONF_DOMAIN] = "invalid_domain"
    return domain


def validate_proxy_url(proxy_url: str, errors: dict[str, str]) -> str:
    """Validate the command proxy URL before any network request."""
    proxy_url = normalize_proxy_url(proxy_url)
    parsed = urlparse(proxy_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        errors[CONF_PROXY_URL] = "proxy_invalid_url"
        return proxy_url
    return proxy_url


def validate_telemetry_endpoint(endpoint: str, errors: dict[str, str]) -> tuple[str, int]:
    """Validate telemetry endpoint hostname and port. Returns (hostname, port)."""
    hostname = ""
    port = 0
    try:
        hostname, port = parse_telemetry_endpoint(endpoint)
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


def telemetry_endpoint_changed(current: dict[str, Any], user_input: dict[str, Any]) -> bool:
    """Return True when options point at a different telemetry server."""
    try:
        hostname, port = parse_telemetry_endpoint(
            str(user_input.get(CONF_TELEMETRY_SERVER_ENDPOINT, "")).strip()
        )
    except ValueError:
        return False
    return hostname.strip().rstrip(".") != current.get(
        CONF_TELEMETRY_SERVER_HOSTNAME
    ) or port != current.get(CONF_TELEMETRY_SERVER_PORT)
