"""Polling profile definitions for Tesla Fleet Telemetry."""

from __future__ import annotations

# =============================================================================
# Polling Intervals and Bandwidth Considerations
# =============================================================================
# Tesla Fleet API recommended polling intervals (from observation):
#
#   - Realtime mode:  1s intervals (AGGRESSIVE, HIGH BANDWIDTH)
#     Use only when you need real-time data (e.g., driving, streaming sensor data).
#     Will significantly increase vehicle's mobile data usage and battery drain.
#     Recommended only for vehicles with unlimited data plans.
#
#   - Normal mode:   10-30s intervals (BALANCED)
#     Default mode for most users. Good balance between responsiveness and
#     bandwidth efficiency. Typical for home automation and telemetry use cases.
#
#   - Low mode:      30s+ intervals (BATTERY-CONSCIOUS)
#     Minimizes bandwidth and battery usage. Suitable for idle monitoring,
#     long-term tracking, and vehicles with limited data plans.
#
# WARNING: Selecting realtime mode will dramatically increase telemetry
# frequency to 1-second intervals for most fields. This is only recommended
# if you truly need real-time data and understand the bandwidth implications.
# =============================================================================

# ---------------------------------------------------------------------------
# Core controls — fields that must always stream at 1 s for command entities.
# ---------------------------------------------------------------------------

CORE_CONTROLS: dict[str, set[str]] = {
    "lock": {"Locked"},
    "remote_start": {"RemoteStartEnabled", "RemoteStartActive"},
    "sentry": {"SentryMode"},
    "charge": {
        "ChargeState",
        "DetailedChargeState",
        "ChargeEnableRequest",
        "ChargeLimitSoc",
        "ChargeCurrentRequest",
        "ChargeCurrentRequestMax",
    },
    "charge_port": {"ChargePortDoorOpen"},
    "doors": {"DoorState"},
    "windows": {"FdWindow", "FpWindow", "RdWindow", "RpWindow"},
    "climate": {
        "PreconditioningEnabled",
        "HvacLeftTemperatureRequest",
        "ClimateKeeperMode",
        "DefrostForPreconditioning",
        "HvacPower",
        "HvacACEnabled",
        "HvacAutoMode",
        "HvacFanSpeed",
        "HvacFanStatus",
    },
    "cabin_overheat": {
        "CabinOverheatProtectionMode",
        "CabinOverheatProtectionTemperatureLimit",
    },
}

INSTANT_FIELDS: frozenset[str] = frozenset(
    field for fields in CORE_CONTROLS.values() for field in fields
)

# ---------------------------------------------------------------------------
# Polling profiles — realtime / normal / low
# ---------------------------------------------------------------------------
# Each group entry has:
#   interval        — interval_seconds for fleet_telemetry_config
#   delta_multiplier — multiplier applied to precision-derived base delta
#                      (None = no minimum_delta, send every update)
# ---------------------------------------------------------------------------

POLLING_PROFILES: dict[str, dict[str, dict]] = {
    "realtime": {
        "percent": {"interval": 1, "delta_multiplier": 1.0},
        "energy": {"interval": 1, "delta_multiplier": 1.0},
        "voltage": {"interval": 1, "delta_multiplier": 1.0},
        "current": {"interval": 1, "delta_multiplier": 1.0},
        "power": {"interval": 1, "delta_multiplier": 1.0},
        "temperature": {"interval": 1, "delta_multiplier": 1.0},
        "speed": {"interval": 1, "delta_multiplier": 1.0},
        "misc_numeric": {"interval": 1, "delta_multiplier": 1.0},
        "status": {"interval": 1, "delta_multiplier": None},
        "binary": {"interval": 1, "delta_multiplier": None},
    },
    "normal": {
        "percent": {"interval": 10, "delta_multiplier": 2.0},
        "energy": {"interval": 10, "delta_multiplier": 2.0},
        "voltage": {"interval": 10, "delta_multiplier": 2.0},
        "current": {"interval": 10, "delta_multiplier": 2.0},
        "power": {"interval": 10, "delta_multiplier": 2.0},
        "temperature": {"interval": 10, "delta_multiplier": 2.0},
        "speed": {"interval": 10, "delta_multiplier": 2.0},
        "misc_numeric": {"interval": 10, "delta_multiplier": 2.0},
        "status": {"interval": 10, "delta_multiplier": None},
        "binary": {"interval": 1, "delta_multiplier": None},
    },
    "low": {
        "percent": {"interval": 30, "delta_multiplier": 2.5},
        "energy": {"interval": 30, "delta_multiplier": 2.5},
        "voltage": {"interval": 30, "delta_multiplier": 2.5},
        "current": {"interval": 30, "delta_multiplier": 2.5},
        "power": {"interval": 30, "delta_multiplier": 2.5},
        "temperature": {"interval": 30, "delta_multiplier": 2.5},
        "speed": {"interval": 30, "delta_multiplier": 2.5},
        "misc_numeric": {"interval": 30, "delta_multiplier": 2.5},
        "status": {"interval": 30, "delta_multiplier": None},
        "binary": {"interval": 1, "delta_multiplier": None},
    },
}


def field_group_for_desc(desc: object) -> str:
    """Map a field descriptor to a polling profile group name."""
    # Import here to avoid circular deps — this module must stay const.py-free.
    from homeassistant.components.binary_sensor import BinarySensorEntityDescription

    if isinstance(desc, BinarySensorEntityDescription):
        return "binary"

    unit = getattr(desc, "native_unit_of_measurement", None)
    if unit is None:
        return "status"
    if unit == "%":
        return "percent"
    if unit in ("kWh", "Wh"):
        return "energy"
    if unit == "V":
        return "voltage"
    if unit == "A":
        return "current"
    if unit in ("kW", "W"):
        return "power"
    if unit in ("\u00b0C", "\u00b0F"):
        return "temperature"
    if unit in ("mph", "km/h", "m/s"):
        return "speed"
    return "misc_numeric"
