"""Sensor platform for Tesla Fleet Telemetry."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    DERIVED_SENSOR_DESCRIPTIONS,
    DESCRIPTIONS,
    DOMAIN,
    DerivedSensorEntityDescription,
    TelemetryEntityDescription,
)
from .coordinator import TelemetryCoordinator
from .device import build_device_info
from .entity_base import TelemetryEntity, telemetry_unique_id
from .fleet_config import PolicyState

ASYNC_DISPATCHER_CONNECT = cast(
    Callable[[HomeAssistant, str, Callable[[dict[str, Any]], None]], Callable[[], None]],
    async_dispatcher_connect,
)


def _float_field(coordinator: TelemetryCoordinator, field: str) -> float | None:
    """Return a telemetry field as a float when possible."""
    value = coordinator.get_field(field)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _battery_power_w(coordinator: TelemetryCoordinator) -> float | None:
    """Return battery power in W; positive means power leaving the pack.

    PackCurrent is positive while charging (current into the pack), so
    negating V*I yields positive power while discharging and negative
    power while charging (AC or DC — the pack doesn't know which).
    """
    voltage = _float_field(coordinator, "PackVoltage")
    current = _float_field(coordinator, "PackCurrent")
    if voltage is None or current is None:
        return None
    power = -(voltage * current)
    # Guard against infinity/NaN and unrealistic power values (>1MW)
    if not math.isfinite(power) or abs(power) > 1e6:
        return None
    return power


def _epoch_to_datetime(seconds: float) -> datetime | None:
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    if seconds > 1e11:  # epoch milliseconds
        seconds /= 1000
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _to_utc_datetime(value: object) -> datetime | None:
    """Coerce a telemetry timestamp (ISO string or epoch s/ms) to an aware datetime."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return _epoch_to_datetime(float(value))
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip().strip('"').strip("'")
        try:
            return _epoch_to_datetime(float(text))
        except ValueError:
            parsed = dt_util.parse_datetime(text)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _pack_imbalance(coordinator: TelemetryCoordinator) -> float | None:
    max_voltage = _float_field(coordinator, "BrickVoltageMax")
    min_voltage = _float_field(coordinator, "BrickVoltageMin")
    if max_voltage is None or min_voltage is None:
        return None
    imbalance = max_voltage - min_voltage
    # Guard against infinity/NaN
    if not math.isfinite(imbalance) or imbalance < 0:
        return None
    return imbalance


def _charger_power_delta(coordinator: TelemetryCoordinator) -> float | None:
    """Charger power (AC or DC, whichever is active) minus power reaching the pack, in W."""
    battery_power_w = _battery_power_w(coordinator)
    if battery_power_w is None:
        return None

    # ACChargingPower/DCChargingPower are reported in kW; convert to W to match battery_power_w.
    ac_power_w = (_float_field(coordinator, "ACChargingPower") or 0.0) * 1000
    dc_power_w = (_float_field(coordinator, "DCChargingPower") or 0.0) * 1000
    charger_power_w = ac_power_w if ac_power_w > 0 else dc_power_w
    if charger_power_w <= 0:
        return 0.0
    delta = charger_power_w + battery_power_w
    # Guard against infinity/NaN and unrealistic power values
    if not math.isfinite(delta) or abs(delta) > 1e6:
        return None
    return delta


def _charger_loss(coordinator: TelemetryCoordinator) -> float | None:
    ac_energy = _float_field(coordinator, "ACChargingEnergyIn")
    dc_energy = _float_field(coordinator, "DCChargingEnergyIn")
    if ac_energy is None or dc_energy is None:
        return None
    loss = ac_energy - dc_energy
    # Guard against infinity/NaN and ensure loss is non-negative
    if not math.isfinite(loss) or loss < 0:
        return None
    return loss


def _battery_module_temp_range(coordinator: TelemetryCoordinator) -> float | None:
    max_temp = _float_field(coordinator, "ModuleTempMax")
    min_temp = _float_field(coordinator, "ModuleTempMin")
    if max_temp is None or min_temp is None:
        return None
    temp_range = max_temp - min_temp
    # Guard against infinity/NaN and ensure range is non-negative
    if not math.isfinite(temp_range) or temp_range < 0:
        return None
    return temp_range


def _charger_efficiency(coordinator: TelemetryCoordinator) -> float | None:
    dc_energy = _float_field(coordinator, "DCChargingEnergyIn")
    ac_energy = _float_field(coordinator, "ACChargingEnergyIn")
    if ac_energy is None or ac_energy <= 0:
        return None
    if dc_energy is None:
        return None
    efficiency = (dc_energy / ac_energy) * 100
    # Guard against infinity/NaN and ensure efficiency is in valid range (0-100%)
    if not math.isfinite(efficiency) or efficiency < 0 or efficiency > 100:
        return None
    return efficiency


def _bms_full_rated_range(coordinator: TelemetryCoordinator) -> float | None:
    rated_range = _float_field(coordinator, "RatedRange")
    soc = _float_field(coordinator, "Soc")
    if rated_range is None or soc is None or soc <= 0:
        return None
    full_range = rated_range / (soc / 100.0)
    # Guard against infinity/NaN and unrealistic range values (>1000 miles)
    if not math.isfinite(full_range) or full_range < 0 or full_range > 1000:
        return None
    return full_range


DERIVED_SENSOR_VALUE_FNS: dict[str, Callable[[TelemetryCoordinator], float | int | None]] = {
    "pack_imbalance": _pack_imbalance,
    "charger_power_delta": _charger_power_delta,
    "charger_loss": _charger_loss,
    "battery_power": _battery_power_w,
    "battery_module_temp_range": _battery_module_temp_range,
    "charger_efficiency": _charger_efficiency,
    "bms_full_rated_range": _bms_full_rated_range,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = entry.runtime_data
    coordinator: TelemetryCoordinator = entry_data.coordinator
    vin: str = entry_data.vin
    policy_state: PolicyState = entry_data.policy_state

    entities: list[SensorEntity] = [
        TelemetrySensor(coordinator, description, vin)
        for description in DESCRIPTIONS
        if not description.binary and not description.has_dedicated_entity
    ]
    entities.extend(
        DerivedTelemetrySensor(coordinator, description, vin)
        for description in DERIVED_SENSOR_DESCRIPTIONS
    )
    entities.append(TelemetryLastPushSensor(coordinator, vin, policy_state, entry.entry_id))
    async_add_entities(entities)


class TelemetrySensor(TelemetryEntity, SensorEntity):
    """A sensor entity for a single Tesla telemetry field."""

    entity_description: TelemetryEntityDescription

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        description: TelemetryEntityDescription,
        vin: str,
    ) -> None:
        super().__init__(coordinator, description, vin)
        self.entity_description = description

    @property
    def _is_timestamp(self) -> bool:
        return self.entity_description.device_class == SensorDeviceClass.TIMESTAMP

    @property
    def _is_numeric(self) -> bool:
        """Mirror HA's numeric-sensor rule: these must never report a string."""
        desc = self.entity_description
        return (
            desc.native_unit_of_measurement is not None
            or desc.state_class is not None
            or desc.suggested_display_precision is not None
        )

    @property
    def native_value(self):
        val = self.raw_value
        if val is None:
            return self._restored_native_value()
        # HA raises ValueError on write for a timestamp sensor that is not an
        # aware datetime, or a numeric sensor with a non-numeric value.
        if self._is_timestamp:
            return _to_utc_datetime(val)

        if isinstance(val, str):
            return self._process_string_value(val)
        if isinstance(val, bool):
            return None if self._is_numeric else val
        if isinstance(val, int | float):
            if not math.isfinite(val):
                return None
            return self._apply_numeric_transform(float(val))
        if self._is_numeric:
            return None
        if isinstance(val, dict | list):
            return json.dumps(val, sort_keys=True, separators=(",", ":"))

        return val

    def _restored_native_value(self) -> str | float | int | datetime | None:
        restored = self.restored_state
        if restored is None:
            return None
        if self._is_timestamp:
            return _to_utc_datetime(restored)
        if self._is_numeric:
            return self._convert_to_numeric(restored)
        return restored

    def _process_string_value(self, val: str) -> str | float | None:
        """Process string values, converting to numeric for numeric sensors."""
        val = val.strip().strip('"').strip("'")
        desc = self.entity_description
        if desc.strip_prefix and val.startswith(desc.strip_prefix):
            val = val[len(desc.strip_prefix) :]
        if self._is_numeric:
            return self._convert_to_numeric(val)
        return val

    def _apply_numeric_transform(self, num: float) -> float | int | None:
        """Apply range check, multiplier, rounding, and int-coercion to a float."""
        if not self._is_in_valid_range(num):
            return None
        desc = self.entity_description
        if desc.value_multiplier is not None:
            num *= desc.value_multiplier
        precision = desc.suggested_display_precision
        result = round(num, precision) if precision is not None else num
        if isinstance(result, float) and result == int(result):
            return int(result)
        return result

    def _convert_to_numeric(self, val: str) -> float | int | None:
        try:
            num = float(val)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(num):
            return None
        return self._apply_numeric_transform(num)

    def _is_in_valid_range(self, num: float) -> bool:
        """Check if numeric value is within valid range."""
        desc = self.entity_description
        if desc.max_valid is not None and num > desc.max_valid:
            return False
        if desc.min_valid is not None and num < desc.min_valid:
            return False
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional attributes for the connectivity status field."""
        if self._field != "ConnectivityStatus":
            return None

        attributes = {}
        network_interface = self.coordinator.get_field("NetworkInterface")
        if network_interface is not None:
            attributes["NetworkInterface"] = network_interface

        return attributes if attributes else None


class DerivedTelemetrySensor(RestoreEntity, SensorEntity):
    """A sensor calculated from one or more telemetry fields."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: DerivedSensorEntityDescription

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        description: DerivedSensorEntityDescription,
        vin: str,
    ) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._vin = vin
        self._attr_unique_id = telemetry_unique_id(vin, description.key)
        self._restored_native_value: float | int | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.coordinator, self._vin)

    @property
    def native_value(self) -> float | int | None:
        value_fn = DERIVED_SENSOR_VALUE_FNS.get(self.entity_description.key)
        if value_fn is None:
            return None
        value = value_fn(self.coordinator)
        if value is None:
            return self._restored_native_value
        if self.entity_description.value_multiplier is not None:
            value *= self.entity_description.value_multiplier
        precision = self.entity_description.suggested_display_precision
        return round(value, precision) if precision is not None else value

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.native_value is not None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                value = float(last_state.state)
            except (TypeError, ValueError):
                value = None
            if value is not None:
                self._restored_native_value = int(value) if value == int(value) else value
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class TelemetryLastPushSensor(SensorEntity):
    """Diagnostic sensor: timestamp of the last fleet_telemetry_config push."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "Telemetry Last Push"
    _attr_icon = "mdi:cloud-upload-outline"

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
        self._attr_unique_id = telemetry_unique_id(vin, "config_last_push")

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self._coordinator, self._vin)

    @property
    def native_value(self):
        return self._policy_state.last_push_dt

    @property
    def available(self) -> bool:
        return True

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
