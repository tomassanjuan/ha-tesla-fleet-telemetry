"""TelemetryCoordinator — push-only state store fed by MQTT messages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, FIELD_DEFS

_LOGGER = logging.getLogger(__name__)

_BURST_DEBOUNCE_SECONDS = 0.05


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
        # Seed data so coordinator is not None before first MQTT message
        self.data = {}

    def get_field(self, field: str) -> Any:
        """Return current value for a field, or None if not yet received."""
        return self.data.get(field)

    @callback
    def async_update_field(self, field: str, value: Any) -> None:
        """Update a telemetry field, notify listeners, and signal waiters."""
        if field not in FIELD_DEFS:
            _LOGGER.warning("Unknown field updated: %s, ignoring", field)
            return
        if self.data.get(field) == value:
            return
        self.data = {**self.data, field: value}
        if value is not None:
            self._field_events.setdefault(field, asyncio.Event()).set()
        self._notify_field_listeners(field, value)
        self._schedule_coordinator_update()

    @callback
    def _schedule_coordinator_update(self) -> None:
        """Notify coordinator listeners once for a burst of field changes."""
        if self._notify_handle is not None:
            self._notify_handle.cancel()
        self._notify_handle = self.hass.loop.call_later(
            _BURST_DEBOUNCE_SECONDS, self._flush_coordinator_update
        )

    @callback
    def _flush_coordinator_update(self) -> None:
        self._notify_handle = None
        self.last_update_success = True
        self.async_update_listeners()

    @callback
    def async_shutdown(self) -> None:
        """Cancel delayed listener updates during entry unload."""
        if self._notify_handle is not None:
            self._notify_handle.cancel()
            self._notify_handle = None

    @callback
    def _notify_field_listeners(self, field: str, value: Any) -> None:
        for listener in list(self._field_listeners.get(field, [])):
            try:
                listener(field, value)
            except Exception as e:
                _LOGGER.error("Error in field listener for field %s: %s", field, e)

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
        if self._last_alert_timestamp is None or started_at > self._last_alert_timestamp:
            self._last_alert_timestamp = started_at
            values = dict(self.data)
            values["LastAlertTriggered"] = alert_code
            self.data = values
            self._notify_field_listeners("LastAlertTriggered", alert_code)
            self._schedule_coordinator_update()

    async def _async_update_data(self) -> dict[str, Any]:
        """Not used — coordinator is push-only. Required by base class."""
        return dict(self.data)
