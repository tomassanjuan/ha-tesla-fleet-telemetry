"""Number platform — Charge limit and Charge amps."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import CommandEntity
from .coordinator import TelemetryCoordinator
from .entity_base import telemetry_unique_id
from .oauth2 import TeslaOAuth2Implementation
from .proxy_client import TeslaProxyClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = entry.runtime_data
    coordinator: TelemetryCoordinator = entry_data.coordinator
    proxy_client: TeslaProxyClient = entry_data.proxy_client
    impl: TeslaOAuth2Implementation = entry_data.implementation
    vin: str = entry_data.vin

    async_add_entities(
        [
            ChargeLimitNumber(coordinator, vin, proxy_client, impl, entry),
            ChargeAmpsNumber(coordinator, vin, proxy_client, impl, entry),
        ]
    )


class ChargeLimitNumber(CommandEntity, NumberEntity):
    """Set charge limit (50–100%)."""

    _attr_name = "Charge Limit"
    _attr_icon = "mdi:battery-charging-80"
    _state_fields = ("ChargeLimitSoc",)
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "charge_limit")

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.get_field("ChargeLimitSoc")
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self._send_command("set_charge_limit", {"percent": int(value)})


class ChargeAmpsNumber(CommandEntity, NumberEntity):
    """Set charge amps (5–32A).

    native_max_value is cached in _attr_native_max_value and updated only when
    ChargeCurrentRequestMax actually changes. This avoids HA logging "capabilities
    updated too often" which happens when a dynamic native_max_value property is
    evaluated on every coordinator update (every MQTT message).
    """

    _attr_name = "Charge Amps"
    _attr_icon = "mdi:current-ac"
    _state_fields = ("ChargeCurrentRequest", "ChargeCurrentRequestMax")
    _attr_native_min_value = 5
    _attr_native_max_value = 32.0
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "A"
    _attr_mode = NumberMode.SLIDER
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "charge_amps")
        self._pending_max_update: asyncio.TimerHandle | None = None

    async def async_will_remove_from_hass(self) -> None:
        if self._pending_max_update is not None:
            self._pending_max_update.cancel()
            self._pending_max_update = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_field_listener(
                "ChargeCurrentRequestMax", self._on_max_changed
            )
        )

    @callback
    def _on_max_changed(self, field: str, value: Any) -> None:
        if not isinstance(value, int | float):
            return
        new_max = float(max(self._attr_native_min_value, min(int(value), 32)))
        if new_max == self._attr_native_max_value:
            return
        self._attr_native_max_value = new_max
        # Debounce: wait 1 s before writing state so bursts (e.g. MQTT reconnect)
        # only trigger one capability update instead of many.
        if self._pending_max_update is not None:
            self._pending_max_update.cancel()
        self._pending_max_update = self.hass.loop.call_later(1.0, self.async_write_ha_state)

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.get_field("ChargeCurrentRequest")
        if not isinstance(val, int | float):
            return None
        amps = int(val)
        if amps < self._attr_native_min_value:
            return None
        return float(min(amps, int(self._attr_native_max_value)))

    async def async_set_native_value(self, value: float) -> None:
        amps = int(max(self._attr_native_min_value, min(value, self._attr_native_max_value)))
        await self._send_command("set_charging_amps", {"charging_amps": amps})
        self.coordinator.async_update_field("ChargeCurrentRequest", amps)
