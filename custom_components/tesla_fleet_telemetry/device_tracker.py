"""Device tracker platform — GPS location from Tesla Fleet Telemetry."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TelemetryCoordinator
from .device import build_device_info
from .entity_base import telemetry_unique_id

_TRACKERS = (
    ("Location", "Location", "mdi:map-marker", "location"),
    ("DestinationLocation", "Destination Location", "mdi:map-marker-path", "destination_location"),
    ("OriginLocation", "Origin Location", "mdi:map-marker-start", "origin_location"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = entry.runtime_data
    coordinator: TelemetryCoordinator = entry_data.coordinator
    vin: str = entry_data.vin
    async_add_entities(TeslaLocationTracker(coordinator, vin, *args) for args in _TRACKERS)


class TeslaLocationTracker(CoordinatorEntity[TelemetryCoordinator], TrackerEntity, RestoreEntity):
    """GPS device tracker fed by one telemetry location field."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        vin: str,
        field: str,
        name: str,
        icon: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._field = field
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = telemetry_unique_id(vin, unique_suffix)
        self._restored_lat: float | None = None
        self._restored_lon: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._restored_lat = last_state.attributes.get("latitude")
            self._restored_lon = last_state.attributes.get("longitude")

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.coordinator, self._vin)

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        loc = self.coordinator.get_field(self._field)
        if isinstance(loc, dict):
            return loc.get("latitude")
        return self._restored_lat

    @property
    def longitude(self) -> float | None:
        loc = self.coordinator.get_field(self._field)
        if isinstance(loc, dict):
            return loc.get("longitude")
        return self._restored_lon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._field != "Location":
            return {}
        heading = self.coordinator.get_field("GpsHeading")
        speed = self.coordinator.get_field("VehicleSpeed")
        attrs = {}
        if heading is not None:
            attrs["heading"] = heading
        if speed is not None:
            attrs["speed"] = speed
        return attrs

    @property
    def available(self) -> bool:
        loc = self.coordinator.get_field(self._field)
        if isinstance(loc, dict):
            return True
        return self._restored_lat is not None and self._restored_lon is not None
