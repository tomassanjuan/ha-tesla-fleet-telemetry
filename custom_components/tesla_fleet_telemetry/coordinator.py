"""TelemetryCoordinator — push-only state store fed by MQTT messages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
)
from .descriptions import (
    FIELD_DEFS,
    TPMS_WHEEL_FIELDS,
)

_LOGGER = logging.getLogger(__name__)

_BURST_DEBOUNCE_SECONDS = 0.05

# Parent TireLocation field -> [(per-tyre child field, tyre key)].
_TPMS_CHILDREN: dict[str, list[tuple[str, str]]] = {}
for _child, (_parent, _tyre) in TPMS_WHEEL_FIELDS.items():
    _TPMS_CHILDREN.setdefault(_parent, []).append((_child, _tyre))


class TelemetryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Push-only coordinator. No polling — MQTT messages call async_update_field."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, vin: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{vin}",
        )
        self.vin = vin
        self._field_listeners: dict[str, list[Callable[[str, Any], None]]] = {}
        self._field_events: dict[str, asyncio.Event] = {}
        self._last_alert_timestamp: str | None = None
        self._notify_handle: asyncio.TimerHandle | None = None
        self._dirty_fields: set[str] = set()
        self._unknown_fields_logged: set[str] = set()
        # Seed data so coordinator is not None before first MQTT message
        self.data = {}

    def get_field(self, field: str) -> Any:
        """Return current value for a field, or None if not yet received."""
        return self.data.get(field)

    @callback
    def async_update_field(self, field: str, value: Any) -> None:
        """Update a telemetry field, notify listeners, and signal waiters."""
        if field not in FIELD_DEFS:
            if field not in self._unknown_fields_logged:
                self._unknown_fields_logged.add(field)
                _LOGGER.warning("Unknown field updated: %s, ignoring", field)
            return
        # Store an explicit None too: "received but invalid" must be
        # distinguishable from "never received" (entities only fall back to
        # their restored state for the latter).
        if field in self.data and self.data[field] == value:
            return
        # Mutate in place: copying the whole field dict on every MQTT message
        # is O(fields) per update and nothing holds on to a previous snapshot.
        self.data[field] = value
        self._dirty_fields.add(field)
        if value is not None:
            self._field_events.setdefault(field, asyncio.Event()).set()
        self._notify_field_listeners(field, value)
        self._schedule_coordinator_update()
        for child, tyre in _TPMS_CHILDREN.get(field, ()):
            self.async_update_field(
                child, bool(value.get(tyre)) if isinstance(value, dict) else None
            )

    @callback
    def _schedule_coordinator_update(self) -> None:
        """Notify coordinator listeners once for a burst of field changes.

        The first change of a burst arms the timer and later changes ride along,
        rather than re-arming it: with a continuous stream (realtime mode) a
        trailing debounce could keep postponing the flush indefinitely.
        """
        if self._notify_handle is None:
            self._notify_handle = self.hass.loop.call_later(
                _BURST_DEBOUNCE_SECONDS, self._flush_coordinator_update
            )

    @callback
    def _flush_coordinator_update(self) -> None:
        self._notify_handle = None
        dirty, self._dirty_fields = self._dirty_fields, set()
        self.last_update_success = True
        self._async_notify_listeners(dirty)

    @callback
    def async_update_listeners(self) -> None:
        """Update all listeners, isolating failures to the entity that raised."""
        self._async_notify_listeners(None)

    @callback
    def _async_notify_listeners(self, changed_fields: set[str] | None) -> None:
        """Update listeners affected by changed_fields (all listeners when None).

        Entities register the telemetry fields they read as their listener
        context (a frozenset); those whose fields did not change are skipped so
        a burst of a few fields doesn't rewrite the state of every entity.
        Listeners without a field-set context are always updated.

        The base implementation stops at the first listener that raises, so one
        entity rejecting a telemetry value (e.g. HA's sensor state validation)
        would silently freeze every entity registered after it.
        """
        for update_callback, context in list(self._listeners.values()):
            if (
                changed_fields is not None
                and isinstance(context, frozenset)
                and context.isdisjoint(changed_fields)
            ):
                continue
            try:
                update_callback()
            except Exception:
                _LOGGER.exception("Error updating entity for %s", self.vin)

    @callback
    def async_cancel_pending_update(self) -> None:
        """Cancel a debounced listener notification that has not fired yet."""
        if self._notify_handle is not None:
            self._notify_handle.cancel()
            self._notify_handle = None
        self._dirty_fields.clear()

    async def async_shutdown(self) -> None:
        """Cancel delayed listener updates during entry unload."""
        self.async_cancel_pending_update()
        await super().async_shutdown()

    @callback
    def _notify_field_listeners(self, field: str, value: Any) -> None:
        for listener in list(self._field_listeners.get(field, [])):
            try:
                listener(field, value)
            except Exception as e:
                _LOGGER.exception("Error in field listener for field %s: %s", field, e)

    @callback
    def async_add_field_listener(
        self,
        field: str,
        listener: Callable[[str, Any], None],
    ) -> Callable[[], None]:
        """Register a per-field callback; return a callable that unregisters it."""
        listeners = self._field_listeners.setdefault(field, [])
        listeners.append(listener)

        @callback
        def _remove_listener() -> None:
            current = self._field_listeners.get(field)
            if not current:
                _LOGGER.debug("Skipping listener removal for %s: no listeners registered", field)
                return
            if listener in current:
                current.remove(listener)
            if not current:
                self._field_listeners.pop(field, None)
                _LOGGER.debug("Cleaned up listeners list for field %s", field)

        return cast(Callable[[], None], _remove_listener)

    async def async_wait_for_fields(
        self,
        fields: set[str],
        timeout: float,
    ) -> bool:
        """Block until all fields have non-None values, or timeout; return True on success."""
        pending = {field for field in fields if self.get_field(field) is None}
        if not pending:
            return True

        for field in pending:
            self._field_events.setdefault(field, asyncio.Event())

        try:
            await asyncio.wait_for(
                asyncio.gather(*(self._field_events[field].wait() for field in pending)),
                timeout=timeout,
            )
        except TimeoutError:
            return False
        finally:
            for field in pending:
                self._field_events.pop(field, None)

        return all(self.get_field(field) is not None for field in fields)

    @callback
    def update_alert(self, alert_code: str, alert_data: dict[str, Any]) -> None:
        """Record alert if it's newer than the last seen one."""
        started_at = alert_data.get("StartedAt")
        if not started_at:
            return
        started_at = str(started_at)
        if self._last_alert_timestamp is None or started_at > self._last_alert_timestamp:
            self._last_alert_timestamp = started_at
            self.data["LastAlertTriggered"] = alert_code
            self._dirty_fields.add("LastAlertTriggered")
            self._notify_field_listeners("LastAlertTriggered", alert_code)
            self._schedule_coordinator_update()

    async def _async_update_data(self) -> dict[str, Any]:
        """Not used — coordinator is push-only. Required by base class."""
        return dict(self.data)
