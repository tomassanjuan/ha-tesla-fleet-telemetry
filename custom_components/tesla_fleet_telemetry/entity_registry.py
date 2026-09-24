"""Entity registry synchronisation helpers for Tesla Fleet Telemetry."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_VIN,
    DERIVED_SENSOR_UNIQUE_ID_SUFFIXES,
    DEVICE_METADATA_FIELDS,
    ENTITY_FIELD_MAPPINGS,
    FIELD_DEFS,
    MIN_AUTOMATIC_PUSH_INTERVAL_SECONDS,
    SUPPORTED_STREAMING_FIELDS,
)

_LOGGER = logging.getLogger(__name__)

# Module-level lowercase field lookup dict for O(1) field name matching.
# Built once at import time to replace O(n) iteration in _fields_for_unique_id.
_FIELD_DEFS_LOWER = {name.lower(): name for name in FIELD_DEFS}


def _unique_id_suffix(unique_id: str, vin: str) -> str | None:
    """Return entity key from a telemetry_{vin}_{name} unique_id, or None."""
    prefix = f"telemetry_{vin}_"
    if unique_id.startswith(prefix):
        return unique_id[len(prefix) :]
    return None


def _fields_for_unique_id(unique_id: str, vin: str) -> set[str]:
    suffix = _unique_id_suffix(unique_id, vin)
    if suffix is None:
        return set()
    mapping = ENTITY_FIELD_MAPPINGS.get(suffix)
    if mapping is not None:
        return set(mapping.fields)
    suffix_lower = suffix.lower()
    if suffix_lower in _FIELD_DEFS_LOWER:
        return {_FIELD_DEFS_LOWER[suffix_lower]}
    return set()


def _enable_dependency_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    fields: set[str],
) -> int:
    """Enable raw sensor entities needed by an enabled derived sensor."""
    if not fields:
        return 0

    vin = entry.data[CONF_VIN]
    registry = er.async_get(hass)
    wanted_suffixes = {field.lower() for field in fields}
    changed = 0

    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        suffix = _unique_id_suffix(entity_entry.unique_id, vin)
        if suffix is None or suffix.lower() not in wanted_suffixes:
            continue
        if entity_entry.disabled_by is None:
            continue
        registry.async_update_entity(entity_entry.entity_id, disabled_by=None)
        changed += 1

    if changed:
        _LOGGER.info(
            "Enabled %s dependency entities for derived telemetry sensors",
            changed,
        )
    return changed


def _default_enabled_for_unique_id(unique_id: str, vin: str) -> bool | None:
    """Return default-enabled status for a unique_id, or None if unknown."""
    suffix = _unique_id_suffix(unique_id, vin)
    if suffix is None:
        return None
    mapping = ENTITY_FIELD_MAPPINGS.get(suffix)
    if mapping is not None:
        return mapping.enabled_by_default
    suffix_lower = suffix.lower()
    if suffix_lower in _FIELD_DEFS_LOWER:
        field_name = _FIELD_DEFS_LOWER[suffix_lower]
        desc = FIELD_DEFS[field_name]
        return bool(getattr(desc, "entity_registry_enabled_default", False))
    return None


def _base_required_fields() -> set[str]:
    """Return telemetry fields required for integration metadata."""
    return set(DEVICE_METADATA_FIELDS)


def _entity_required_fields(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> set[str]:
    """Build required telemetry field set from enabled entities in registry."""
    vin = entry.data[CONF_VIN]
    registry = er.async_get(hass)
    entity_entries = list(er.async_entries_for_config_entry(registry, entry.entry_id))
    required: set[str] = set()

    for entity_entry in entity_entries:
        if entity_entry.disabled_by is not None:
            continue
        mapped_fields = _fields_for_unique_id(entity_entry.unique_id, vin)
        if mapped_fields:
            required.update(mapped_fields)
    _LOGGER.debug(
        "Resolved %s entity-driven telemetry fields for %s",
        len(required),
        vin,
    )
    return required


def _resolved_required_fields(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> set[str]:
    """Return final supported telemetry field set managed by the integration."""
    vin = entry.data[CONF_VIN]
    required = _base_required_fields()
    required.update(_entity_required_fields(hass, entry))
    resolved = {
        field for field in required if field in FIELD_DEFS and field in SUPPORTED_STREAMING_FIELDS
    }
    _LOGGER.debug(
        "Resolved %s required telemetry fields from enabled entities for %s",
        len(resolved),
        vin,
    )
    return resolved


def _register_entity_registry_sync_listener(  # noqa: C901
    hass: HomeAssistant,
    entry: ConfigEntry,
):
    """Resync fleet_telemetry_config when entities are enabled/disabled."""

    vin = entry.data[CONF_VIN]
    _debounce_handle: asyncio.TimerHandle | None = None
    _unloaded = False

    @callback
    def _schedule_resync(delay: float) -> None:
        nonlocal _debounce_handle
        if _unloaded:
            return
        if _debounce_handle is not None:
            _debounce_handle.cancel()
        _debounce_handle = hass.loop.call_later(delay, lambda: hass.async_create_task(_resync()))

    def _throttle_remaining(policy_state) -> float:
        """Seconds until an automatic push would no longer be throttled."""
        now = hass.loop.time()
        remaining = policy_state.rate_limited_until - now
        last_push = policy_state.last_push_monotonic
        if isinstance(last_push, int | float):
            remaining = max(remaining, last_push + MIN_AUTOMATIC_PUSH_INTERVAL_SECONDS - now)
        return max(remaining, 0.0)

    async def _resync() -> None:
        try:
            entry_data = getattr(entry, "runtime_data", None)
            if not entry_data:
                return
            result = await entry_data.push_telemetry_config(reason="entity_registry")
            # A throttled push would otherwise drop this change until the next
            # reload; retry once the throttle window has passed.
            if isinstance(result, dict) and result.get("skipped"):
                delay = _throttle_remaining(entry_data.policy_state) + 1.0
                _LOGGER.debug(
                    "fleet_telemetry_config resync for %s skipped (%s); retrying in %.0fs",
                    vin,
                    result.get("skip_reason"),
                    delay,
                )
                _schedule_resync(delay)
        except ConfigEntryAuthFailed:
            _LOGGER.warning(
                "Tesla authentication failed during fleet config resync for %s — "
                "re-authentication required",
                entry.data[CONF_VIN],
            )
            entry.async_start_reauth(hass)
        except Exception as err:
            _LOGGER.error("fleet_telemetry_config resync failed: %s", err)

    @callback
    def _handle_entity_registry_change(event) -> None:
        if event.data.get("action") != "update":
            return
        changes = event.data.get("changes")
        if not isinstance(changes, dict) or "disabled_by" not in changes:
            return
        entity_id = event.data.get("entity_id")
        if not entity_id:
            return
        registry = er.async_get(hass)
        entity_entry = registry.async_get(entity_id)
        if entity_entry is None:
            return
        if entity_entry.config_entry_id != entry.entry_id:
            return
        suffix = _unique_id_suffix(entity_entry.unique_id, vin)
        fields = _fields_for_unique_id(entity_entry.unique_id, vin)
        # Only react to entities owned by this integration (or mapped by unique_id).
        if not fields:
            return
        # Ignore integration-owned default enable/disable churn during setup.
        if entity_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            return
        if suffix in DERIVED_SENSOR_UNIQUE_ID_SUFFIXES and entity_entry.disabled_by is None:
            _enable_dependency_entities(hass, entry, fields)
        _LOGGER.debug(
            "Entity registry update detected for %s (%s), scheduling fleet config resync",
            entity_id,
            entity_entry.unique_id,
        )
        # Debounce: cancel any pending resync and schedule a fresh one 2 seconds out.
        # This coalesces rapid bursts of entity enable/disable into a single push.
        _schedule_resync(2.0)

    event_name = getattr(er, "EVENT_ENTITY_REGISTRY_UPDATED", "entity_registry_updated")
    unsubscribe = hass.bus.async_listen(event_name, _handle_entity_registry_change)

    @callback
    def _unload() -> None:
        nonlocal _debounce_handle, _unloaded
        _unloaded = True
        unsubscribe()
        if _debounce_handle is not None:
            _debounce_handle.cancel()
            _debounce_handle = None

    return _unload


def _sync_entity_registry_defaults(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Update existing entity registry entries to match integration defaults.

    Rules:
    - Re-enable only entries that were disabled by integration and are default-enabled.
    - Never override user-enabled or user-disabled entities.
    """
    registry = er.async_get(hass)
    vin = entry.data[CONF_VIN]
    changed = 0
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        desired = _default_enabled_for_unique_id(entity_entry.unique_id, vin)
        if desired is None:
            continue
        if desired and entity_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(
                entity_entry.entity_id,
                disabled_by=None,
            )
            changed += 1

    if changed:
        _LOGGER.info("Updated default-enabled state for %s entity registry entries", changed)
    return changed > 0
