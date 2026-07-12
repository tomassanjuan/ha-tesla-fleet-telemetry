"""Binary sensor platform for Tesla Fleet Telemetry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DESCRIPTIONS,
    DOMAIN,
    TelemetryEntityDescription,
)
from .coordinator import TelemetryCoordinator
from .device import build_device_info
from .entity_base import TelemetryEntity, telemetry_unique_id
from .fleet_config import PolicyState
from .utils import door_state_tokens

ASYNC_DISPATCHER_CONNECT = cast(
    Callable[[HomeAssistant, str, Callable[[dict[str, Any]], None]], Callable[[], None]],
    async_dispatcher_connect,
)

_DOOR_SENSORS = (
    ("Door Front Left", "door_front_left", ("df", "fd", "front_left", "driver_front")),
    ("Door Front Right", "door_front_right", ("pf", "fp", "front_right", "passenger_front")),
    ("Door Rear Left", "door_rear_left", ("dr", "rd", "rear_left", "driver_rear")),
    ("Door Rear Right", "door_rear_right", ("pr", "rp", "rear_right", "passenger_rear")),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = entry.runtime_data
    coordinator: TelemetryCoordinator = entry_data.coordinator
    vin: str = entry_data.vin
    policy_state: PolicyState = entry_data.policy_state

    entities: list[BinarySensorEntity] = [
        TelemetryBinarySensor(coordinator, description, vin)
        for description in DESCRIPTIONS
        if description.binary and not description.has_dedicated_entity
    ]
    entities.extend(TeslaDoorStateBinarySensor(coordinator, vin, *args) for args in _DOOR_SENSORS)
    entities.extend(
        [
            TeslaAnyDoorOpenBinarySensor(coordinator, vin),
            TelemetryConfigSyncedBinarySensor(coordinator, vin, policy_state, entry.entry_id),
        ]
    )
    async_add_entities(entities)


class TelemetryBinarySensor(TelemetryEntity, BinarySensorEntity):
    """A binary sensor entity for a boolean Tesla telemetry field."""

    entity_description: BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        description: TelemetryEntityDescription,
        vin: str,
    ) -> None:
        super().__init__(coordinator, description, vin)
        self.entity_description = cast(BinarySensorEntityDescription, description)

    @property
    def is_on(self) -> bool | None:
        val = self.raw_value
        if val is None:
            if self.restored_state == STATE_ON:
                return True
            if self.restored_state == STATE_OFF:
                return False
            return None

        key = self.entity_description.key
        if key == "Hvil":
            return self._is_on_hvil(val)
        if key == "ChargePortLatch":
            return self._is_on_charge_port_latch(val)
        if key == "DriveRail" and isinstance(val, str):
            return "Dual" in val

        if isinstance(val, bool):
            return val
        if isinstance(val, int | float):
            return bool(val)
        return None

    def _is_on_hvil(self, val: Any) -> bool | None:
        """Parse HVIL status."""
        if isinstance(val, str):
            lowered = self._clean_string(val).lower()
            for prefix in ("hvilstatus", "hvil"):
                if lowered.startswith(prefix):
                    lowered = lowered[len(prefix) :].strip()
                    break
            if lowered == "ok":
                return False
            if lowered in {"fault", "unknown"}:
                return True
        if isinstance(val, int | float):
            if int(val) == 2:
                return False
            if int(val) in {0, 1}:
                return True
        return None

    def _is_on_charge_port_latch(self, val: Any) -> bool | None:
        """Parse charge port latch status."""
        if not isinstance(val, str):
            return None
        lowered = self._clean_string(val).lower()
        for prefix in ("chargeportlatch", "chargeportlatchstate"):
            if lowered.startswith(prefix):
                lowered = lowered[len(prefix) :].strip()
                break
        if any(token in lowered for token in ("engaged", "latched", "closed", "locked")):
            return True
        if any(token in lowered for token in ("disengaged", "unlatched", "open", "unlocked")):
            return False
        return None

    @staticmethod
    def _clean_string(val: str) -> str:
        """Clean string by removing quotes and extra whitespace."""
        return val.strip().strip('"').strip("'").strip()


class TeslaDoorStateBinarySensor(CoordinatorEntity[TelemetryCoordinator], BinarySensorEntity):
    """Door/frunk/trunk state derived from DoorState telemetry field."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.DOOR
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        vin: str,
        name: str,
        unique_suffix: str,
        token_keys: tuple[str, ...],
    ) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._attr_name = name
        self._attr_unique_id = telemetry_unique_id(vin, unique_suffix)
        self._token_keys = set(token_keys)

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.coordinator, self._vin)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.get_field("DoorState") is not None
        )

    @property
    def is_on(self) -> bool | None:
        tokens = door_state_tokens(self.coordinator.get_field("DoorState"))
        if not tokens and self.coordinator.get_field("DoorState") is None:
            return None
        return any(token in tokens for token in self._token_keys)


class TeslaAnyDoorOpenBinarySensor(CoordinatorEntity[TelemetryCoordinator], BinarySensorEntity):
    """Binary sensor: whether any door is open."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.DOOR
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_name = "Door Open"

    def __init__(self, coordinator: TelemetryCoordinator, vin: str) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._attr_unique_id = telemetry_unique_id(self._vin, "any_door_open")

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.coordinator, self._vin)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.get_field("DoorState") is not None
        )

    @property
    def is_on(self) -> bool | None:
        tokens = door_state_tokens(self.coordinator.get_field("DoorState"))
        if not tokens and self.coordinator.get_field("DoorState") is None:
            return None
        return bool(tokens)


class TelemetryConfigSyncedBinarySensor(BinarySensorEntity):
    """Diagnostic binary sensor: whether the last fleet_telemetry_config push was synced."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Telemetry Config Synced"
    _attr_icon = "mdi:cloud-check-outline"

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        vin: str,
        policy_state: PolicyState,
        entry_id: str,
    ) -> None:
        self._coordinator = coordinator
        self._vin = vin
        self._policy_state = policy_state
        self._entry_id = entry_id
        self._attr_unique_id = telemetry_unique_id(self._vin, "config_synced")

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self._coordinator, self._vin)

    @property
    def is_on(self) -> bool | None:
        last_result = self._policy_state.last_result
        if not last_result:
            return None
        synced = last_result.get("synced")
        return synced is True

    @property
    def available(self) -> bool:
        return bool(self._policy_state.last_result)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            ASYNC_DISPATCHER_CONNECT(
                self.hass,
                f"{DOMAIN}_config_push_{self._entry_id}",
                self._handle_push,
            )
        )

    @callback
    def _handle_push(self, result: dict) -> None:
        self.async_write_ha_state()
