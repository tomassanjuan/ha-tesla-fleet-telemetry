"""Tesla Fleet Telemetry integration setup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_entry_oauth2_flow, instance_id
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util.ssl import client_context

from .const import (
    BOOTSTRAP_PUSH_RETRY_DELAY_SECONDS,
    CONF_CONNECTION_TIMEOUT,
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
    CONF_VIN,
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_MQTT_TOPIC_BASE,
    DEFAULT_TELEMETRY_MODE,
    DEVICE_METADATA_FIELDS,
    DOMAIN,
    MIN_AUTOMATIC_PUSH_INTERVAL_SECONDS,
    MIN_UNIVERSAL_PUSH_INTERVAL_SECONDS,
    PLATFORMS,
    RUNTIME_INTERVAL_OVERRIDES,
    WAKE_UP_SYNC_RECHECK_DELAY_SECONDS,
)
from .coordinator import TelemetryCoordinator
from .device import device_registry_metadata
from .entity_registry import (
    _register_entity_registry_sync_listener,
    _resolved_required_fields,
    _sync_entity_registry_defaults,
)
from .fleet_config import (
    PolicyState,
    _dismiss_key_not_paired,
    _extract_fleet_config_status,
    _log_fleet_telemetry_errors,
    _notify_key_not_paired,
    _policy_context_from_options,
    _push_telemetry_config,
    _wait_for_fleet_config_sync_and_log_errors,
)
from .mqtt_client import TeslaMQTTClient
from .oauth2 import (
    TeslaOAuth2Implementation,
    async_drop_oauth_session,
    async_get_valid_access_token,
)
from .proxy_client import TeslaProxyClient
from .utils import async_fetch_server_ca, normalize_pem_cert_bundle

_LOGGER = logging.getLogger(__name__)
ASYNC_DISPATCHER_SEND = cast(Callable[[HomeAssistant, str, Any], None], async_dispatcher_send)


@dataclass
class TeslaFleetEntryData:
    """Typed container for per-entry runtime state shared across platforms."""

    coordinator: TelemetryCoordinator
    mqtt_client: TeslaMQTTClient
    proxy_client: TeslaProxyClient
    implementation: TeslaOAuth2Implementation
    vin: str
    push_telemetry_config: Callable[..., Awaitable[dict[str, Any]]]
    reload_telemetry_data: Callable[[], Awaitable[dict[str, Any]]]
    get_telemetry_config: Callable[[], Awaitable[dict[str, Any]]]
    delete_telemetry_config: Callable[[], Awaitable[dict[str, Any]]]
    get_telemetry_errors: Callable[[], Awaitable[None]]
    policy_state: PolicyState


def _sync_device_metadata(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: TelemetryCoordinator,
    vin: str,
) -> None:
    """Update device registry metadata from coordinator fields."""
    device_registry = dr.async_get(hass)
    # Scope to this entry: identifiers are not unique across config entries.
    device = next(
        (
            d
            for d in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
            if (DOMAIN, vin) in d.identifiers
        ),
        None,
    )
    if device is None:
        return

    name, model, hw_version, sw_version = device_registry_metadata(coordinator, vin)
    updates: dict[str, Any] = {}
    if name and device.name != name:
        updates["name"] = name

    current_model = device.model or None
    if model != current_model:
        updates["model"] = model

    current_hw_version = device.hw_version or None
    if hw_version != current_hw_version:
        updates["hw_version"] = hw_version

    current_sw_version = device.sw_version or None
    if sw_version != current_sw_version:
        updates["sw_version"] = sw_version

    if updates:
        device_registry.async_update_device(device.id, **updates)


def _sync_entry_title(hass: HomeAssistant, entry: ConfigEntry, vin: str) -> None:
    """Keep the config entry title stable and VIN-based."""
    title = vin
    if entry.title != title:
        hass.config_entries.async_update_entry(entry, title=title)


def _sync_key_pairing_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    vin: str,
    result: dict[str, Any],
) -> None:
    """Create or dismiss the virtual-key repair issue from a Tesla status result."""
    if result.get("key_paired") is False:
        _notify_key_not_paired(
            hass,
            vin,
            str(
                entry.data.get(CONF_DOMAIN) or entry.data.get(CONF_TELEMETRY_SERVER_HOSTNAME) or ""
            ),
        )
    elif result.get("key_paired") is True:
        _dismiss_key_not_paired(hass, vin)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:  # noqa: C901
    """Set up Tesla Fleet Telemetry from a config entry."""
    entry_data = entry.data

    # --- Validate required config keys ---
    required_keys = {CONF_VIN, CONF_MQTT_HOST, CONF_MQTT_PORT, CONF_PROXY_URL}
    missing_keys = required_keys - set(entry_data.keys())
    if missing_keys:
        _LOGGER.error("Missing required config keys: %s", ", ".join(sorted(missing_keys)))
        raise ConfigEntryError("Missing required configuration")

    vin = entry_data[CONF_VIN]

    # --- OAuth2 ---
    try:
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
        implementation = cast(TeslaOAuth2Implementation, implementation)
        await async_get_valid_access_token(hass, entry, implementation)
    except ConfigEntryAuthFailed:
        raise
    except HomeAssistantError as err:
        raise ConfigEntryNotReady(f"Tesla OAuth token refresh failed: {err}") from err

    # --- Coordinator ---
    coordinator = TelemetryCoordinator(hass, entry, vin)

    # --- MQTT client ---
    def _on_telemetry(field: str, value: Any) -> None:
        coordinator.async_update_field(field, value)
        if field in DEVICE_METADATA_FIELDS:
            _sync_device_metadata(hass, entry, coordinator, vin)

    def _on_alert(alert_code: str, alert_data: Any) -> None:
        coordinator.update_alert(alert_code, alert_data)

    _opts = {**entry.data, **entry.options}
    mqtt_host = _opts.get(CONF_MQTT_HOST, entry_data.get(CONF_MQTT_HOST))
    mqtt_port = _opts.get(CONF_MQTT_PORT, entry_data.get(CONF_MQTT_PORT))
    if not mqtt_host or not mqtt_port:
        _LOGGER.error("Invalid MQTT configuration: host=%s, port=%s", mqtt_host, mqtt_port)
        raise ConfigEntryError("Invalid MQTT configuration")
    try:
        mqtt_port = int(mqtt_port)
        connection_timeout = int(_opts.get(CONF_CONNECTION_TIMEOUT, DEFAULT_CONNECTION_TIMEOUT))
    except (TypeError, ValueError) as err:
        raise ConfigEntryError("Invalid connection configuration") from err
    connection_timeout = max(5, min(60, connection_timeout))
    if not 1 <= mqtt_port <= 65535:
        raise ConfigEntryError("Invalid MQTT port")
    topic_base = _opts.get(CONF_MQTT_TOPIC_BASE, entry_data.get(CONF_MQTT_TOPIC_BASE)) or (
        DEFAULT_MQTT_TOPIC_BASE
    )
    # Suffix the client id with this HA instance's id: brokers disconnect the
    # older of two sessions sharing an id, so a second HA instance (e.g. a dev
    # box) on the same broker would otherwise knock this one off in a loop.
    hass_instance_id = await instance_id.async_get(hass)
    mqtt_client = TeslaMQTTClient(
        hass,
        host=mqtt_host,
        port=mqtt_port,
        vin=vin,
        on_message=_on_telemetry,
        on_alert=_on_alert,
        timeout_seconds=connection_timeout,
        topic_base=topic_base,
        username=_opts.get(CONF_MQTT_USERNAME) or None,
        password=_opts.get(CONF_MQTT_PASSWORD) or None,
        ssl_context=client_context() if _opts.get(CONF_MQTT_TLS) else None,
        client_id=f"{DOMAIN}_{vin}_{hass_instance_id[:8]}",
    )

    try:
        await mqtt_client.async_connect()
    except (TimeoutError, OSError) as err:
        await mqtt_client.async_disconnect()
        raise ConfigEntryNotReady(f"MQTT connect failed: {err}") from err
    except Exception:
        await mqtt_client.async_disconnect()
        raise

    # --- Proxy client ---
    proxy_url = _opts.get(CONF_PROXY_URL, entry_data.get(CONF_PROXY_URL, ""))
    if not proxy_url:
        _LOGGER.error("Proxy URL is required but not configured")
        await mqtt_client.async_disconnect()
        raise ConfigEntryError("Proxy URL not configured")
    proxy_client = TeslaProxyClient(
        hass,
        proxy_url,
        vin,
        timeout_seconds=connection_timeout,
        verify_ssl=bool(_opts.get(CONF_PROXY_VERIFY_SSL)),
    )
    try:
        await proxy_client.async_setup()
    except Exception:
        await proxy_client.async_close()
        await mqtt_client.async_disconnect()
        raise

    policy_state = PolicyState()
    push_lock = asyncio.Lock()
    cached_telemetry_config: dict[str, Any] | None = None
    cached_telemetry_config_at: float | None = None

    def _invalidate_telemetry_config_cache() -> None:
        nonlocal cached_telemetry_config, cached_telemetry_config_at
        cached_telemetry_config = None
        cached_telemetry_config_at = None

    def _publish_config_push_status(result: dict[str, Any]) -> None:
        policy_state.last_result = result
        ASYNC_DISPATCHER_SEND(hass, f"{DOMAIN}_config_push_{entry.entry_id}", result)

    pending_sync_tasks: set[asyncio.Task] = set()

    async def _finish_pending_config_sync(
        base_result: dict[str, Any],
        access_token: str,
    ) -> dict[str, Any]:
        try:
            sync_result = await _wait_for_fleet_config_sync_and_log_errors(
                proxy_client,
                vin,
                access_token,
                policy_state.logged_telemetry_error_keys,
                _publish_config_push_status,
                hass,
            )
            result = {
                **base_result,
                "sync_pending": False,
                "synced": sync_result.get("synced"),
                "limit_reached": sync_result.get("limit_reached"),
                "key_paired": sync_result.get("key_paired"),
                "attempts": sync_result.get("attempts"),
                "wait_seconds": sync_result.get("wait_seconds"),
            }
            _publish_config_push_status(result)
            _sync_key_pairing_issue(hass, entry, vin, result)
            return result
        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            # Runs as a detached task for manual pushes: nobody awaits it, so
            # surface the auth failure here instead of as an unhandled task error.
            _LOGGER.warning(
                "Tesla authentication failed during fleet config sync for %s — "
                "re-authentication required",
                vin,
            )
            entry.async_start_reauth(hass)
            result = {**base_result, "sync_pending": False, "synced": False, "error": "auth"}
            _publish_config_push_status(result)
            return result
        except Exception as err:
            _LOGGER.debug(
                "Background fleet_telemetry_config sync check failed for %s: %s",
                vin,
                err,
            )
            result = {
                **base_result,
                "sync_pending": False,
                "synced": False,
                "attempts": 0,
                "wait_seconds": 0,
                "error": str(err),
            }
            _publish_config_push_status(result)
            return result
        finally:
            task = asyncio.current_task()
            if task is not None:
                pending_sync_tasks.discard(task)

    async def _push_entry_telemetry_config(  # noqa: C901
        reason: str = "manual", *, force: bool = False
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        now = loop.time()

        def _skip_result(skip_reason: str) -> dict[str, Any]:
            return {
                "pushed": False,
                "synced": True,
                "limit_reached": None,
                "attempts": 0,
                "wait_seconds": 0,
                "field_count": 0,
                "fast_field_count": 0,
                "slow_field_count": 0,
                "added_fields": [],
                "removed_fields": [],
                "still_missing": [],
                "response": None,
                "telemetry_mode": _opts.get(CONF_TELEMETRY_MODE, DEFAULT_TELEMETRY_MODE),
                "policy_key": _policy_context_from_options(_opts).policy_key,
                "reason": reason,
                "error": None,
                "skipped": True,
                "skip_reason": skip_reason,
            }

        # Hard rate-limit block (429 response from Tesla) — blocks all pushes
        if now < policy_state.rate_limited_until:
            remaining = int(policy_state.rate_limited_until - now)
            _LOGGER.warning(
                "fleet_telemetry_config push skipped for %s: rate-limited for %ss",
                vin,
                remaining,
            )
            return _skip_result(f"rate_limited_for_{remaining}s")

        # Universal minimum interval — prevent rapid-fire pushes for every reason,
        # including manual button presses. `force` is only for callers that just
        # deleted the config and must not leave the vehicle without one.
        last_push_monotonic = policy_state.last_push_monotonic
        if (
            not force
            and isinstance(last_push_monotonic, int | float)
            and now - last_push_monotonic < MIN_UNIVERSAL_PUSH_INTERVAL_SECONDS
        ):
            remaining = int(MIN_UNIVERSAL_PUSH_INTERVAL_SECONDS - (now - last_push_monotonic)) + 1
            return _skip_result(f"throttled, retry in {remaining}s")

        # Automatic-push extended minimum
        if (
            reason != "manual"
            and isinstance(last_push_monotonic, int | float)
            and now - last_push_monotonic < MIN_AUTOMATIC_PUSH_INTERVAL_SECONDS
        ):
            return _skip_result("throttled_auto")

        async with push_lock:
            # Re-check throttle inside the lock to prevent a race where two callers
            # both passed the outer guard before either updated last_push_monotonic.
            now_inner = loop.time()
            last_push_inner = policy_state.last_push_monotonic
            if (
                not force
                and isinstance(last_push_inner, int | float)
                and now_inner - last_push_inner < MIN_UNIVERSAL_PUSH_INTERVAL_SECONDS
            ):
                return _skip_result("throttled")
            if (
                reason != "manual"
                and isinstance(last_push_inner, int | float)
                and now_inner - last_push_inner < MIN_AUTOMATIC_PUSH_INTERVAL_SECONDS
            ):
                return _skip_result("throttled_auto")

            effective_options = dict(_opts)
            effective_options[RUNTIME_INTERVAL_OVERRIDES] = dict(
                policy_state.authorized_interval_overrides
            )
            context = _policy_context_from_options(effective_options)
            try:
                access_token, _ = await async_get_valid_access_token(hass, entry, implementation)
            except ConfigEntryAuthFailed:
                entry.async_start_reauth(hass)
                raise
            required_fields = _resolved_required_fields(hass, entry)
            _publish_config_push_status(
                {
                    "pushed": False,
                    "synced": False,
                    "limit_reached": None,
                    "key_paired": None,
                    "attempts": 0,
                    "wait_seconds": 0,
                    "reason": reason,
                    "error": None,
                }
            )
            result = await _push_telemetry_config(
                proxy_client,
                vin,
                access_token,
                required_fields,
                context,
                reason=reason,
                seen_error_keys=policy_state.logged_telemetry_error_keys,
                sync_status_callback=_publish_config_push_status,
                wait_for_sync=False,
                hass=hass,
            )
            _invalidate_telemetry_config_cache()
            policy_state.last_push_monotonic = loop.time()
            policy_state.last_push_dt = datetime.now(UTC)
            # Detect 429 rate-limit in push result and set hard block
            retry_after = result.get("retry_after")
            if isinstance(retry_after, int) and retry_after > 0:
                policy_state.rate_limited_until = loop.time() + retry_after
                _LOGGER.warning(
                    "fleet_telemetry_config rate-limited for %s: blocking for %ss",
                    vin,
                    retry_after,
                )
            overrides = result.get("authorized_interval_overrides")
            if isinstance(overrides, dict):
                for field_name, interval in overrides.items():
                    if isinstance(field_name, str) and isinstance(interval, int):
                        policy_state.authorized_interval_overrides[field_name] = interval
            policy_state.last_policy_key = context.policy_key
            _publish_config_push_status(result)
            _sync_key_pairing_issue(hass, entry, vin, result)
        if result.get("sync_pending") is True:
            if reason == "manual":
                sync_task = hass.async_create_task(
                    _finish_pending_config_sync(result, access_token)
                )
                pending_sync_tasks.add(sync_task)
            else:
                result = await _finish_pending_config_sync(result, access_token)
        return result

    # --- Store references for platforms ---
    async def _get_entry_telemetry_config() -> dict[str, Any]:
        nonlocal cached_telemetry_config, cached_telemetry_config_at
        if (
            cached_telemetry_config is not None
            and cached_telemetry_config_at is not None
            and asyncio.get_running_loop().time() - cached_telemetry_config_at < 10
        ):
            _LOGGER.debug("Returning cached fleet_telemetry_config for %s", vin)
            return cached_telemetry_config

        access_token, _ = await async_get_valid_access_token(hass, entry, implementation)
        result = await proxy_client.proxy_fleet_telemetry_get(access_token)
        _LOGGER.debug("fleet_telemetry_config fetched for %s", vin)
        status = _extract_fleet_config_status(result)
        status_result = {
            "pushed": False,
            "synced": status.get("synced"),
            "limit_reached": status.get("limit_reached"),
            "key_paired": status.get("key_paired"),
        }
        policy_state.last_result = status_result
        ASYNC_DISPATCHER_SEND(hass, f"{DOMAIN}_config_push_{entry.entry_id}", status_result)
        cached_telemetry_config = result
        cached_telemetry_config_at = asyncio.get_running_loop().time()
        return result

    async def _reload_entry_telemetry_data() -> dict[str, Any]:
        await _delete_entry_telemetry_config()
        await _push_entry_telemetry_config(reason="manual", force=True)
        return await _get_entry_telemetry_config()

    async def _delete_entry_telemetry_config() -> dict[str, Any]:
        _invalidate_telemetry_config_cache()
        access_token, _ = await async_get_valid_access_token(hass, entry, implementation)
        result = await proxy_client.proxy_fleet_telemetry_delete(access_token)
        deleted_result: dict[str, Any] = {
            "pushed": False,
            "synced": False,
            "limit_reached": None,
            "key_paired": None,
        }
        policy_state.last_result = deleted_result
        ASYNC_DISPATCHER_SEND(hass, f"{DOMAIN}_config_push_{entry.entry_id}", deleted_result)
        return result

    async def _get_entry_fleet_telemetry_errors() -> None:
        access_token, _ = await async_get_valid_access_token(hass, entry, implementation)
        await _log_fleet_telemetry_errors(
            proxy_client,
            vin,
            access_token,
            policy_state.logged_telemetry_error_keys,
            hass,
        )

    async def _refresh_telemetry_ca() -> None:
        """Replace the stored telemetry CA when the server's trusted chain changed.

        Entries created before the full chain was stored pinned only the leaf
        certificate, which vehicles stop trusting when it renews. Runs before the
        startup push so that push carries the current chain.
        """
        hostname = _opts.get(CONF_TELEMETRY_SERVER_HOSTNAME)
        port = _opts.get(CONF_TELEMETRY_SERVER_PORT)
        if not hostname or not isinstance(port, int):
            return
        try:
            pem, trusted = await async_fetch_server_ca(hass, hostname, port)
        except (OSError, ValueError) as err:
            _LOGGER.debug("Telemetry server CA check failed for %s:%s: %s", hostname, port, err)
            return
        if not trusted:
            return
        try:
            current = normalize_pem_cert_bundle(_opts.get(CONF_TELEMETRY_SERVER_CA, ""))
        except ValueError:
            current = ""
        if pem == current:
            return
        _LOGGER.info(
            "Telemetry server certificate chain for %s:%s changed; updating stored CA",
            hostname,
            port,
        )
        _opts[CONF_TELEMETRY_SERVER_CA] = pem
        if CONF_TELEMETRY_SERVER_CA in entry.options:
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_TELEMETRY_SERVER_CA: pem}
            )
        else:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_TELEMETRY_SERVER_CA: pem}
            )

    async def _wake_entry_vehicle() -> None:
        access_token, _ = await async_get_valid_access_token(hass, entry, implementation)
        await proxy_client.wake_vehicle(access_token)

    bootstrap_task: asyncio.Task | None = None

    async def _cancel_bootstrap() -> None:
        if bootstrap_task is None or bootstrap_task.done():
            return
        bootstrap_task.cancel()
        await asyncio.gather(bootstrap_task, return_exceptions=True)

    async def _cancel_pending_sync_tasks() -> None:
        tasks = tuple(pending_sync_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    runtime_data = TeslaFleetEntryData(
        coordinator=coordinator,
        mqtt_client=mqtt_client,
        proxy_client=proxy_client,
        implementation=implementation,
        vin=vin,
        push_telemetry_config=_push_entry_telemetry_config,
        reload_telemetry_data=_reload_entry_telemetry_data,
        get_telemetry_config=_get_entry_telemetry_config,
        delete_telemetry_config=_delete_entry_telemetry_config,
        get_telemetry_errors=_get_entry_fleet_telemetry_errors,
        policy_state=policy_state,
    )
    entry.runtime_data = runtime_data

    # --- Load platforms ---
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _sync_device_metadata(hass, entry, coordinator, vin)
        _sync_entry_title(hass, entry, vin)

        # Enforce default enabled/disabled policy after entities are created.
        _sync_entity_registry_defaults(hass, entry)
        bootstrap_task = hass.async_create_task(
            _async_run_setup_bootstrap(
                hass=hass,
                entry=entry,
                coordinator=coordinator,
                vin=vin,
                push_entry_telemetry_config=_push_entry_telemetry_config,
                get_entry_telemetry_config=_get_entry_telemetry_config,
                wake_entry_vehicle=_wake_entry_vehicle,
                refresh_telemetry_ca=_refresh_telemetry_ca,
            )
        )
    except Exception:
        await _cancel_bootstrap()
        await _cancel_pending_sync_tasks()
        await proxy_client.async_close()
        await mqtt_client.async_disconnect()
        raise

    # --- Teardown ---
    # No update listener: OAuth token refreshes write entry.data every few hours,
    # and reloading on that tore down the proxy session under in-flight commands.
    # Options changes reload via OptionsFlowWithReload; reauth via
    # async_update_reload_and_abort.
    entry.async_on_unload(_register_entity_registry_sync_listener(hass, entry))
    entry.async_on_unload(coordinator.async_shutdown)
    entry.async_on_unload(_cancel_bootstrap)
    entry.async_on_unload(_cancel_pending_sync_tasks)
    entry.async_on_unload(mqtt_client.async_disconnect)
    entry.async_on_unload(proxy_client.async_close)

    return True


async def _async_run_setup_bootstrap(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: TelemetryCoordinator,
    vin: str,
    push_entry_telemetry_config,
    get_entry_telemetry_config,
    wake_entry_vehicle,
    refresh_telemetry_ca,
) -> None:
    """Finish slow post-setup work without blocking config entry load."""
    try:
        _LOGGER.debug("Starting background setup bootstrap for %s", vin)

        await refresh_telemetry_ca()

        push_result = await push_entry_telemetry_config(reason="setup")
        # Entity registry entries can still be settling when this first push fires,
        # so it can race ahead of them and resolve only the always-on metadata
        # fields. Retry once so it picks up the real enabled-entity field set.
        fields_are_fallback_only = push_result.get("pushed") and push_result.get(
            "field_count"
        ) <= len(DEVICE_METADATA_FIELDS)
        # Retry push once if it failed (likely proxy went down after initial check)
        # or only resolved the fallback metadata fields (entity registry race).
        if not push_result.get("rate_limited") and (
            (not push_result.get("pushed") and not push_result.get("skipped"))
            or fields_are_fallback_only
        ):
            _LOGGER.warning(
                "fleet_telemetry_config push during bootstrap for %s %s — retrying in %ss",
                vin,
                "failed"
                if not push_result.get("pushed")
                else "only resolved fallback metadata fields",
                BOOTSTRAP_PUSH_RETRY_DELAY_SECONDS,
            )
            await asyncio.sleep(BOOTSTRAP_PUSH_RETRY_DELAY_SECONDS)
            push_result = await push_entry_telemetry_config(reason="setup_retry")
            if not push_result.get("pushed") and not push_result.get("skipped"):
                _LOGGER.warning(
                    "fleet_telemetry_config push still failing for %s "
                    "— leaving integration loaded",
                    vin,
                )

        if push_result.get("synced") is not True and _should_attempt_wake(coordinator, push_result):
            _LOGGER.warning(
                "fleet_telemetry_config for %s did not sync during setup; sending one wake_up",
                vin,
            )
            try:
                await wake_entry_vehicle()
                _LOGGER.info(
                    "wake_up sent for %s; waiting %ss before rechecking fleet telemetry sync",
                    vin,
                    WAKE_UP_SYNC_RECHECK_DELAY_SECONDS,
                )
                await asyncio.sleep(WAKE_UP_SYNC_RECHECK_DELAY_SECONDS)
                try:
                    recheck = await get_entry_telemetry_config()
                    status = _extract_fleet_config_status(recheck)
                    _LOGGER.info(
                        "fleet_telemetry_config rechecked for %s after wake_up: "
                        "synced=%s key_paired=%s limit_reached=%s",
                        vin,
                        status.get("synced"),
                        status.get("key_paired"),
                        status.get("limit_reached"),
                    )
                except (TimeoutError, OSError) as err:
                    _LOGGER.debug(
                        "Post-wake fleet_telemetry_config check failed for %s: %s",
                        vin,
                        err,
                    )
            except (TimeoutError, OSError) as err:
                _LOGGER.debug("Setup wake_up failed for %s: %s", vin, err)

        _sync_device_metadata(hass, entry, coordinator, vin)
        _LOGGER.debug("Completed background setup bootstrap for %s", vin)
    except asyncio.CancelledError:
        raise
    except ConfigEntryAuthFailed:
        _LOGGER.warning(
            "Tesla authentication failed during setup bootstrap for %s — "
            "re-authentication required",
            vin,
        )
        entry.async_start_reauth(hass)
    except Exception as err:
        _LOGGER.warning("Background setup bootstrap failed for %s: %s", vin, err)


def _should_attempt_wake(
    coordinator: TelemetryCoordinator,
    push_result: dict,
) -> bool:
    """Return True when a wake_up attempt is warranted after a failed sync.

    Virtual key must be paired; attempt wake if connectivity shows offline/asleep
    or is not yet available.
    """
    if push_result.get("key_paired") is not True:
        return False
    connectivity = coordinator.get_field("ConnectivityStatus")
    if connectivity is None:
        return True
    normalized = str(connectivity).strip().lower()
    return any(marker in normalized for marker in ("disconnected", "offline", "asleep"))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    unloaded = bool(await hass.config_entries.async_unload_platforms(entry, PLATFORMS))
    if unloaded:
        async_drop_oauth_session(hass, entry)
    return unloaded
