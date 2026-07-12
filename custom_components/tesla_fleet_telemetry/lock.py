"""Lock platform — vehicle lock/unlock control."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import CommandEntity
from .coordinator import TelemetryCoordinator
from .entity_base import telemetry_unique_id
from .oauth2 import TeslaOAuth2Implementation
from .proxy_client import TeslaProxyClient
from .utils import coerce_bool, normalize_enum


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
            TeslaVehicleLock(coordinator, vin, proxy_client, impl, entry),
            TeslaRemoteStartLock(coordinator, vin, proxy_client, impl, entry),
        ]
    )


class TeslaVehicleLock(CommandEntity, LockEntity):
    """Vehicle central lock entity."""

    _attr_name = "Lock"
    _attr_icon = "mdi:car-door-lock"
    _state_fields = ("Locked",)
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "lock")

    @property
    def is_locked(self) -> bool | None:
        value = self.coordinator.get_field("Locked")
        if isinstance(value, str):
            value = normalize_enum(value, prefixes=("LockState",))
        return coerce_bool(value)

    async def async_lock(self, **kwargs) -> None:
        await self._send_command("door_lock")

    async def async_unlock(self, **kwargs) -> None:
        await self._send_command("door_unlock")


class TeslaRemoteStartLock(CommandEntity, LockEntity):
    """Remote start control as a lock-like safety action."""

    _attr_name = "Keyless"
    _attr_icon = "mdi:car-key"
    _state_fields = ("RemoteStartEnabled",)
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "remote_start_lock")

    @property
    def is_locked(self) -> bool | None:
        return coerce_bool(self.coordinator.get_field("RemoteStartEnabled"))

    async def async_unlock(self, **kwargs) -> None:
        await self._send_command("remote_start_drive")

    async def async_lock(self, **kwargs) -> None:
        # Tesla does not expose a reliable explicit disable command here.
        raise HomeAssistantError("Remote Start cannot be force-disabled; it expires automatically.")
