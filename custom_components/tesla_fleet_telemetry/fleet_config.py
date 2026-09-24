"""Fleet telemetry configuration push helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_TELEMETRY_MODE,
    CONF_TELEMETRY_SERVER_CA,
    CONF_TELEMETRY_SERVER_HOSTNAME,
    CONF_TELEMETRY_SERVER_PORT,
    DEFAULT_TELEMETRY_ALERT_TYPES,
    DEFAULT_TELEMETRY_DELIVERY_POLICY,
    DEFAULT_TELEMETRY_MODE,
    DOMAIN,
    FIELD_DEFS,
    FLEET_CONFIG_SYNC_DELAYS_SECONDS,
    FLEET_TELEMETRY_ERROR_LOG_LIMIT,
    SUPPORTED_STREAMING_FIELDS,
)
from .policy import (
    TelemetryPolicyContext,
    default_field_policy,
    extract_unsupported_interval,
)
from .proxy_client import RateLimitedError, TeslaProxyClient, TokenRevokedError
from .utils import normalize_pem_cert_bundle

_LOGGER = logging.getLogger(__name__)


@dataclass
class PolicyState:
    """Typed state for fleet telemetry config push operations."""

    last_policy_key: str | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    last_push_monotonic: float | None = None
    last_push_dt: datetime | None = None
    rate_limited_until: float = 0.0
    authorized_interval_overrides: dict[str, int] = field(default_factory=dict)
    logged_telemetry_error_keys: set[tuple[str, str]] = field(default_factory=set)


def _policy_context_from_options(
    options: dict[str, Any],
) -> TelemetryPolicyContext:
    """Build a telemetry policy context from entry options."""
    return TelemetryPolicyContext(
        telemetry_mode=options.get(CONF_TELEMETRY_MODE, DEFAULT_TELEMETRY_MODE),
        config=options,
    )


def _extract_fleet_config_status(payload: Any) -> dict[str, bool | None]:
    """Extract sync status from fleet config payload.

    The Tesla Fleet API may wrap the actual config response in "response" or "result" keys.
    This function unwraps those and validates the three critical status fields:
    synced, limit_reached, key_paired.

    Args:
        payload: Response from fleet API (may have nested structure, may not be dict).
                 If invalid, logs warning and returns empty dict.

    Returns:
        Dict with keys 'synced', 'limit_reached', 'key_paired' (all bool | None).
        Returns empty dict if payload is not a dict after unwrapping.
    """
    if isinstance(payload, dict):
        for key in ("response", "result"):
            value = payload.get(key)
            if isinstance(value, dict):
                payload = value
                break
    if not isinstance(payload, dict):
        _LOGGER.warning(
            "Invalid fleet config response: expected dict, got %s", type(payload).__name__
        )
        return {}
    synced = payload.get("synced")
    if synced is not None and not isinstance(synced, bool):
        _LOGGER.warning("Invalid synced field type: %s", type(synced).__name__)
        synced = None
    limit_reached = payload.get("limit_reached")
    if limit_reached is not None and not isinstance(limit_reached, bool):
        _LOGGER.warning("Invalid limit_reached field type: %s", type(limit_reached).__name__)
        limit_reached = None
    key_paired = payload.get("key_paired")
    if key_paired is not None and not isinstance(key_paired, bool):
        _LOGGER.warning("Invalid key_paired field type: %s", type(key_paired).__name__)
        key_paired = None
    return {
        "synced": synced,
        "limit_reached": limit_reached,
        "key_paired": key_paired,
    }


def _extract_fleet_telemetry_errors(payload: Any) -> list[dict[str, Any]]:
    """Extract a normalized list of fleet telemetry errors from proxy payloads.

    Handles multiple response formats from the Tesla Fleet API:
    - Direct list of error dicts
    - Nested under 'fleet_telemetry_errors', 'response', 'errors', or 'result' keys
    - Supports multiple levels of nesting

    Args:
        payload: Response payload from fleet API (may not be dict/list, may be deeply nested).
                 If invalid, logs warning and returns empty list.

    Returns:
        List of error dicts, filtered to only include dict items.
        Returns empty list if payload invalid or no errors found.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        _LOGGER.warning("Invalid errors payload: expected dict, got %s", type(payload).__name__)
        return []

    direct = payload.get("fleet_telemetry_errors")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]

    for key in ("response", "errors", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested_list = value.get("fleet_telemetry_errors")
            if isinstance(nested_list, list):
                return [item for item in nested_list if isinstance(item, dict)]
            nested = value.get("errors")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]

    return []


def _parse_fleet_telemetry_error_created_at(value: object) -> datetime | None:
    """Parse Tesla fleet telemetry error timestamps as UTC-aware datetimes."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _generate_qr_data_uri(data: str) -> str:
    """Generate a QR code as an SVG data URI."""
    import base64  # noqa: PLC0415

    import qrcode  # noqa: PLC0415

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    import io  # noqa: PLC0415

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_b64 = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{img_b64}"


def _notify_key_not_paired(hass: HomeAssistant, vin: str, pairing_domain: str) -> None:
    """Create a repair issue when key_paired=False is reported by Tesla.

    pairing_domain should be the registered application domain (e.g. yourdomain.com)
    or the telemetry server hostname as a fallback.
    """
    from homeassistant.helpers.issue_registry import (  # noqa: PLC0415
        IssueSeverity,
        async_create_issue,
    )

    pairing_url = f"https://tesla.com/_ak/{pairing_domain}"
    qr_url = _generate_qr_data_uri(pairing_url)
    async_create_issue(
        hass,
        DOMAIN,
        f"key_not_paired_{vin}",
        is_fixable=True,
        learn_more_url=pairing_url,
        severity=IssueSeverity.WARNING,
        translation_key="key_not_paired",
        translation_placeholders={
            "vin": vin,
            "pairing_url": pairing_url,
            "qr_url": qr_url,
        },
    )


def _dismiss_key_not_paired(hass: HomeAssistant, vin: str) -> None:
    """Dismiss the virtual key repair issue once the key is confirmed paired."""
    from homeassistant.helpers.issue_registry import async_delete_issue  # noqa: PLC0415

    async_delete_issue(hass, DOMAIN, f"key_not_paired_{vin}")


def _response_vin_skipped_missing_key(vin: str, response: Any) -> bool:
    """Return True if the push response reports missing_key for this VIN."""
    if not isinstance(response, dict):
        return False
    inner = response.get("response", response)
    if not isinstance(inner, dict):
        return False
    skipped = inner.get("skipped_vehicles")
    if not isinstance(skipped, dict):
        return False
    return vin in (skipped.get("missing_key") or [])


def _log_push_response_skipped(vin: str, response: Any) -> None:
    """Warn when Tesla's push response shows the vehicle was skipped."""
    if not isinstance(response, dict):
        return
    inner = response.get("response", response)
    if not isinstance(inner, dict):
        return
    skipped = inner.get("skipped_vehicles")
    if not isinstance(skipped, dict):
        return
    reasons = {
        "missing_key": "virtual key is not paired — open the Tesla app to pair it",
        "unsupported_hardware": "vehicle hardware does not support fleet telemetry",
        "unsupported_firmware": "vehicle firmware is too old for fleet telemetry",
        "max_configs": "maximum fleet telemetry config slots reached for this vehicle",
    }
    for key, reason in reasons.items():
        if vin in (skipped.get(key) or []):
            _LOGGER.warning("fleet_telemetry_config push blocked for %s: %s", vin, reason)


def _is_fatal_fleet_config_push_error(err: Exception) -> bool:
    """Return True for request errors that retries cannot fix."""
    message = str(err).lower()
    return (
        "invalidrequest" in message
        or "request body is not valid" in message
        or "not valid" in message
        or "telemetry server ca is not a valid pem bundle" in message
    )


def _log_fleet_config_push_error(vin: str, err: Exception) -> None:
    """Log actionable fleet telemetry push failures."""
    message = str(err)
    lowered = message.lower()

    if "sleep" in lowered or "asleep" in lowered:
        _LOGGER.warning(
            "fleet_telemetry_config push failed for %s because the vehicle appears asleep: %s",
            vin,
            err,
        )
        return

    if "missing_key" in lowered:
        _LOGGER.warning(
            "fleet_telemetry_config push failed for %s because Tesla reports missing_key. "
            "The virtual key has not been added to the vehicle: %s",
            vin,
            err,
        )
        return

    if "unsupported_hardware" in lowered:
        _LOGGER.warning(
            "fleet_telemetry_config push failed for %s because the vehicle hardware is unsupported "
            "for fleet telemetry: %s",
            vin,
            err,
        )
        return

    if "unsupported_firmware" in lowered:
        _LOGGER.warning(
            "fleet_telemetry_config push failed for %s because the vehicle firmware is too old "
            "for fleet telemetry: %s",
            vin,
            err,
        )
        return

    if "endpoint" in lowered and ("not registered" in lowered or "not_registered" in lowered):
        _LOGGER.warning(
            "fleet_telemetry_config push failed for %s: Tesla reports the telemetry endpoint is not "
            "registered. Check that your partner domain is registered with Tesla (Settings → "
            "Re-register domain) and that the telemetry server hostname in the integration options "
            "matches the registered endpoint: %s",
            vin,
            err,
        )
        return

    _LOGGER.warning("fleet_telemetry_config push failed for %s: %s", vin, err)


async def _log_fleet_telemetry_errors(
    proxy_client: TeslaProxyClient,
    vin: str,
    access_token: str,
    seen_error_keys: set[tuple[str, str]] | None = None,
    hass: HomeAssistant | None = None,
) -> None:
    """Fetch and log fleet telemetry errors after a config push."""
    try:
        payload = await proxy_client.proxy_fleet_telemetry_errors_get(access_token)
    except TokenRevokedError as err:
        raise ConfigEntryAuthFailed(
            "Tesla OAuth token was revoked — re-authentication required"
        ) from err
    except Exception as err:
        _LOGGER.debug("Unable to read fleet_telemetry_errors for %s: %s", vin, err)
        return

    raw_errors = _extract_fleet_telemetry_errors(payload)
    if not raw_errors:
        _LOGGER.debug("No recent fleet_telemetry_errors reported for %s", vin)
        return

    if seen_error_keys is None:
        seen_error_keys = set()

    now_utc = datetime.now(UTC)
    for errors in _group_fleet_telemetry_errors(raw_errors)[:FLEET_TELEMETRY_ERROR_LOG_LIMIT]:
        _process_and_log_telemetry_errors(errors, vin, now_utc, seen_error_keys, hass)


def _group_fleet_telemetry_errors(
    errors: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group repeated telemetry errors that only differ by occurrence timestamp."""
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for error in errors:
        key = (
            str(error.get("vin") or ""),
            str(error.get("error_name") or ""),
            str(error.get("error") or ""),
            str(error.get("hostname") or ""),
            str(error.get("port") or ""),
        )
        grouped.setdefault(key, []).append(error)

    return sorted(
        grouped.values(),
        key=lambda group: max(_fleet_telemetry_error_sort_key(item) for item in group),
        reverse=True,
    )


def _fleet_telemetry_error_sort_key(error: dict[str, Any]) -> datetime:
    """Return a sortable timestamp for telemetry errors."""
    return _parse_fleet_telemetry_error_created_at(error.get("created_at")) or datetime.min.replace(
        tzinfo=UTC
    )


def _format_fleet_telemetry_error_timestamp(value: object) -> str | None:
    """Format Tesla fleet telemetry timestamps for concise logs."""
    parsed = _parse_fleet_telemetry_error_created_at(value)
    if parsed is not None:
        return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _format_fleet_telemetry_error_description(value: object) -> str | None:
    """Return a readable fleet telemetry error description."""
    if not isinstance(value, str):
        return None
    description = value.strip()
    if description.startswith('"'):
        quote_end = description.find('"', 1)
        if quote_end > 0:
            description = f"{description[1:quote_end]}{description[quote_end + 1 :]}"
            description = description.strip()
    return description or None


def _format_fleet_telemetry_error_target(hostname: object, port: object) -> str | None:
    """Return the telemetry error target as host:port when available."""
    hostname_text = str(hostname).strip() if hostname else ""
    port_text = str(port).strip() if port else ""
    if hostname_text and port_text:
        return f"{hostname_text}:{port_text}"
    return hostname_text or port_text or None


def _detect_and_report_unknown_field(hass: HomeAssistant, error: dict[str, Any], vin: str) -> None:
    """Detect unknown field errors and create repair issues."""
    error_text = str(error.get("error", "")).strip()
    if not error_text.startswith("Unknown field"):
        return

    field_name = error_text.replace("Unknown field", "", 1).strip()
    if not field_name:
        return

    issue_id = f"unknown_field_{vin}_{field_name}"
    issue_registry = ir.async_get(hass)

    if not issue_registry.async_get_issue(DOMAIN, issue_id):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="unknown_telemetry_field",
            translation_placeholders={
                "vin": vin,
                "field": field_name,
            },
            data={
                "vin": vin,
                "field": field_name,
            },
        )
        _LOGGER.error(
            "Created repair issue for unknown telemetry field '%s' in vehicle %s. "
            "This field is likely not supported by your vehicle or Tesla Fleet Telemetry API. "
            "Go to Settings → Devices & Services → Tesla Fleet Telemetry → Unknown field repair "
            "to disable this entity.",
            field_name,
            vin,
        )


def _process_and_log_telemetry_errors(
    errors: list[dict[str, Any]],
    vin: str,
    now_utc: datetime,
    seen_error_keys: set[tuple[str, str]],
    hass: HomeAssistant | None = None,
) -> bool:
    """Log a grouped telemetry error if not seen before. Returns True if logged."""
    if not errors:
        return False

    errors = sorted(errors, key=_fleet_telemetry_error_sort_key)
    error = errors[-1]
    created_at_values = [
        formatted
        for item in errors
        if (formatted := _format_fleet_telemetry_error_timestamp(item.get("created_at")))
    ]
    first_seen = created_at_values[0] if created_at_values else None
    last_seen = created_at_values[-1] if created_at_values else None

    error_key = (_fleet_telemetry_error_seen_key(error, vin), last_seen or "")
    if error_key in seen_error_keys:
        return False

    error_vin = error.get("vin") or vin
    error_name = error.get("error_name") or "telemetry_error"
    description = _format_fleet_telemetry_error_description(error.get("error"))
    target = _format_fleet_telemetry_error_target(
        error.get("hostname"),
        error.get("port"),
    )

    # Detect and report unknown field errors
    if hass is not None:
        _detect_and_report_unknown_field(hass, error, error_vin)

    age_label = ""
    created_at_dt = _parse_fleet_telemetry_error_created_at(last_seen)
    if created_at_dt is not None:
        age_hours = (now_utc - created_at_dt).total_seconds() / 3600
        if age_hours >= 24:
            age_label = f" ({age_hours:.0f}h ago)"

    parts = [f"error_name={error_name}"]
    if description:
        parts.append(f"description={description}")
    if last_seen:
        repeated = bool(first_seen) and first_seen != last_seen
        parts.append(f"count={len(errors) if repeated else 1}")
        if age_label:
            parts.append(f"last_seen_age={age_label.strip(' ()')}")
    if target:
        parts.append(f"target={target}")

    _LOGGER.warning("fleet_telemetry_errors: %s", ", ".join(parts))

    seen_error_keys.add(error_key)
    return True


def _fleet_telemetry_error_seen_key(error: dict[str, Any], vin: str) -> str:
    """Build a stable key for repeated telemetry errors."""
    return "|".join(
        (
            str(error.get("vin") or vin),
            str(error.get("error_name") or ""),
            str(error.get("error") or ""),
            str(error.get("hostname") or ""),
            str(error.get("port") or ""),
        )
    )


async def _wait_for_fleet_config_sync_and_log_errors(
    proxy_client: TeslaProxyClient,
    vin: str,
    access_token: str,
    seen_error_keys: set[tuple[str, str]] | None = None,
    sync_status_callback: Callable[[dict[str, Any]], None] | None = None,
    hass: HomeAssistant | None = None,
) -> dict[str, Any]:
    """Wait briefly for vehicle config sync, then fetch and log telemetry errors."""
    verify: dict[str, Any] | None = None
    elapsed_seconds = 0
    for attempt, delay_seconds in enumerate(FLEET_CONFIG_SYNC_DELAYS_SECONDS, start=1):
        await asyncio.sleep(delay_seconds)
        elapsed_seconds += delay_seconds
        try:
            verify = await proxy_client.proxy_fleet_telemetry_get(access_token)
        except Exception as err:
            _LOGGER.debug(
                "Unable to re-read fleet_telemetry_config for %s after push: %s",
                vin,
                err,
            )
            continue

        status = _extract_fleet_config_status(verify)
        synced = status.get("synced")
        limit_reached = status.get("limit_reached")
        key_paired = status.get("key_paired")
        if sync_status_callback is not None:
            sync_status_callback(
                {
                    "pushed": True,
                    "synced": synced is True,
                    "limit_reached": limit_reached,
                    "key_paired": key_paired,
                    "attempts": attempt,
                    "wait_seconds": elapsed_seconds,
                }
            )
        if limit_reached is True:
            _LOGGER.warning(
                "fleet_telemetry_config for %s reports limit_reached=true",
                vin,
            )
        if synced is True:
            _LOGGER.debug(
                "fleet_telemetry_config for %s synced on attempt %s",
                vin,
                attempt,
            )
            await _log_fleet_telemetry_errors(
                proxy_client, vin, access_token, seen_error_keys, hass
            )
            return {
                "synced": True,
                "limit_reached": limit_reached,
                "key_paired": key_paired,
                "attempts": attempt,
                "wait_seconds": elapsed_seconds,
            }
        _LOGGER.debug(
            "fleet_telemetry_config for %s not yet synced on attempt %s (synced=%s, key_paired=%s)",
            vin,
            attempt,
            synced,
            key_paired,
        )

    if verify is not None:
        status = _extract_fleet_config_status(verify)
        await _log_fleet_telemetry_errors(proxy_client, vin, access_token, seen_error_keys, hass)
        key_paired = status.get("key_paired")
        synced = status.get("synced")
        if key_paired is False:
            hint = (
                " Vehicle virtual key not paired — open the Tesla app and pair the "
                "proxy key at the vehicle pairing URL."
            )
        elif synced is False:
            hint = (
                " Key is paired but vehicle has not synced — check telemetry server "
                "hostname, port, and TLS certificate reachability from the vehicle."
            )
        else:
            hint = ""
        _LOGGER.warning(
            "fleet_telemetry_config for %s did not report synced=true after %ss "
            "(synced=%s, key_paired=%s, limit_reached=%s).%s",
            vin,
            elapsed_seconds,
            synced,
            key_paired,
            status.get("limit_reached"),
            hint,
        )
        return {
            "synced": synced,
            "limit_reached": status.get("limit_reached"),
            "key_paired": key_paired,
            "attempts": len(FLEET_CONFIG_SYNC_DELAYS_SECONDS),
            "wait_seconds": elapsed_seconds,
        }
    _LOGGER.warning(
        "fleet_telemetry_config for %s did not respond after %ss — "
        "vehicle may be offline or the proxy unreachable; fetching errors",
        vin,
        elapsed_seconds,
    )
    await _log_fleet_telemetry_errors(proxy_client, vin, access_token, seen_error_keys, hass)
    return {
        "synced": False,
        "limit_reached": None,
        "key_paired": None,
        "attempts": len(FLEET_CONFIG_SYNC_DELAYS_SECONDS),
        "wait_seconds": elapsed_seconds,
    }


def _build_managed_fleet_config(
    required_fields: set[str],
    policy_context: TelemetryPolicyContext,
) -> tuple[dict[str, Any], int, int]:
    """Build the managed fleet telemetry config payload."""
    fields: dict[str, Any] = {}
    fast_field_count = 0
    for fname in sorted(required_fields):
        policy = default_field_policy(fname, policy_context)
        fields[fname] = policy
        if policy["interval_seconds"] == 1:
            fast_field_count += 1
    slow_field_count = len(required_fields) - fast_field_count

    cfg = policy_context.config or {}
    raw_ca = cfg.get(CONF_TELEMETRY_SERVER_CA, "")
    try:
        normalized_ca = normalize_pem_cert_bundle(raw_ca)
    except ValueError as err:
        raise ValueError("telemetry server CA is not a valid PEM bundle") from err

    config = {
        "hostname": cfg.get(CONF_TELEMETRY_SERVER_HOSTNAME, ""),
        "port": cfg.get(CONF_TELEMETRY_SERVER_PORT),
        "ca": normalized_ca,
        "alert_types": DEFAULT_TELEMETRY_ALERT_TYPES,
        "delivery_policy": DEFAULT_TELEMETRY_DELIVERY_POLICY,
        "fields": fields,
    }
    return config, fast_field_count, slow_field_count


def _build_push_result(
    base_result: dict[str, Any],
    pushed: bool = False,
    synced: bool | None = None,
    sync_result: dict[str, Any] | None = None,
    response: Any = None,
    error: str | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    """Build a consistent telemetry push result dict."""
    result = {**base_result, "pushed": pushed}
    if sync_result:
        result.update(
            {
                "synced": sync_result.get("synced", synced),
                "limit_reached": sync_result.get("limit_reached"),
                "key_paired": sync_result.get("key_paired"),
                "attempts": sync_result.get("attempts"),
                "wait_seconds": sync_result.get("wait_seconds"),
                "sync_pending": False,
            }
        )
    else:
        result.update(
            {
                "synced": synced,
                "limit_reached": None,
                "key_paired": None,
                "attempts": 0,
                "wait_seconds": 0,
                # Only a push Tesla accepted can be pending sync; a failed or
                # rate-limited push must not start the sync-polling loop.
                "sync_pending": pushed and synced is None,
            }
        )
    result.update(
        {
            "response": response,
            "error": error,
            **extra_fields,
        }
    )
    return result


async def _do_push_and_sync(
    proxy_client: TeslaProxyClient,
    vin: str,
    access_token: str,
    config: dict[str, Any],
    base_result: dict[str, Any],
    wait_for_sync: bool,
    seen_error_keys: set[tuple[str, str]] | None,
    sync_status_callback: Callable[[dict[str, Any]], None] | None,
    hass: HomeAssistant | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    """Execute a single push and optional sync operation."""
    response = await proxy_client.proxy_fleet_telemetry_set(config, access_token)
    _LOGGER.debug(
        "fleet_telemetry_config response received for %s: %s",
        vin,
        response,
    )
    _log_push_response_skipped(vin, response)
    if not wait_for_sync:
        # Propagate key_paired=False from skipped_vehicles.missing_key so the
        # caller's _notify_key_not_paired path fires without waiting for the sync GET.
        missing_key_extra: dict[str, Any] = (
            {"key_paired": False} if _response_vin_skipped_missing_key(vin, response) else {}
        )
        return _build_push_result(
            base_result,
            pushed=True,
            response=response,
            **{**extra_fields, **missing_key_extra},
        )
    sync_result = await _wait_for_fleet_config_sync_and_log_errors(
        proxy_client, vin, access_token, seen_error_keys, sync_status_callback, hass
    )
    return _build_push_result(
        base_result,
        pushed=True,
        sync_result=sync_result,
        response=response,
        **extra_fields,
    )


async def _push_telemetry_config(
    proxy_client: TeslaProxyClient,
    vin: str,
    access_token: str,
    required_fields: set[str],
    policy_context: TelemetryPolicyContext,
    reason: str = "manual",
    seen_error_keys: set[tuple[str, str]] | None = None,
    sync_status_callback: Callable[[dict[str, Any]], None] | None = None,
    wait_for_sync: bool = True,
    hass: HomeAssistant | None = None,
) -> dict[str, Any]:
    """Push fleet_telemetry_config to Tesla with the required field set."""
    required_fields = {
        field
        for field in required_fields
        if field in FIELD_DEFS and field in SUPPORTED_STREAMING_FIELDS
    }

    config, fast_field_count, slow_field_count = _build_managed_fleet_config(
        required_fields,
        policy_context,
    )
    managed_fields = config["fields"]
    _LOGGER.debug(
        "fleet_telemetry_config push target for %s: hostname=%s port=%s ca=%s",
        vin,
        config.get("hostname") or "(none)",
        config.get("port"),
        "set" if config.get("ca") else "none",
    )

    base_result = {
        "field_count": len(managed_fields),
        "fast_field_count": fast_field_count,
        "slow_field_count": slow_field_count,
        "telemetry_mode": policy_context.telemetry_mode,
        "policy_key": policy_context.policy_key,
        "reason": reason,
    }

    try:
        _LOGGER.info(
            "fleet_telemetry_config pushed for %s: mode=%s reason=%s total=%s fast=%s slow=%s",
            vin,
            policy_context.telemetry_mode,
            reason,
            len(managed_fields),
            fast_field_count,
            slow_field_count,
        )
        return await _do_push_and_sync(
            proxy_client,
            vin,
            access_token,
            config,
            base_result,
            wait_for_sync,
            seen_error_keys,
            sync_status_callback,
            hass,
        )
    except TokenRevokedError as err:
        raise ConfigEntryAuthFailed(
            "Tesla OAuth token was revoked — re-authentication required"
        ) from err
    except RateLimitedError as err:
        return _build_push_result(
            base_result,
            pushed=False,
            rate_limited=True,
            retry_after=err.retry_after,
            error=str(err),
        )
    except Exception as err:
        unsupported = extract_unsupported_interval(err)
        if unsupported is not None:
            field_name, authorized_interval = unsupported
            if field_name in managed_fields:
                managed_fields[field_name]["interval_seconds"] = authorized_interval
                _LOGGER.warning(
                    "Tesla rejected %s at %ss for %s; retrying at authorized %ss",
                    field_name,
                    default_field_policy(field_name, policy_context)["interval_seconds"],
                    vin,
                    authorized_interval,
                )
                try:
                    return await _do_push_and_sync(
                        proxy_client,
                        vin,
                        access_token,
                        config,
                        base_result,
                        wait_for_sync,
                        seen_error_keys,
                        sync_status_callback,
                        hass,
                        retried_field=field_name,
                        authorized_interval=authorized_interval,
                    )
                except Exception as retry_err:
                    err = retry_err
        _log_fleet_config_push_error(vin, err)
        if _is_fatal_fleet_config_push_error(err):
            raise HomeAssistantError(
                "Tesla rejected the fleet telemetry configuration; check generated payload fields, "
                "telemetry endpoint, port, and CA certificate"
            ) from err
        return _build_push_result(base_result, pushed=False, error=str(err))
