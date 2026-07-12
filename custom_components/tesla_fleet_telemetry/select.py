"""Select platform for Tesla seat climate and wheel heater levels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import CommandEntity
from .coordinator import TelemetryCoordinator
from .entity_base import telemetry_unique_id
from .oauth2 import TeslaOAuth2Implementation
from .proxy_client import TeslaProxyClient
from .utils import coerce_bool

_LEVEL_TO_OPTION = {
    0: "Off",
    1: "Low",
    2: "Medium",
    3: "High",
}
_OPTION_TO_LEVEL = {label: level for level, label in _LEVEL_TO_OPTION.items()}

_WHEEL_LEVEL_TO_OPTION = {0: "Off", 1: "Low", 3: "High"}
_WHEEL_OPTION_TO_LEVEL = {label: level for level, label in _WHEEL_LEVEL_TO_OPTION.items()}

_HEAT_PREFIX = "Heat "
_COOL_PREFIX = "Cool "
_SEAT_OFF_OPTION = "Off"


@dataclass(frozen=True)
class SeatSelectDef:
    key: str
    name: str
    icon: str
    field: str
    endpoint: str
    level_key: str
    seat_key: str | None = None  # None = no seat position in command body (e.g. wheel)
    seat_value: int | None = None
    enabled_default: bool = True
    cooler_field: str | None = None
    cooler_seat_value: int | None = None
    # Auto mode support
    auto_endpoint: str | None = None
    auto_seat_position: int | None = None  # seat position for remote_auto_seat_climate_request
    auto_field: str | None = None  # telemetry field reflecting auto-mode state
    use_wheel_levels: bool = False  # Off/Low/High instead of Off/Low/Medium/High


SEAT_SELECT_DEFS: tuple[SeatSelectDef, ...] = (
    SeatSelectDef(
        key="seat_heater_front_left",
        name="Seat Climate Front Left",
        icon="mdi:car-seat",
        field="SeatHeaterLeft",
        endpoint="remote_seat_heater_request",
        level_key="level",
        seat_key="seat_position",
        seat_value=0,
        enabled_default=True,
        cooler_field="ClimateSeatCoolingFrontLeft",
        cooler_seat_value=0,
        auto_endpoint="remote_auto_seat_climate_request",
        auto_seat_position=0,
        auto_field="AutoSeatClimateLeft",
    ),
    SeatSelectDef(
        key="seat_heater_front_right",
        name="Seat Climate Front Right",
        icon="mdi:car-seat",
        field="SeatHeaterRight",
        endpoint="remote_seat_heater_request",
        level_key="level",
        seat_key="seat_position",
        seat_value=1,
        enabled_default=True,
        cooler_field="ClimateSeatCoolingFrontRight",
        cooler_seat_value=1,
        auto_endpoint="remote_auto_seat_climate_request",
        auto_seat_position=1,
        auto_field="AutoSeatClimateRight",
    ),
    SeatSelectDef(
        key="seat_heater_rear_left",
        name="Seat Heater Rear Left",
        icon="mdi:car-seat-heater",
        field="SeatHeaterRearLeft",
        endpoint="remote_seat_heater_request",
        level_key="level",
        seat_key="seat_position",
        seat_value=2,
        enabled_default=False,
    ),
    SeatSelectDef(
        key="seat_heater_rear_center",
        name="Seat Heater Rear Center",
        icon="mdi:car-seat-heater",
        field="SeatHeaterRearCenter",
        endpoint="remote_seat_heater_request",
        level_key="level",
        seat_key="seat_position",
        seat_value=4,
        enabled_default=False,
    ),
    SeatSelectDef(
        key="seat_heater_rear_right",
        name="Seat Heater Rear Right",
        icon="mdi:car-seat-heater",
        field="SeatHeaterRearRight",
        endpoint="remote_seat_heater_request",
        level_key="level",
        seat_key="seat_position",
        seat_value=5,
        enabled_default=False,
    ),
    SeatSelectDef(
        key="wheel_heater",
        name="Steering Wheel Heater",
        icon="mdi:steering",
        field="HvacSteeringWheelHeatLevel",
        endpoint="remote_steering_wheel_heat_level_request",
        level_key="level",
        enabled_default=True,
        auto_endpoint="remote_steering_wheel_heater_request",
        auto_field="HvacSteeringWheelHeatAuto",
        use_wheel_levels=True,
    ),
)


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
        TeslaSeatLevelSelect(coordinator, vin, proxy_client, impl, entry, select_def)
        for select_def in SEAT_SELECT_DEFS
    )


class TeslaSeatLevelSelect(CommandEntity, SelectEntity):
    """Seat heater/cooler/wheel-heater level control as a select entity."""

    def __init__(
        self,
        coordinator,
        vin,
        proxy_client,
        impl,
        entry,
        select_def: SeatSelectDef,
    ) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._select_def = select_def
        state_fields = [select_def.field]
        if select_def.cooler_field:
            state_fields.append(select_def.cooler_field)
        if select_def.auto_field:
            state_fields.append(select_def.auto_field)
        self._state_fields = tuple(state_fields)
        self._attr_unique_id = telemetry_unique_id(self._vin, select_def.key)
        self._attr_name = select_def.name
        self._attr_icon = select_def.icon
        self._attr_entity_registry_enabled_default = select_def.enabled_default
        if select_def.cooler_field:
            base_options = (
                [_SEAT_OFF_OPTION]
                + [f"{_HEAT_PREFIX}{option}" for option in _OPTION_TO_LEVEL if option != "Off"]
                + [f"{_COOL_PREFIX}{option}" for option in _OPTION_TO_LEVEL if option != "Off"]
            )
        else:
            base_options = (
                list(_WHEEL_LEVEL_TO_OPTION.values())
                if select_def.use_wheel_levels
                else list(_OPTION_TO_LEVEL)
            )
        self._attr_options = base_options + ["Auto"] if select_def.auto_endpoint else base_options

    @property
    def current_option(self) -> str | None:
        if self._select_def.auto_field:
            if coerce_bool(self.coordinator.get_field(self._select_def.auto_field)):
                return "Auto"
        if self._select_def.cooler_field:
            heat_level = self._level_from_field(self._select_def.field, _LEVEL_TO_OPTION)
            cool_level = self._level_from_field(self._select_def.cooler_field, _LEVEL_TO_OPTION)
            if cool_level and cool_level > 0:
                return f"{_COOL_PREFIX}{_LEVEL_TO_OPTION[cool_level]}"
            if heat_level and heat_level > 0:
                return f"{_HEAT_PREFIX}{_LEVEL_TO_OPTION[heat_level]}"
            if heat_level == 0 or cool_level == 0:
                return _SEAT_OFF_OPTION
            return None
        level_map = (
            _WHEEL_LEVEL_TO_OPTION if self._select_def.use_wheel_levels else _LEVEL_TO_OPTION
        )
        level = self._level_from_field(self._select_def.field, level_map)
        return level_map.get(level) if level is not None else None

    def _level_from_field(self, field: str, level_map: dict[int, str]) -> int | None:
        value = self.coordinator.get_field(field)
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int | float):
            level = int(value)
            return level if level in level_map else None
        try:
            level = int(float(str(value).strip()))
            return level if level in level_map else None
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        if option == "Auto" and self._select_def.auto_endpoint:
            if self._select_def.auto_endpoint == "remote_steering_wheel_heater_request":
                body_auto: dict[str, Any] = {"on": True}
            else:
                body_auto = {
                    "auto_seat_position": self._select_def.auto_seat_position,
                    "auto_climate_on": True,
                }
            await self._send_command(self._select_def.auto_endpoint, body_auto)
            if self._select_def.auto_field:
                self.coordinator.async_update_field(self._select_def.auto_field, True)
            return

        if self._select_def.cooler_field:
            await self._async_select_seat_climate_option(option)
            return

        option_to_level = (
            _WHEEL_OPTION_TO_LEVEL if self._select_def.use_wheel_levels else _OPTION_TO_LEVEL
        )
        level = option_to_level[option]
        body: dict[str, Any] = {self._select_def.level_key: level}
        if self._select_def.seat_key is not None:
            body[self._select_def.seat_key] = self._select_def.seat_value
        await self._send_command(self._select_def.endpoint, body)
        self.coordinator.async_update_field(self._select_def.field, level)
        if self._select_def.auto_field:
            self.coordinator.async_update_field(self._select_def.auto_field, False)

    async def _async_select_seat_climate_option(self, option: str) -> None:
        heat_level = 0
        cool_level = 0
        if option.startswith(_HEAT_PREFIX):
            heat_level = _OPTION_TO_LEVEL[option.removeprefix(_HEAT_PREFIX)]
        elif option.startswith(_COOL_PREFIX):
            cool_level = _OPTION_TO_LEVEL[option.removeprefix(_COOL_PREFIX)]
        elif option != _SEAT_OFF_OPTION:
            raise ValueError(f"Unknown seat climate option: {option}")

        heat_body: dict[str, Any] = {self._select_def.level_key: heat_level}
        if self._select_def.seat_key is not None:
            heat_body[self._select_def.seat_key] = self._select_def.seat_value
        await self._send_command(self._select_def.endpoint, heat_body)
        self.coordinator.async_update_field(self._select_def.field, heat_level)

        if self._select_def.cooler_field is not None:
            cool_body = {
                "seat_cooler_level": cool_level,
                "seat_position": self._select_def.cooler_seat_value,
            }
            await self._send_command("remote_seat_cooler_request", cool_body)
            self.coordinator.async_update_field(self._select_def.cooler_field, cool_level)

        if self._select_def.auto_field:
            self.coordinator.async_update_field(self._select_def.auto_field, False)
