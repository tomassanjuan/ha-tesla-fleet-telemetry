"""Telemetry field definitions: entity descriptions and field/entity mappings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.helpers.entity import EntityCategory, EntityDescription

from .polling_profiles import INSTANT_FIELDS as CONTROL_REALTIME_FIELDS

# ---------------------------------------------------------------------------
# Entity description dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TelemetryEntityDescription(SensorEntityDescription):
    """Entity description for Tesla telemetry fields (sensors and binary sensors).

    Inherits SensorEntityDescription so HA's sensor machinery
    (native_unit_of_measurement, state_class, suggested_display_precision, etc.)
    works without extra wiring.  Binary sensor entries set binary=True; all
    sensor-specific fields (strip_prefix, value_multiplier, …) are unused for
    those entries.
    """

    # True → binary_sensor platform; False → sensor platform
    binary: bool = False
    # Override HA's default of True — disabled unless explicitly opted in
    entity_registry_enabled_default: bool = False
    # True when a dedicated entity (cover, select, number, etc.) already exposes this field.
    # Generic sensor/binary_sensor platform skips fields with this flag set.
    has_dedicated_entity: bool = False
    # device_class covers both SensorDeviceClass and BinarySensorDeviceClass.
    # Use Any here because Home Assistant's sensor and binary_sensor description
    # base classes expose incompatible device_class types to mypy.
    device_class: Any = None
    # Sensor-specific extras (unused for binary sensor entries)
    # Strip this prefix from enum value strings (e.g. "ChargeStateCharging" → "Charging")
    strip_prefix: str | None = None
    # Scale the raw numeric value before display (e.g. 0.1 to divide by 10)
    value_multiplier: float | None = None
    # Suppress values above this threshold (e.g. isolation resistance > 7500 kΩ is out-of-range)
    max_valid: float | None = None
    # Suppress values below this threshold (e.g. pack voltage < 280 V means car is off/invalid)
    min_valid: float | None = None
    # Explicit minimum_delta override. "auto" derives from display precision.
    minimum_delta_override: float | None | Literal["auto"] = "auto"


# ---------------------------------------------------------------------------
# Entity descriptions (sensors + binary sensors)
# ---------------------------------------------------------------------------

DESCRIPTIONS: tuple[TelemetryEntityDescription, ...] = (
    # --- Charging ---
    TelemetryEntityDescription(
        key="ACChargingEnergyIn",
        name="AC Charging Energy In",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="ACChargingPower",
        name="AC Charging Power",
        native_unit_of_measurement="kW",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="BatteryLevel",
        name="Battery Level",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="BMSState",
        name="BMS State",
        icon="mdi:battery-check",
        strip_prefix="BMSState",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="BrickVoltageMax",
        name="Brick Voltage Max",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        icon="mdi:flash",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="BrickVoltageMin",
        name="Brick Voltage Min",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        icon="mdi:flash",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="BrickSocMinPercent",
        name="Brick SoC Min",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-low",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="NominalFullPackEnergyKwh",
        name="Nominal Full Pack Energy",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-heart-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        minimum_delta_override=0.1,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="ChargeAmps",
        name="Charge Amps",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:current-ac",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="ChargeCurrentRequest",
        has_dedicated_entity=True,
        name="Charge Current Request",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:current-ac",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="ChargeCurrentRequestMax",
        has_dedicated_entity=True,
        name="Charge Current Request Max",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:current-ac",
    ),
    TelemetryEntityDescription(
        key="ChargeLimitSoc",
        has_dedicated_entity=True,
        name="Charge Limit SOC",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-charging-80",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="ChargePort",
        name="Charge Port",
        icon="mdi:ev-plug-type2",
        strip_prefix="ChargePort",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="ChargePortLatch",
        name="Charge Port Latch",
        icon="mdi:lock",
        strip_prefix="ChargePortLatch",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="ChargeRateMilePerHour",
        name="Charge Rate",
        native_unit_of_measurement="mph",
        suggested_unit_of_measurement="km/h",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
        min_valid=0,
    ),
    TelemetryEntityDescription(
        key="ChargeState",
        name="Charge State",
        icon="mdi:battery-charging",
        strip_prefix="ChargeState",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="ChargerPhases",
        name="Charger Phases",
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="ChargerVoltage",
        name="Charger Voltage",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:flash",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="ChargingCableType",
        name="Charging Cable Type",
        icon="mdi:cable-data",
        strip_prefix="CableType",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DCChargingEnergyIn",
        name="DC Charging Energy In",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="DetailedChargeState",
        name="Charge State Detailed",
        icon="mdi:battery-charging",
        strip_prefix="DetailedChargeState",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="EnergyRemaining",
        name="Energy Remaining",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:battery",
        entity_registry_enabled_default=True,
        minimum_delta_override=0.05,
    ),
    TelemetryEntityDescription(
        key="IsolationResistance",
        name="Isolation Resistance",
        native_unit_of_measurement="kΩ",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        max_valid=8000.0,
        icon="mdi:resistor",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        minimum_delta_override=20,
    ),
    TelemetryEntityDescription(
        key="PackCurrent",
        name="Pack Current",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:current-dc",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_multiplier=-1.0,
        minimum_delta_override=0.5,
    ),
    TelemetryEntityDescription(
        key="PackVoltage",
        name="Pack Voltage",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        min_valid=280.0,
        icon="mdi:flash",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        minimum_delta_override=0.2,
    ),
    TelemetryEntityDescription(
        key="RatedRange",
        name="Rated Range",
        native_unit_of_measurement="mi",
        suggested_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:map-marker-distance",
        entity_registry_enabled_default=True,
        minimum_delta_override=0.1,
    ),
    TelemetryEntityDescription(
        key="ScheduledChargingMode",
        name="Scheduled Charging Mode",
        icon="mdi:clock-outline",
        strip_prefix="ScheduledChargingMode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="Soc",
        name="SoC",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:battery",
        entity_registry_enabled_default=True,
    ),
    # --- Climate ---
    TelemetryEntityDescription(
        key="CabinOverheatProtectionMode",
        has_dedicated_entity=True,
        name="Cabin Overheat Protection Mode",
        icon="mdi:thermometer-alert",
        strip_prefix="CabinOverheatProtectionModeState",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="CabinOverheatProtectionTemperatureLimit",
        has_dedicated_entity=True,
        name="Cabin Overheat Protection Temp Limit",
        icon="mdi:thermometer-alert",
        strip_prefix="ClimateOverheatProtectionTempLimit",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="DefrostMode",
        name="Defrost Mode",
        icon="mdi:car-defrost-front",
        strip_prefix="DefrostModeState",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="HvacLeftTemperatureRequest",
        has_dedicated_entity=True,
        name="HVAC Left Temp Request",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="HvacRightTemperatureRequest",
        has_dedicated_entity=True,
        name="HVAC Right Temp Request",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
    ),
    TelemetryEntityDescription(
        key="InsideTemp",
        name="Inside Temperature",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="OutsideTemp",
        name="Outside Temperature",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="SeatHeaterLeft",
        has_dedicated_entity=True,
        name="Seat Heater Left",
        icon="mdi:car-seat-heater",
    ),
    TelemetryEntityDescription(
        key="SeatHeaterRight",
        has_dedicated_entity=True,
        name="Seat Heater Right",
        icon="mdi:car-seat-heater",
    ),
    TelemetryEntityDescription(
        key="SeatHeaterRearCenter",
        has_dedicated_entity=True,
        name="Seat Heater Rear Center",
        icon="mdi:car-seat-heater",
    ),
    TelemetryEntityDescription(
        key="SeatHeaterRearLeft",
        has_dedicated_entity=True,
        name="Seat Heater Rear Left",
        icon="mdi:car-seat-heater",
    ),
    TelemetryEntityDescription(
        key="SeatHeaterRearRight",
        has_dedicated_entity=True,
        name="Seat Heater Rear Right",
        icon="mdi:car-seat-heater",
    ),
    # --- Driving ---
    TelemetryEntityDescription(
        key="BrakePedalPos",
        name="Brake Pressure",
        native_unit_of_measurement="bar",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:car-brake-hold",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="DiAxleSpeedR",
        name="Rear Axle Speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:rotate-right",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DiMotorCurrentR",
        name="Rear Motor Current",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:current-dc",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DiTorqueActualR",
        name="Rear Motor Torque",
        native_unit_of_measurement="Nm",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_multiplier=0.1,
        icon="mdi:engine",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DiTorquemotor",
        name="Motor Torque Command",
        native_unit_of_measurement="Nm",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:engine",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DiVBatR",
        name="Rear Inverter DC Voltage",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="Gear",
        name="Gear",
        icon="mdi:car-shift-pattern",
        strip_prefix="ShiftState",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="LateralAcceleration",
        name="Lateral Acceleration",
        native_unit_of_measurement="m/s²",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:axis-x-rotate-clockwise",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="LongitudinalAcceleration",
        name="Longitudinal Acceleration",
        native_unit_of_measurement="m/s²",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:axis-z-rotate-clockwise",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="PedalPosition",
        name="Accelerator Position",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:car-cruise-control",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="VehicleSpeed",
        name="Vehicle Speed",
        native_unit_of_measurement="mph",
        suggested_unit_of_measurement="km/h",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:speedometer",
    ),
    TelemetryEntityDescription(
        key="CruiseSetSpeed",
        name="Cruise Set Speed",
        native_unit_of_measurement="km/h",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:car-cruise-control",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Powertrain ---
    TelemetryEntityDescription(
        key="DiHeatsinkTR",
        name="Rear Heatsink Temp",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:thermometer",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DiInverterTR",
        name="Rear Inverter Temp",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:thermometer",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DiStateR",
        name="Rear Drive Inverter State",
        icon="mdi:engine-outline",
        strip_prefix="DriveInverterState",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DiStatorTempR",
        name="Rear Stator Temp",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:thermometer",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="ModuleTempMax",
        name="Module Temp Max",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="ModuleTempMin",
        name="Module Temp Min",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        entity_registry_enabled_default=True,
    ),
    # --- Location ---
    TelemetryEntityDescription(
        key="GpsHeading",
        name="GPS Heading",
        native_unit_of_measurement="°",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:compass",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        minimum_delta_override=2,
    ),
    TelemetryEntityDescription(
        key="GpsAccuracyMeters",
        name="GPS Accuracy",
        native_unit_of_measurement="m",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:crosshairs-gps",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="GradeEstimatePercent",
        name="Road Grade",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:slope-uphill",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MaxSpeedToReachDestinationMph",
        name="Max Speed To Reach Destination",
        native_unit_of_measurement="mph",
        suggested_unit_of_measurement="km/h",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:speedometer",
        entity_registry_enabled_default=True,
    ),
    # --- Safety & Access ---
    TelemetryEntityDescription(
        key="CruiseFollowDistance",
        name="Cruise Follow Distance",
        icon="mdi:car-cruise-control",
        strip_prefix="FollowDistance",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="ForwardCollisionWarning",
        name="Forward Collision Warning",
        icon="mdi:alert-rhombus",
        strip_prefix="ForwardCollisionSensitivity",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="LaneDepartureAvoidance",
        name="Lane Departure Avoidance",
        icon="mdi:car-lane-assist",
        strip_prefix="LaneAssistLevel",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="EmergencyLaneDepartureAvoidance",
        name="Emergency Lane Departure Avoidance",
        icon="mdi:car-lane-assist",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Vehicle State ---
    TelemetryEntityDescription(
        key="CenterDisplay",
        name="Center Display",
        icon="mdi:monitor",
        strip_prefix="DisplayState",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="ConnectivityStatus",
        name="Connectivity",
        icon="mdi:signal",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="NetworkInterface",
        name="Network Interface",
        icon="mdi:wifi",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="ClimateSeatCoolingFrontLeft",
        has_dedicated_entity=True,
        name="Seat Cooling Front Left",
        icon="mdi:car-seat-cooler",
    ),
    TelemetryEntityDescription(
        key="ClimateSeatCoolingFrontRight",
        has_dedicated_entity=True,
        name="Seat Cooling Front Right",
        icon="mdi:car-seat-cooler",
    ),
    TelemetryEntityDescription(
        key="CurrentLimitMph",
        name="Current Limit",
        native_unit_of_measurement="km/h",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:speedometer-medium",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DCChargingPower",
        name="DC Charging Power",
        native_unit_of_measurement="kW",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="DestinationLocation",
        has_dedicated_entity=True,
        name="Destination Location",
        icon="mdi:map-marker-path",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="DestinationName",
        name="Destination Name",
        icon="mdi:map-search",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="EstBatteryRange",
        name="Estimated Battery Range",
        native_unit_of_measurement="mi",
        suggested_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:map-marker-distance",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="EfficiencyPackage",
        name="Efficiency Package",
        icon="mdi:leaf",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="EstimatedHoursToChargeTermination",
        name="Hours To Charge End",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:clock-end",
    ),
    TelemetryEntityDescription(
        key="ExpectedEnergyPercentAtTripArrival",
        name="Expected Energy At Arrival",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery-clock",
    ),
    TelemetryEntityDescription(
        key="ExteriorColor",
        name="Exterior Color",
        icon="mdi:palette",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="FastChargerType",
        name="Fast Charger Type",
        icon="mdi:ev-station",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="GpsState",
        name="GPS State",
        icon="mdi:crosshairs-gps",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="GuestModeMobileAccessState",
        name="Guest Mode Mobile Access",
        icon="mdi:account-key",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="HomelinkDeviceCount",
        name="Homelink Device Count",
        suggested_display_precision=0,
        icon="mdi:home-automation",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="HvacFanSpeed",
        has_dedicated_entity=True,
        name="HVAC Fan Speed",
        suggested_display_precision=0,
        icon="mdi:fan",
    ),
    TelemetryEntityDescription(
        key="HvacFanStatus",
        has_dedicated_entity=True,
        name="HVAC Fan Status",
        icon="mdi:fan-alert",
    ),
    TelemetryEntityDescription(
        key="HvacSteeringWheelHeatLevel",
        has_dedicated_entity=True,
        name="Steering Wheel Heat Level",
        suggested_display_precision=0,
        icon="mdi:steering",
    ),
    TelemetryEntityDescription(
        key="IdealBatteryRange",
        name="Ideal Battery Range",
        native_unit_of_measurement="mi",
        suggested_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:map-marker-distance",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="LifetimeEnergyUsed",
        name="Lifetime Energy Used",
        native_unit_of_measurement="kWh",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:lightning-bolt-circle",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="LifetimeEnergyChargedKwh",
        name="Lifetime Energy Charged",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:battery-charging-high",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="LifetimeEnergyGainedRegen",
        name="Lifetime Regen Energy",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-up",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="LifetimeDcChargeEnergyKwh",
        name="Lifetime DC Charge Energy",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:ev-station",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="RollingResistanceCoefficient",
        name="Rolling Resistance Coefficient",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        icon="mdi:tire",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="AccessoryRail",
        name="Accessory Rail",
        icon="mdi:rail",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="AutomaticEmergencyBrakingState",
        name="Automatic Emergency Braking State",
        icon="mdi:car-brake-alert",
        strip_prefix="AutomaticEmergencyBrakingState",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    # --- Self-Driving Stats ---
    TelemetryEntityDescription(
        key="MilesSinceReset",
        name="Distance Since Stats Reset",
        native_unit_of_measurement="mi",
        suggested_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SelfDrivingMilesSinceReset",
        name="Self-Driving Distance Since Reset",
        native_unit_of_measurement="mi",
        suggested_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:robot",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="LightsTurnSignal",
        name="Turn Signal",
        strip_prefix="TurnSignalState",
        icon="mdi:arrow-left-right-bold",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MilesToArrival",
        name="Distance To Arrival",
        native_unit_of_measurement="mi",
        suggested_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:map-marker-distance",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MinutesToArrival",
        name="Minutes To Arrival",
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:clock-fast",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="NumBrickVoltageMax",
        name="Brick Voltage Max Count",
        suggested_display_precision=0,
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="NumBrickVoltageMin",
        name="Brick Voltage Min Count",
        suggested_display_precision=0,
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="NumModuleTempMax",
        name="Module Temp Max Count",
        suggested_display_precision=0,
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="NumModuleTempMin",
        name="Module Temp Min Count",
        suggested_display_precision=0,
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="OriginLocation",
        has_dedicated_entity=True,
        name="Origin Location",
        icon="mdi:map-marker-start",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="PairedPhoneKeyAndKeyFobQty",
        name="Paired Keys",
        icon="mdi:key-variant",
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="RearSeatHeaters",
        has_dedicated_entity=True,
        name="Rear Seat Heaters",
        suggested_display_precision=0,
        icon="mdi:car-seat-heater",
    ),
    TelemetryEntityDescription(
        key="RoofColor",
        name="Roof Color",
        icon="mdi:palette-swatch",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="RouteLastUpdated",
        name="Route Last Updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-edit-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="RouteTrafficMinutesDelay",
        name="Route Traffic Delay",
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:traffic-light",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="Setting24HourTime",
        name="UI Setting 24 Hour Time",
        icon="mdi:clock-time-four-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SettingChargeUnit",
        name="UI Setting Charge Unit",
        icon="mdi:percent-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SettingDistanceUnit",
        name="UI Setting Distance Unit",
        icon="mdi:ruler",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SettingTemperatureUnit",
        name="UI Setting Temperature Unit",
        icon="mdi:thermometer-lines",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SettingTirePressureUnit",
        name="UI Setting Tire Pressure Unit",
        icon="mdi:gauge",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SoftwareUpdateDownloadPercentComplete",
        has_dedicated_entity=True,
        name="Update Download Progress",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:download",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SoftwareUpdateExpectedDurationMinutes",
        name="Update Expected Duration",
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SoftwareUpdateInstallationPercentComplete",
        has_dedicated_entity=True,
        name="Update Install Progress",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:progress-download",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SoftwareUpdateScheduledStartTime",
        name="Update Scheduled Start",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="TimeToFullCharge",
        name="Time To Full Charge",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-clock",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="Trim",
        name="Trim",
        icon="mdi:car-info",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="WheelType",
        name="Wheel Type",
        icon="mdi:car-tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Media ---
    TelemetryEntityDescription(
        key="MediaAudioVolume",
        has_dedicated_entity=True,
        name="Media Audio Volume",
        icon="mdi:volume-high",
        suggested_display_precision=0,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MediaAudioVolumeIncrement",
        has_dedicated_entity=True,
        name="Media Audio Volume Increment",
        icon="mdi:volume-plus",
        suggested_display_precision=0,
    ),
    TelemetryEntityDescription(
        key="MediaAudioVolumeMax",
        has_dedicated_entity=True,
        name="Media Audio Volume Max",
        icon="mdi:volume-source",
        suggested_display_precision=0,
    ),
    TelemetryEntityDescription(
        key="MediaNowPlayingAlbum",
        has_dedicated_entity=True,
        name="Media Album",
        icon="mdi:album",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MediaNowPlayingArtist",
        has_dedicated_entity=True,
        name="Media Artist",
        icon="mdi:account-music",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MediaNowPlayingDuration",
        has_dedicated_entity=True,
        name="Media Duration",
        icon="mdi:timer-outline",
        suggested_display_precision=0,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MediaNowPlayingElapsed",
        has_dedicated_entity=True,
        name="Media Elapsed",
        icon="mdi:timeline-clock",
        suggested_display_precision=0,
        entity_registry_enabled_default=True,
        minimum_delta_override=5000,
    ),
    TelemetryEntityDescription(
        key="MediaNowPlayingStation",
        has_dedicated_entity=True,
        name="Media Station",
        icon="mdi:radio",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MediaNowPlayingTitle",
        has_dedicated_entity=True,
        name="Media Title",
        icon="mdi:music",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MediaPlaybackSource",
        has_dedicated_entity=True,
        name="Media Playback Source",
        icon="mdi:audio-input-stereo-minijack",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="MediaPlaybackStatus",
        has_dedicated_entity=True,
        name="Media Playback Status",
        icon="mdi:play-circle",
        strip_prefix="MediaStatus",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="DoorState",
        has_dedicated_entity=True,
        name="Door State",
        icon="mdi:car-door",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="Odometer",
        name="Odometer",
        native_unit_of_measurement="mi",
        suggested_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="ScheduledChargingStartTime",
        name="Scheduled Charging Start Time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="Version",
        has_dedicated_entity=True,
        name="Firmware Version",
        icon="mdi:update",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SoftwareUpdateVersion",
        has_dedicated_entity=True,
        name="Software Update Version",
        icon="mdi:update",
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="SoftwareUpdateAvailable",
        has_dedicated_entity=True,
        name="Software Update Available",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="SoftwareUpdateInProgress",
        has_dedicated_entity=True,
        name="Software Update In Progress",
        icon="mdi:progress-download",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="SunroofInstalled",
        name="Sunroof",
        icon="mdi:car-select",
        strip_prefix="SunroofInstalledState",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="VehicleName",
        name="Vehicle Name",
        icon="mdi:car",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="CarType",
        name="Car Type",
        icon="mdi:car-info",
        strip_prefix="CarType",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- TPMS ---
    TelemetryEntityDescription(
        key="TpmsLastSeenPressureTimeFl",
        name="TPMS Last Seen FL",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:tire",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="TpmsLastSeenPressureTimeFr",
        name="TPMS Last Seen FR",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:tire",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="TpmsLastSeenPressureTimeRl",
        name="TPMS Last Seen RL",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:tire",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="TpmsLastSeenPressureTimeRr",
        name="TPMS Last Seen RR",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:tire",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="TpmsPressureFl",
        name="TPMS FL",
        native_unit_of_measurement="bar",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:tire",
    ),
    TelemetryEntityDescription(
        key="TpmsPressureFr",
        name="TPMS FR",
        native_unit_of_measurement="bar",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:tire",
    ),
    TelemetryEntityDescription(
        key="TpmsPressureRl",
        name="TPMS RL",
        native_unit_of_measurement="bar",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:tire",
    ),
    TelemetryEntityDescription(
        key="TpmsPressureRr",
        name="TPMS RR",
        native_unit_of_measurement="bar",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:tire",
    ),
    # --- Binary sensors: Charging ---
    TelemetryEntityDescription(
        binary=True,
        key="BatteryHeaterOn",
        name="Battery Heater",
        device_class=BinarySensorDeviceClass.HEAT,
        icon="mdi:heat-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="BmsFullchargecomplete",
        name="BMS Full Charge Complete",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        icon="mdi:battery-check",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="ChargeEnableRequest",
        name="Charge Enable Request",
        icon="mdi:ev-plug-type2",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="ChargePortColdWeatherMode",
        name="Charge Port Cold Weather Mode",
        icon="mdi:snowflake",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="ChargePortDoorOpen",
        has_dedicated_entity=True,
        name="Charge Port Door",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:ev-plug-type2",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="ScheduledChargingPending",
        name="Scheduled Charging Pending",
        icon="mdi:clock-outline",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="Hvil",
        name="HVIL",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:transmission-tower-off",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    # --- Climate ---
    TelemetryEntityDescription(
        binary=True,
        key="AutoSeatClimateLeft",
        has_dedicated_entity=True,
        name="Auto Seat Climate Left",
        icon="mdi:car-seat-heater",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="AutoSeatClimateRight",
        has_dedicated_entity=True,
        name="Auto Seat Climate Right",
        icon="mdi:car-seat-heater",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="PreconditioningEnabled",
        has_dedicated_entity=True,
        name="Preconditioning",
        icon="mdi:fan",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="DefrostForPreconditioning",
        has_dedicated_entity=True,
        name="Defrost (Preconditioning)",
        icon="mdi:car-defrost-front",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="RearDefrostEnabled",
        name="Rear Defrost",
        icon="mdi:car-defrost-rear",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="HvacACEnabled",
        has_dedicated_entity=True,
        name="HVAC AC",
        icon="mdi:snowflake",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="HvacAutoMode",
        has_dedicated_entity=True,
        name="HVAC Auto Mode",
        icon="mdi:fan-auto",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="HvacPower",
        has_dedicated_entity=True,
        name="HVAC Power",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:power",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="HvacSteeringWheelHeatAuto",
        has_dedicated_entity=True,
        name="Steering Wheel Heat Auto",
        icon="mdi:steering",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="NotEnoughPowerToHeat",
        name="Not Enough Power To Heat",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="RearDisplayHvacEnabled",
        name="Rear Display HVAC",
        icon="mdi:tablet-dashboard",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="SeatVentEnabled",
        name="Seat Vent",
        icon="mdi:car-seat-cooler",
    ),
    # --- Driving ---
    TelemetryEntityDescription(
        binary=True,
        key="BrakePedal",
        name="Brake Pedal",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:car-brake-hold",
    ),
    # --- Safety & Access ---
    TelemetryEntityDescription(
        binary=True,
        key="AutomaticEmergencyBrakingOff",
        name="Automatic Emergency Braking Off",
        icon="mdi:car-brake-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="AutomaticBlindSpotCamera",
        name="Blind Spot Camera",
        icon="mdi:camera",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="BlindSpotCollisionWarningChime",
        name="Blind Spot Warning Chime",
        icon="mdi:bell-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="Locked",
        has_dedicated_entity=True,
        name="Locked",
        device_class=BinarySensorDeviceClass.LOCK,
        icon="mdi:lock",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="DriveRail",
        name="Drive Rail",
        icon="mdi:car-connected",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="DriverSeatBelt",
        name="Driver Seat Belt",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        icon="mdi:seatbelt",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="DriverSeatOccupied",
        name="Driver Seat Occupied",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        icon="mdi:car-seat",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="EuropeVehicle",
        name="Europe Vehicle",
        icon="mdi:earth",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="DCDCEnable",
        name="DCDC Enable",
        icon="mdi:current-dc",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="FastChargerPresent",
        name="Fast Charger Present",
        icon="mdi:ev-station",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="GuestModeEnabled",
        name="Guest Mode",
        icon="mdi:account-switch",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="HomelinkNearby",
        name="Homelink Nearby",
        icon="mdi:garage-open",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="OffroadLightbarPresent",
        name="Offroad Lightbar Present",
        icon="mdi:car-light-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="PassengerSeatBelt",
        name="Passenger Seat Belt",
        icon="mdi:seatbelt",
        strip_prefix="BuckleStatus",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="PinToDriveEnabled",
        name="PIN To Drive",
        icon="mdi:numeric",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="RemoteStartEnabled",
        has_dedicated_entity=True,
        name="Remote Start",
        icon="mdi:car-key",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="RemoteStartActive",
        name="Remote Start Active",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:car-key",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="RightHandDrive",
        name="Right Hand Drive",
        icon="mdi:steering",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="ServiceMode",
        name="Service Mode",
        icon="mdi:wrench-cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="LightsHazardsActive",
        name="Hazard Lights",
        icon="mdi:hazard-lights",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="LightsHighBeams",
        name="High Beams",
        device_class=BinarySensorDeviceClass.LIGHT,
        icon="mdi:car-light-high",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        key="SentryMode",
        name="Sentry Mode",
        icon="mdi:shield-car",
        strip_prefix="SentryModeState",
        entity_registry_enabled_default=True,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="SpeedLimitMode",
        name="Speed Limit Mode",
        icon="mdi:speedometer-slow",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="SpeedLimitWarning",
        name="Speed Limit Warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:speedometer-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="SuperchargerSessionTripPlanner",
        name="Trip Planner Supercharger Session",
        icon="mdi:map-marker-path",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="ValetModeEnabled",
        name="Valet Mode",
        icon="mdi:car-key",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="LocatedAtFavorite",
        name="Located At Favorite",
        icon="mdi:star",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="LocatedAtHome",
        name="Located At Home",
        icon="mdi:home",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="LocatedAtWork",
        name="Located At Work",
        icon="mdi:briefcase",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="FdWindow",
        name="Front Left Window",
        device_class=BinarySensorDeviceClass.WINDOW,
        icon="mdi:car-door",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="FpWindow",
        name="Front Right Window",
        device_class=BinarySensorDeviceClass.WINDOW,
        icon="mdi:car-door",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="RdWindow",
        name="Rear Left Window",
        device_class=BinarySensorDeviceClass.WINDOW,
        icon="mdi:car-door",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="RpWindow",
        name="Rear Right Window",
        device_class=BinarySensorDeviceClass.WINDOW,
        icon="mdi:car-door",
    ),
    TelemetryEntityDescription(
        binary=True,
        key="WiperHeatEnabled",
        name="Wiper Heat",
        device_class=BinarySensorDeviceClass.HEAT,
        icon="mdi:wiper-wash-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- TPMS ---
    TelemetryEntityDescription(
        binary=True,
        key="TpmsHardWarningFl",
        name="TPMS Hard Warning FL",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsHardWarningFr",
        name="TPMS Hard Warning FR",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsHardWarningRl",
        name="TPMS Hard Warning RL",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsHardWarningRr",
        name="TPMS Hard Warning RR",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsSoftWarningFl",
        name="TPMS Soft Warning FL",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsSoftWarningFr",
        name="TPMS Soft Warning FR",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsSoftWarningRl",
        name="TPMS Soft Warning RL",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsSoftWarningRr",
        name="TPMS Soft Warning RR",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsHardWarnings",
        name="TPMS Hard Warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        binary=True,
        key="TpmsSoftWarnings",
        name="TPMS Soft Warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:tire-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TelemetryEntityDescription(
        key="LastAlertTriggered",
        name="Last Alert Triggered",
        icon="mdi:bell-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

# ---------------------------------------------------------------------------
# Unified field lookup (used by __init__.py for fleet config management)
# ---------------------------------------------------------------------------

# Maps field name → description object.  Use desc.binary to distinguish binary vs sensor.
FIELD_DEFS: dict[str, TelemetryEntityDescription | EntityDescription] = {
    desc.key: desc for desc in DESCRIPTIONS
}

# Ensure special fields exist in the lookup even if they do not have entities.
if "Location" not in FIELD_DEFS:
    FIELD_DEFS["Location"] = EntityDescription(key="Location")
FIELD_DEFS["ClimateKeeperMode"] = EntityDescription(key="ClimateKeeperMode")


# ---------------------------------------------------------------------------
# Derived sensor descriptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class DerivedSensorEntityDescription(SensorEntityDescription):
    """Entity description for sensors calculated from multiple telemetry fields."""

    source_fields: frozenset[str]
    value_multiplier: float | None = None
    entity_registry_enabled_default: bool = True


DERIVED_SENSOR_DESCRIPTIONS: tuple[DerivedSensorEntityDescription, ...] = (
    DerivedSensorEntityDescription(
        key="pack_imbalance",
        name="Pack Imbalance",
        source_fields=frozenset({"BrickVoltageMax", "BrickVoltageMin"}),
        native_unit_of_measurement="mV",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_multiplier=1000,
        icon="mdi:flash-triangle",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DerivedSensorEntityDescription(
        key="charger_power_delta",
        name="Charger Power Delta",
        source_fields=frozenset(
            {"ACChargingPower", "DCChargingPower", "PackVoltage", "PackCurrent"}
        ),
        native_unit_of_measurement="W",
        suggested_unit_of_measurement="kW",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:transmission-tower-export",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DerivedSensorEntityDescription(
        key="charger_loss",
        name="Charger Loss",
        source_fields=frozenset({"ACChargingEnergyIn", "DCChargingEnergyIn"}),
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=3,
        icon="mdi:lightning-bolt-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DerivedSensorEntityDescription(
        key="battery_power",
        name="Battery Power",
        source_fields=frozenset({"PackVoltage", "PackCurrent"}),
        native_unit_of_measurement="W",
        suggested_unit_of_measurement="kW",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:battery-sync",
    ),
    DerivedSensorEntityDescription(
        key="bms_full_rated_range",
        name="BMS Full Rated Range",
        source_fields=frozenset({"RatedRange", "Soc"}),
        native_unit_of_measurement="mi",
        suggested_unit_of_measurement="km",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-check",
    ),
    DerivedSensorEntityDescription(
        key="battery_module_temp_range",
        name="Battery Module Temp Range",
        source_fields=frozenset({"ModuleTempMax", "ModuleTempMin"}),
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer-lines",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DerivedSensorEntityDescription(
        key="charger_efficiency",
        name="Charger Efficiency",
        source_fields=frozenset({"DCChargingEnergyIn", "ACChargingEnergyIn"}),
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:percent",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


# ---------------------------------------------------------------------------
# Entity field mappings (unique_id suffix → required telemetry fields)
# ---------------------------------------------------------------------------


# Per-tyre TPMS warnings are not Tesla telemetry fields (pushing them fails the
# whole config). fleet-telemetry publishes TpmsHardWarnings/TpmsSoftWarnings as a
# TireLocation object listing only the flagged tyres, e.g. {"front_left": true},
# so the per-tyre entities are derived from it: child key -> (parent, tyre key).
TPMS_WHEEL_FIELDS: dict[str, tuple[str, str]] = {
    f"Tpms{kind}Warning{pos}": (f"Tpms{kind}Warnings", tyre)
    for kind in ("Hard", "Soft")
    for pos, tyre in (
        ("Fl", "front_left"),
        ("Fr", "front_right"),
        ("Rl", "rear_left"),
        ("Rr", "rear_right"),
    )
}


@dataclass(frozen=True)
class TelemetryEntityMapping:
    """Maps a unique_id suffix to its required telemetry fields and default-enabled state.

    Used for entities whose unique_id suffix either does not match a field name
    directly (e.g. "sentry" → SentryMode), or spans multiple fields (e.g. "climate").

    Single-field entities whose suffix equals their field name do NOT need an entry
    here; they are resolved by direct FIELD_DEFS lookup in __init__.py.
    """

    fields: frozenset[str]
    enabled_by_default: bool = True


ENTITY_FIELD_MAPPINGS: dict[str, TelemetryEntityMapping] = {
    **{
        desc.key: TelemetryEntityMapping(desc.source_fields) for desc in DERIVED_SENSOR_DESCRIPTIONS
    },
    "sentry": TelemetryEntityMapping(frozenset({"SentryMode"})),
    "charge_switch": TelemetryEntityMapping(frozenset({"DetailedChargeState"})),
    "lock": TelemetryEntityMapping(frozenset({"Locked"})),
    "remote_start_lock": TelemetryEntityMapping(frozenset({"RemoteStartEnabled"})),
    "charge_port_cover": TelemetryEntityMapping(frozenset({"ChargePortDoorOpen"})),
    "frunk": TelemetryEntityMapping(frozenset({"DoorState"})),
    "trunk": TelemetryEntityMapping(frozenset({"DoorState"})),
    "door_front_left": TelemetryEntityMapping(frozenset({"DoorState"})),
    "door_front_right": TelemetryEntityMapping(frozenset({"DoorState"})),
    "door_rear_left": TelemetryEntityMapping(frozenset({"DoorState"})),
    "door_rear_right": TelemetryEntityMapping(frozenset({"DoorState"})),
    "windows": TelemetryEntityMapping(
        frozenset({"FdWindow", "FpWindow", "RdWindow", "RpWindow", "Location"})
    ),
    "seat_heater_front_left": TelemetryEntityMapping(
        frozenset({"SeatHeaterLeft", "ClimateSeatCoolingFrontLeft", "AutoSeatClimateLeft"})
    ),
    "seat_heater_front_right": TelemetryEntityMapping(
        frozenset({"SeatHeaterRight", "ClimateSeatCoolingFrontRight", "AutoSeatClimateRight"})
    ),
    "seat_heater_rear_left": TelemetryEntityMapping(frozenset({"SeatHeaterRearLeft"})),
    "seat_heater_rear_center": TelemetryEntityMapping(frozenset({"SeatHeaterRearCenter"})),
    "seat_heater_rear_right": TelemetryEntityMapping(frozenset({"SeatHeaterRearRight"})),
    "seat_cooler_front_left": TelemetryEntityMapping(frozenset({"ClimateSeatCoolingFrontLeft"})),
    "seat_cooler_front_right": TelemetryEntityMapping(frozenset({"ClimateSeatCoolingFrontRight"})),
    "wheel_heater": TelemetryEntityMapping(
        frozenset({"HvacSteeringWheelHeatLevel", "HvacSteeringWheelHeatAuto"})
    ),
    "charge_limit": TelemetryEntityMapping(frozenset({"ChargeLimitSoc"})),
    "charge_amps": TelemetryEntityMapping(
        frozenset({"ChargeCurrentRequest", "ChargeCurrentRequestMax"})
    ),
    "climate": TelemetryEntityMapping(
        frozenset(
            {
                "PreconditioningEnabled",
                "InsideTemp",
                "HvacLeftTemperatureRequest",
                "HvacRightTemperatureRequest",
                "ClimateKeeperMode",
                "DefrostForPreconditioning",
                "HvacPower",
                "HvacACEnabled",
            }
        )
    ),
    "cabin_overheat_climate": TelemetryEntityMapping(
        frozenset(
            {
                "CabinOverheatProtectionMode",
                "CabinOverheatProtectionTemperatureLimit",
                "InsideTemp",
            }
        )
    ),
    "location": TelemetryEntityMapping(
        frozenset(
            {
                "Location",
                "GpsHeading",
                "DestinationLocation",
            }
        )
    ),
    "destination_location": TelemetryEntityMapping(
        frozenset({"DestinationLocation"}),
        enabled_by_default=False,
    ),
    "origin_location": TelemetryEntityMapping(
        frozenset({"OriginLocation"}),
        enabled_by_default=False,
    ),
    "media_player": TelemetryEntityMapping(
        frozenset(
            {
                "MediaAudioVolume",
                "MediaAudioVolumeIncrement",
                "MediaAudioVolumeMax",
                "MediaNowPlayingAlbum",
                "MediaNowPlayingArtist",
                "MediaNowPlayingDuration",
                "MediaNowPlayingElapsed",
                "MediaNowPlayingStation",
                "MediaNowPlayingTitle",
                "MediaPlaybackSource",
                "MediaPlaybackStatus",
            }
        )
    ),
    "firmware": TelemetryEntityMapping(
        frozenset(
            {
                "Version",
                "SoftwareUpdateVersion",
                "SoftwareUpdateDownloadPercentComplete",
                "SoftwareUpdateInstallationPercentComplete",
                "SoftwareUpdateAvailable",
                "SoftwareUpdateInProgress",
            }
        )
    ),
    **{
        child: TelemetryEntityMapping(frozenset({parent}), enabled_by_default=False)
        for child, (parent, _) in TPMS_WHEEL_FIELDS.items()
    },
}

DERIVED_SENSOR_UNIQUE_ID_SUFFIXES: frozenset[str] = frozenset(
    desc.key for desc in DERIVED_SENSOR_DESCRIPTIONS
)


MQTT_ONLY_FIELDS: set[str] = {
    "ConnectivityStatus",
    "NetworkInterface",
    "LastAlertTriggered",
}

SUPPORTED_STREAMING_FIELDS: set[str] = (
    set(FIELD_DEFS.keys()) - MQTT_ONLY_FIELDS - set(TPMS_WHEEL_FIELDS)
)

LOCATION_FIELDS: set[str] = {"Location"}

NUMERIC_SENSOR_FIELDS: set[str] = {
    desc.key
    for desc in DESCRIPTIONS
    if not desc.binary
    and (
        desc.native_unit_of_measurement is not None
        or desc.suggested_display_precision is not None
        or desc.value_multiplier is not None
        or desc.state_class is not None
    )
    and desc.key not in CONTROL_REALTIME_FIELDS
    and desc.key not in LOCATION_FIELDS
}
