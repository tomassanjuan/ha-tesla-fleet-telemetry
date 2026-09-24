"""Base entity class for Tesla Fleet Telemetry entities."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TelemetryCoordinator
from .device import build_device_info


def telemetry_unique_id(vin: str, name: str) -> str:
    """Build integration unique_id scoped to a specific vehicle's VIN.

    Must include the VIN: HA's entity registry keys entities by
    (domain, platform, unique_id) across the whole instance, not per
    config entry, so a name-only id collides across multiple vehicles.
    """
    return f"telemetry_{vin}_{name}"


_UNRESTORED_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE}


class TelemetryEntity(CoordinatorEntity[TelemetryCoordinator], RestoreEntity):
    """Base class for all Tesla Fleet Telemetry entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        description: EntityDescription,
        vin: str,
        *,
        extra_fields: frozenset[str] = frozenset(),
    ) -> None:
        """Bind the entity to its telemetry field (description.key).

        The entity is only refreshed when that field or one of extra_fields
        changes.
        """
        self._field = description.key
        super().__init__(coordinator, context=frozenset({self._field}) | extra_fields)
        self._vin = vin
        self._attr_unique_id = telemetry_unique_id(vin, description.key)
        self._restored_state: str | None = None
        # Name, icon, and entity_registry_enabled_default are read from
        # entity_description by HA; subclasses set self.entity_description.

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in _UNRESTORED_STATES:
            self._restored_state = last_state.state

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.coordinator, self._vin)

    @property
    def raw_value(self) -> Any:
        """Return the raw value from the coordinator for this field."""
        return self.coordinator.get_field(self._field)

    @property
    def restored_state(self) -> str | None:
        """Return restored HA state while MQTT has not delivered this field yet.

        Once the field has been received — even as null/invalid — the live
        value wins, so an invalid reading never shows a stale pre-restart state.
        """
        if self._field in self.coordinator.data:
            return None
        return self._restored_state

    @property
    def available(self) -> bool:
        """Available when a live or (not yet superseded) restored value exists."""
        return self.coordinator.last_update_success and (
            self.raw_value is not None or self.restored_state is not None
        )
