"""Value decoding against fleet-telemetry's MQTT wire format.

The payload shapes below mirror fleet-telemetry's datastore/mqtt getDatumValue:
every value is json.Marshal'ed, enums are published by name, TireLocation is an
object of only the flagged tyres, and invalid values are `null`.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.tesla_fleet_telemetry.binary_sensor import TelemetryBinarySensor
from custom_components.tesla_fleet_telemetry.const import (
    DOMAIN,
)
from custom_components.tesla_fleet_telemetry.coordinator import TelemetryCoordinator
from custom_components.tesla_fleet_telemetry.cover import _window_is_open
from custom_components.tesla_fleet_telemetry.descriptions import (
    DESCRIPTIONS,
    SUPPORTED_STREAMING_FIELDS,
    TPMS_WHEEL_FIELDS,
)
from custom_components.tesla_fleet_telemetry.entity_registry import _fields_for_unique_id
from custom_components.tesla_fleet_telemetry.mqtt_client import _parse_value
from custom_components.tesla_fleet_telemetry.repairs import UnknownFieldRepairFlow
from custom_components.tesla_fleet_telemetry.sensor import TelemetrySensor

from .conftest import TEST_VIN


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=TEST_VIN, data={"vin": TEST_VIN})
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinator(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> Generator[TelemetryCoordinator]:
    coord = TelemetryCoordinator(hass, config_entry, TEST_VIN)
    yield coord
    coord.async_cancel_pending_update()


def _binary(coordinator: TelemetryCoordinator, key: str) -> TelemetryBinarySensor:
    description = next(d for d in DESCRIPTIONS if d.key == key)
    return TelemetryBinarySensor(coordinator, description, TEST_VIN)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"123"', "123"),  # numeric-looking string field stays a string
        ('"Caf\\u00e9"', "Café"),  # JSON escapes are decoded
        ("80.0", 80),
        ("12.5", 12.5),
        ("null", None),  # fleet-telemetry's "invalid"
        ('"WindowStatePartiallyOpen"', "WindowStatePartiallyOpen"),
        ('{"front_left":true}', {"front_left": True}),
    ],
)
def test_parse_value_decodes_json(raw: str, expected: object) -> None:
    assert _parse_value(raw) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("WindowStateClosed", False),
        ("WindowStatePartiallyOpen", True),
        ("WindowStateOpened", True),
        ("WindowStateUnknown", None),
    ],
)
def test_window_binary_sensor_decodes_enum(
    coordinator: TelemetryCoordinator, value: str, expected: bool | None
) -> None:
    sensor = _binary(coordinator, "FdWindow")
    coordinator.async_update_field("FdWindow", value)
    assert sensor.is_on is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("BuckleStatusLatched", True),
        ("BuckleStatusUnlatched", False),
        ("BuckleStatusFaulted", None),
    ],
)
def test_seat_belt_binary_sensor_decodes_enum(
    coordinator: TelemetryCoordinator, value: str, expected: bool | None
) -> None:
    sensor = _binary(coordinator, "DriverSeatBelt")
    coordinator.async_update_field("DriverSeatBelt", value)
    assert sensor.is_on is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("HvilStatusOK", False), ("HvilStatusFault", True), ("HvilStatusUnknown", None)],
)
def test_hvil_unknown_is_not_a_fault(
    coordinator: TelemetryCoordinator, value: str, expected: bool | None
) -> None:
    sensor = _binary(coordinator, "Hvil")
    coordinator.async_update_field("Hvil", value)
    assert sensor.is_on is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("ChargePortLatchEngaged", True), ("ChargePortLatchDisengaged", False)],
)
def test_charge_port_latch_disengaged_is_not_engaged(
    coordinator: TelemetryCoordinator, value: str, expected: bool
) -> None:
    sensor = _binary(coordinator, "Hvil")  # any binary description; call the parser directly
    assert sensor._is_on_charge_port_latch(value) is expected


def test_tpms_warning_object_drives_aggregate_and_per_tyre_sensors(
    coordinator: TelemetryCoordinator,
) -> None:
    aggregate = _binary(coordinator, "TpmsHardWarnings")
    front_left = _binary(coordinator, "TpmsHardWarningFl")
    rear_right = _binary(coordinator, "TpmsHardWarningRr")

    coordinator.async_update_field("TpmsHardWarnings", {})
    assert aggregate.is_on is False
    assert front_left.is_on is False

    coordinator.async_update_field("TpmsHardWarnings", {"front_left": True})
    assert aggregate.is_on is True
    assert front_left.is_on is True
    assert rear_right.is_on is False


def test_per_tyre_tpms_fields_are_never_pushed_to_tesla() -> None:
    """They are not Tesla fields; pushing one fails the whole config."""
    assert not set(TPMS_WHEEL_FIELDS) & SUPPORTED_STREAMING_FIELDS
    assert _fields_for_unique_id(f"telemetry_{TEST_VIN}_TpmsSoftWarningRl", TEST_VIN) == {
        "TpmsSoftWarnings"
    }


def test_partially_open_window_is_open() -> None:
    assert _window_is_open("WindowStatePartiallyOpen") is True


async def test_invalid_value_replaces_restored_state(
    hass: HomeAssistant, coordinator: TelemetryCoordinator
) -> None:
    """Restored state is a startup placeholder, not a substitute for a live null."""
    description = next(d for d in DESCRIPTIONS if d.key == "FdWindow")
    mock_restore_cache(hass, [State("binary_sensor.front_left_window", STATE_ON)])
    sensor = TelemetryBinarySensor(coordinator, description, TEST_VIN)
    sensor.hass = hass
    sensor.entity_id = "binary_sensor.front_left_window"
    await sensor.async_added_to_hass()

    assert sensor.is_on is True  # nothing received yet: restored value

    coordinator.async_update_field("FdWindow", None)  # Tesla reports it invalid
    assert sensor.is_on is None
    assert sensor.available is False


async def test_restored_distance_is_not_converted_twice(
    hass: HomeAssistant, coordinator: TelemetryCoordinator
) -> None:
    """HA stores km (display unit); native is mi. Restore must convert back."""
    description = next(d for d in DESCRIPTIONS if d.key == "RatedRange")
    mock_restore_cache(
        hass, [State("sensor.rated_range", "346.0", {"unit_of_measurement": "km"})]
    )
    sensor = TelemetrySensor(coordinator, description, TEST_VIN)
    sensor.hass = hass
    sensor.entity_id = "sensor.rated_range"
    await sensor.async_added_to_hass()

    assert sensor.native_value == pytest.approx(346.0 / 1.609344, abs=0.01)


async def test_unknown_field_repair_disables_as_user(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """INTEGRATION-disabled entities get re-enabled by the default sync on setup."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "sensor", DOMAIN, f"telemetry_{TEST_VIN}_Soc", config_entry=config_entry
    )
    config_entry.runtime_data = MagicMock(vin=TEST_VIN)

    flow = UnknownFieldRepairFlow()
    flow.hass = hass
    flow.issue_id = f"unknown_field_{TEST_VIN}_Soc"
    await flow.async_step_init({})

    assert registry.async_get(entry.entity_id).disabled_by is er.RegistryEntryDisabler.USER


def test_sensor_keeps_numeric_looking_text(coordinator: TelemetryCoordinator) -> None:
    description = next(d for d in DESCRIPTIONS if d.key == "VehicleName")
    sensor = TelemetrySensor(coordinator, description, TEST_VIN)
    coordinator.async_update_field("VehicleName", _parse_value('"0042"'))
    assert sensor.native_value == "0042"
