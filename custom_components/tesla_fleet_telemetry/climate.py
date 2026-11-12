"""Climate platform — HVAC and seat heaters."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import PRESET_NONE
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import CommandEntity
from .coordinator import TelemetryCoordinator
from .device import build_device_info
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
            TeslaClimate(coordinator, vin, proxy_client, impl, entry),
            CabinOverheatClimate(coordinator, vin, proxy_client, impl, entry),
        ]
    )


_KEEPER_PRESET_TO_MODE = {
    PRESET_NONE: 0,
    "keep": 1,
    "dog": 2,
    "camp": 3,
}

_PRESET_DEFROST = "defrost"

# Telemetry enum suffixes → preset names (after stripping "ClimateKeeperMode" prefix)
_KEEPER_ENUM_TO_PRESET = {
    "off": PRESET_NONE,
    "on": "keep",
    "keep": "keep",
    "keepmode": "keep",
    "dog": "dog",
    "dogmode": "dog",
    "camp": "camp",
    "party": "camp",
    "partymode": "camp",
    "campmode": "camp",
}

_COP_TEMP_LIMITS = {
    "low": 30.0,
    "medium": 35.0,
    "high": 40.0,
}

_COP_TEMP_COMMANDS = {
    30.0: (0, "ClimateOverheatProtectionTempLimitLow"),
    35.0: (1, "ClimateOverheatProtectionTempLimitMedium"),
    40.0: (2, "ClimateOverheatProtectionTempLimitHigh"),
}


class TeslaClimate(CommandEntity, ClimateEntity):
    """HVAC control entity for Tesla vehicle climate system."""

    _attr_name = "Climate"
    _attr_translation_key = "climate"
    _state_fields = (
        "PreconditioningEnabled",
        "InsideTemp",
        "HvacLeftTemperatureRequest",
        "HvacRightTemperatureRequest",
        "ClimateKeeperMode",
        "DefrostForPreconditioning",
        "HvacPower",
        "HvacACEnabled",
        "HvacFanSpeed",
        "HvacFanStatus",
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 15.0
    _attr_max_temp = 28.0
    _attr_target_temperature_step = 0.5
    _attr_preset_modes = [PRESET_NONE, "keep", "dog", "camp", _PRESET_DEFROST]
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "climate")

    def _set_climate_running(self, running: bool) -> None:
        """Apply optimistic coordinator state for climate on/off."""
        self.coordinator.async_update_field("PreconditioningEnabled", running)
        self.coordinator.async_update_field("HvacPower", running)

    @property
    def icon(self) -> str:
        preset = self.preset_mode
        if preset == _PRESET_DEFROST:
            return "mdi:car-defrost-front"
        if preset == "dog":
            return "mdi:dog"
        if preset == "camp":
            return "mdi:campfire"
        if self.hvac_mode == HVACMode.OFF:
            return "mdi:fan-off"
        if coerce_bool(self.coordinator.get_field("HvacACEnabled")):
            return "mdi:snowflake"
        return "mdi:fan"

    @property
    def hvac_mode(self) -> HVACMode:
        hvac_power = self.coordinator.get_field("HvacPower")
        if hvac_power is not None:
            if isinstance(hvac_power, bool):
                # Optimistic update path (set by _set_climate_running)
                return HVACMode.HEAT_COOL if hvac_power else HVACMode.OFF
            power_state = normalize_enum(hvac_power, prefixes=("HvacPowerState",))
            if power_state is not None:
                normalized = power_state.lower().replace("_", "").replace("-", "")
                if normalized in {"on", "precondition", "overheatprotect"}:
                    return HVACMode.HEAT_COOL
                if normalized in {"off", "unknown"}:
                    return HVACMode.OFF
        # Fallback: HvacPower not yet received — use PreconditioningEnabled
        if coerce_bool(self.coordinator.get_field("PreconditioningEnabled")):
            return HVACMode.HEAT_COOL
        # Keeper modes and defrost imply climate is running
        if self.preset_mode is not None:
            return HVACMode.HEAT_COOL
        return HVACMode.OFF

    @property
    def native_current_temperature(self) -> float | None:
        value = self.coordinator.get_field("InsideTemp")
        if isinstance(value, int | float):
            return round(float(value), 1)
        return None

    @property
    def native_target_temperature(self) -> float | None:
        """Return driver temp as main target temperature."""
        value = self.coordinator.get_field("HvacLeftTemperatureRequest")
        if isinstance(value, int | float):
            return round(float(value), 1)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        field_map = {
            "fan_speed": "HvacFanSpeed",
            "passenger_target_temperature": "HvacRightTemperatureRequest",
            "ac_enabled": "HvacACEnabled",
            "auto_mode": "HvacAutoMode",
            "fan_status": "HvacFanStatus",
            "steering_wheel_heat_auto": "HvacSteeringWheelHeatAuto",
            "steering_wheel_heat_level": "HvacSteeringWheelHeatLevel",
            "rear_display_hvac_enabled": "RearDisplayHvacEnabled",
            "rear_seat_heaters": "RearSeatHeaters",
            "not_enough_power_to_heat": "NotEnoughPowerToHeat",
        }
        for attr_name, field in field_map.items():
            value = self.coordinator.get_field(field)
            if value is not None:
                attrs[attr_name] = value
        power_raw = self.coordinator.get_field("HvacPower")
        if power_raw is not None:
            decoded = normalize_enum(power_raw, prefixes=("HvacPowerState",))
            attrs["hvac_power"] = decoded if decoded is not None else power_raw
        keeper_raw = self.coordinator.get_field("ClimateKeeperMode")
        if keeper_raw is not None:
            decoded = normalize_enum(
                keeper_raw, prefixes=("ClimateKeeperModeState", "ClimateKeeperMode")
            )
            attrs["climate_keeper_mode"] = decoded if decoded is not None else keeper_raw
        return attrs

    @property
    def preset_mode(self) -> str | None:
        if coerce_bool(self.coordinator.get_field("DefrostForPreconditioning")):
            return _PRESET_DEFROST
        raw = normalize_enum(
            self.coordinator.get_field("ClimateKeeperMode"),
            prefixes=("ClimateKeeperModeState", "ClimateKeeperMode"),
        )
        if raw is None:
            return None
        key = raw.lower().replace("_", "").replace("-", "").replace(" ", "")
        preset = _KEEPER_ENUM_TO_PRESET.get(key)
        return None if preset == PRESET_NONE else preset

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT_COOL)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._send_command("auto_conditioning_stop")
            self._set_climate_running(False)
            self.coordinator.async_update_field("DefrostForPreconditioning", False)
            self.coordinator.async_update_field("ClimateKeeperMode", "ClimateKeeperModeOff")
        else:
            await self._send_command("auto_conditioning_start")
            self._set_climate_running(True)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        driver_temp = kwargs.get("temperature")
        if driver_temp is None:
            driver_temp = kwargs.get("target_temp_low")
        passenger_temp = kwargs.get("target_temp_high")
        if passenger_temp is None:
            # set_temps needs both sides; keep the passenger's own setpoint rather
            # than overwriting it with the driver's (the car applies its own
            # dual-zone sync setting either way).
            current = self.coordinator.get_field("HvacRightTemperatureRequest")
            if isinstance(current, int | float) and not isinstance(current, bool):
                passenger_temp = current
        if driver_temp is not None:
            d_temp = float(driver_temp)
            p_temp = float(passenger_temp) if passenger_temp is not None else d_temp
            await self._send_command(
                "set_temps",
                {"driver_temp": d_temp, "passenger_temp": p_temp},
            )
            self.coordinator.async_update_field("HvacLeftTemperatureRequest", d_temp)
            self.coordinator.async_update_field("HvacRightTemperatureRequest", p_temp)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        was_defrost = coerce_bool(self.coordinator.get_field("DefrostForPreconditioning"))
        if preset_mode == _PRESET_DEFROST:
            await self._send_command("auto_conditioning_start")
            await self._send_command("set_preconditioning_max", {"on": True})
            self._set_climate_running(True)
            self.coordinator.async_update_field("DefrostForPreconditioning", True)
            return
        mode_int = _KEEPER_PRESET_TO_MODE.get(preset_mode)
        if mode_int is None:
            raise ServiceValidationError(f"Unknown preset mode: {preset_mode}")
        if was_defrost:
            await self._send_command("set_preconditioning_max", {"on": False})
        if preset_mode != PRESET_NONE:
            await self._send_command("auto_conditioning_start")
        await self._send_command("set_climate_keeper_mode", {"climate_keeper_mode": mode_int})
        self.coordinator.async_update_field("DefrostForPreconditioning", False)
        self.coordinator.async_update_field(
            "ClimateKeeperMode",
            {
                0: "ClimateKeeperModeStateOff",
                1: "ClimateKeeperModeStateOn",
                2: "ClimateKeeperModeStateDog",
                3: "ClimateKeeperModeStateParty",
            }[mode_int],
        )
        if preset_mode != PRESET_NONE:
            self._set_climate_running(True)


class CabinOverheatClimate(CommandEntity, ClimateEntity):
    """Cabin overheat protection climate control."""

    _attr_name = "COP"
    _state_fields = (
        "CabinOverheatProtectionMode",
        "CabinOverheatProtectionTemperatureLimit",
    )
    _attr_has_entity_name = True
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.FAN_ONLY]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 30.0
    _attr_max_temp = 40.0
    _attr_target_temperature_step = 5.0
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, vin, proxy_client, impl, entry) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "cabin_overheat_climate")

    def _set_cop_mode(self, mode: str) -> None:
        """Apply optimistic coordinator state for COP mode."""
        self.coordinator.async_update_field("CabinOverheatProtectionMode", mode)

    @property
    def icon(self) -> str:
        mode = self.hvac_mode
        if mode == HVACMode.COOL:
            return "mdi:thermometer-low"
        if mode == HVACMode.FAN_ONLY:
            return "mdi:fan"
        return "mdi:thermometer-alert"

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.coordinator, self._vin)

    @property
    def hvac_mode(self) -> HVACMode | None:
        mode = normalize_enum(
            self.coordinator.get_field("CabinOverheatProtectionMode"),
            prefixes=("CabinOverheatProtectionModeState", "CabinOverheatProtection"),
        )
        if mode is not None:
            normalized = mode.lower().replace("_", "").replace("-", "").replace("/", "")
            if normalized in {"off", "false", "disabled", "inactive"}:
                return HVACMode.OFF
            if normalized in {"noac", "fanonly", "fan"}:
                return HVACMode.FAN_ONLY
            if normalized in {"on", "ac", "cool"}:
                return HVACMode.COOL

        return None

    @property
    def native_current_temperature(self) -> float | None:
        """COP is mode-only; cabin temperature is exposed by main Climate."""
        return None

    @property
    def native_target_temperature(self) -> float | None:
        temp_limit = normalize_enum(
            self.coordinator.get_field("CabinOverheatProtectionTemperatureLimit"),
            prefixes=("ClimateOverheatProtectionTempLimit",),
        )
        if temp_limit is None:
            return None
        return _COP_TEMP_LIMITS.get(temp_limit.lower())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        mode = normalize_enum(
            self.coordinator.get_field("CabinOverheatProtectionMode"),
            prefixes=("CabinOverheatProtectionModeState", "CabinOverheatProtection"),
        )
        if mode is not None:
            attrs["protection_mode"] = mode
        temp_limit = normalize_enum(
            self.coordinator.get_field("CabinOverheatProtectionTemperatureLimit"),
            prefixes=("ClimateOverheatProtectionTempLimit",),
        )
        if temp_limit is not None:
            attrs["temperature_limit"] = (
                self.native_target_temperature
                if self.native_target_temperature is not None
                else temp_limit
            )
        return attrs

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get("temperature")
        if temperature is None:
            return

        target = round(float(temperature) / 5.0) * 5.0
        command = _COP_TEMP_COMMANDS.get(target)
        if command is None:
            raise ServiceValidationError(f"Unsupported COP temperature: {temperature}")

        level, optimistic_state = command
        await self._send_command("set_cop_temp", {"cop_temp": level})
        self.coordinator.async_update_field(
            "CabinOverheatProtectionTemperatureLimit", optimistic_state
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._send_command("set_cabin_overheat_protection", {"on": False})
            self._set_cop_mode("CabinOverheatProtectionOff")
            return
        if hvac_mode == HVACMode.FAN_ONLY:
            await self._send_command(
                "set_cabin_overheat_protection",
                {"on": True, "fan_only": True},
            )
            self._set_cop_mode("CabinOverheatProtectionNoAC")
            return
        if hvac_mode == HVACMode.COOL:
            await self._send_command(
                "set_cabin_overheat_protection",
                {"on": True, "fan_only": False},
            )
            self._set_cop_mode("CabinOverheatProtectionOn")
