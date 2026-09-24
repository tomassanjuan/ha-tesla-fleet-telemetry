"""Cover platform — charge port door, trunks, and windows."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import CommandEntity
from .coordinator import TelemetryCoordinator
from .entity_base import telemetry_unique_id
from .oauth2 import TeslaOAuth2Implementation
from .proxy_client import TeslaProxyClient
from .utils import coerce_bool, door_state_tokens, normalize_enum


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
            TeslaChargePortCover(coordinator, vin, proxy_client, impl, entry),
            TeslaFrontTrunkCover(coordinator, vin, proxy_client, impl, entry),
            TeslaRearTrunkCover(coordinator, vin, proxy_client, impl, entry),
            TeslaWindowsCover(coordinator, vin, proxy_client, impl, entry),
        ]
    )


class TeslaChargePortCover(CommandEntity, CoverEntity):
    """Charge port door cover with real state from telemetry."""

    _attr_name = "Charge Port Door"
    _attr_icon = "mdi:ev-plug-type2"
    _state_fields = ("ChargePortDoorOpen",)
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "charge_port_cover")

    @property
    def is_closed(self) -> bool | None:
        value = self.coordinator.get_field("ChargePortDoorOpen")
        is_open = coerce_bool(value)
        if is_open is None:
            return None
        return not is_open

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send_command("charge_port_door_open")

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send_command("charge_port_door_close")


class TeslaFrontTrunkCover(CommandEntity, CoverEntity):
    """Front trunk cover with state from DoorState telemetry."""

    _attr_name = "Frunk"
    _attr_icon = "mdi:car"
    _state_fields = ("DoorState",)
    _attr_supported_features = CoverEntityFeature.OPEN
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "frunk")

    @property
    def is_closed(self) -> bool | None:
        door_state = self.coordinator.get_field("DoorState")
        if door_state is None:
            return None
        tokens = door_state_tokens(door_state)
        return not any(token in tokens for token in {"ft", "frunk", "front_trunk"})

    async def async_open_cover(self, **kwargs: Any) -> None:
        state = self.is_closed
        if state is None:
            raise HomeAssistantError("Cannot open frunk: DoorState is unavailable")
        if not state:
            return
        await self._send_command("actuate_trunk", {"which_trunk": "front"})


class TeslaRearTrunkCover(CommandEntity, CoverEntity):
    """Rear trunk cover with state from DoorState telemetry."""

    _attr_name = "Trunk"
    _attr_icon = "mdi:car-back"
    _state_fields = ("DoorState",)
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "trunk")

    @property
    def is_closed(self) -> bool | None:
        door_state = self.coordinator.get_field("DoorState")
        if door_state is None:
            return None
        tokens = door_state_tokens(door_state)
        return not any(token in tokens for token in {"rt", "trunk", "rear_trunk", "liftgate"})

    async def async_open_cover(self, **kwargs: Any) -> None:
        state = self.is_closed
        if state is None:
            raise HomeAssistantError("Cannot open trunk: DoorState is unavailable")
        if not state:
            return
        await self._send_command("actuate_trunk", {"which_trunk": "rear"})

    async def async_close_cover(self, **kwargs: Any) -> None:
        state = self.is_closed
        if state is None:
            raise HomeAssistantError("Cannot close trunk: DoorState is unavailable")
        if state:
            return
        await self._send_command("actuate_trunk", {"which_trunk": "rear"})


class TeslaWindowsCover(CommandEntity, CoverEntity):
    """Window vent/close as a cover entity (optimistic state)."""

    _attr_name = "Windows"
    _attr_icon = "mdi:car-door"
    _state_fields = ("FdWindow", "FpWindow", "RdWindow", "RpWindow", "Location")
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_entity_registry_enabled_default = True
    _attr_assumed_state = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "windows")
        self._optimistic_is_closed: bool | None = None

    @property
    def is_closed(self) -> bool | None:
        window_values = (
            self.coordinator.get_field("FdWindow"),
            self.coordinator.get_field("FpWindow"),
            self.coordinator.get_field("RdWindow"),
            self.coordinator.get_field("RpWindow"),
        )
        known = [value for value in window_values if value is not None]
        if known:
            return not any(_window_is_open(value) for value in known)
        return self._optimistic_is_closed

    def _window_command_body(self, command: str) -> dict[str, Any]:
        # Tesla requires the requester's lat/lon near the car to *close* windows
        # (vent ignores them); the vehicle's own location satisfies that check.
        body: dict[str, Any] = {"command": command}
        location = self.coordinator.get_field("Location")
        if isinstance(location, dict):
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            if latitude is not None and longitude is not None:
                body["lat"] = latitude
                body["lon"] = longitude
        return body

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send_command("window_control", self._window_command_body("vent"))
        self._optimistic_is_closed = False
        for field in ("FdWindow", "FpWindow", "RdWindow", "RpWindow"):
            self.coordinator.async_update_field(field, "WindowStateVented")

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send_command("window_control", self._window_command_body("close"))
        self._optimistic_is_closed = True
        for field in ("FdWindow", "FpWindow", "RdWindow", "RpWindow"):
            self.coordinator.async_update_field(field, "WindowStateClosed")


def _window_is_open(value: object) -> bool:
    """Return True when a window telemetry value indicates open/not fully closed."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value) > 0
    if isinstance(value, str):
        normalized = normalize_enum(value, prefixes=("WindowState",))
        lowered = (
            normalized.lower()
            if normalized is not None
            else value.strip().strip('"').strip("'").lower()
        )
        if lowered in {"open", "opened", "partiallyopen", "vent", "vented", "true", "on"}:
            return True
        if lowered in {"closed", "close", "false", "off"}:
            return False
        try:
            return float(lowered) > 0
        except ValueError:
            return False
    return False
