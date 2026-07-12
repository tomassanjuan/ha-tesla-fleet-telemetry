"""Switch platform — Sentry Mode and Charge enable."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import CommandEntity
from .coordinator import TelemetryCoordinator
from .entity_base import telemetry_unique_id
from .oauth2 import TeslaOAuth2Implementation
from .proxy_client import TeslaProxyClient
from .utils import coerce_bool, normalize_enum


def _charge_state_is_on(value: object) -> bool | None:
    """Map DetailedChargeState enum to a switch state."""
    state = normalize_enum(value)
    if state is None:
        return None
    lowered = state.lower()
    if lowered.startswith("detailedchargestate"):
        lowered = lowered[len("detailedchargestate") :].strip()
    if lowered in {"starting", "charging"}:
        return True
    if lowered in {
        "disconnected",
        "nopower",
        "complete",
        "stopped",
        "idle",
        "unknown",
    }:
        return False
    return None


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
            SentryModeSwitch(coordinator, vin, proxy_client, impl, entry),
            ChargeSwitch(coordinator, vin, proxy_client, impl, entry),
        ]
    )


class SentryModeSwitch(CommandEntity, SwitchEntity):
    """Toggle Sentry Mode."""

    _attr_name = "Sentry"
    _attr_icon = "mdi:shield-car"
    _state_fields = ("SentryMode",)
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "sentry")

    @property
    def is_on(self) -> bool | None:
        val = self.coordinator.get_field("SentryMode")
        return coerce_bool(val)

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on is True:
            return
        await self._send_command("set_sentry_mode", {"on": True})
        self.coordinator.async_update_field("SentryMode", True)

    async def async_turn_off(self, **kwargs) -> None:
        if self.is_on is False:
            return
        await self._send_command("set_sentry_mode", {"on": False})
        self.coordinator.async_update_field("SentryMode", False)


class ChargeSwitch(CommandEntity, SwitchEntity):
    """Start or stop charging."""

    _attr_name = "Charging"
    _attr_icon = "mdi:ev-plug-type2"
    _state_fields = ("DetailedChargeState",)
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "charge_switch")

    @property
    def is_on(self) -> bool | None:
        return _charge_state_is_on(self.coordinator.get_field("DetailedChargeState"))

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on is True:
            return
        await self._send_command("charge_start")
        self.coordinator.async_update_field("DetailedChargeState", "DetailedChargeStateCharging")

    async def async_turn_off(self, **kwargs) -> None:
        if self.is_on is False:
            return
        await self._send_command("charge_stop")
        self.coordinator.async_update_field("DetailedChargeState", "DetailedChargeStateStopped")
