"""Smoke tests: one entity per platform, verifying unique_id/device_info wiring.

Not exhaustive per-platform behavior coverage — just confirms every platform's
entities still construct correctly and carry a VIN-scoped unique_id and a
device_info tied to the right vehicle, which is exactly the class of thing
that broke in the unique_id VIN-scoping regression.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_fleet_telemetry.binary_sensor import TelemetryBinarySensor
from custom_components.tesla_fleet_telemetry.const import DESCRIPTIONS, DOMAIN
from custom_components.tesla_fleet_telemetry.coordinator import TelemetryCoordinator
from custom_components.tesla_fleet_telemetry.cover import TeslaChargePortCover
from custom_components.tesla_fleet_telemetry.device_tracker import TeslaLocationTracker
from custom_components.tesla_fleet_telemetry.lock import TeslaVehicleLock
from custom_components.tesla_fleet_telemetry.number import ChargeLimitNumber
from custom_components.tesla_fleet_telemetry.sensor import TelemetrySensor
from custom_components.tesla_fleet_telemetry.switch import ChargeSwitch

from .conftest import TEST_VIN


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=TEST_VIN)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry) -> TelemetryCoordinator:
    return TelemetryCoordinator(hass, config_entry, TEST_VIN)


def _assert_common(entity, expected_suffix: str) -> None:
    assert entity.unique_id == f"telemetry_{TEST_VIN}_{expected_suffix}"
    assert entity.device_info["identifiers"] == {(DOMAIN, TEST_VIN)}


def test_sensor_entity(coordinator: TelemetryCoordinator) -> None:
    description = next(d for d in DESCRIPTIONS if not d.binary and not d.has_dedicated_entity)
    entity = TelemetrySensor(coordinator, description, TEST_VIN)
    _assert_common(entity, description.key)


def test_binary_sensor_entity(coordinator: TelemetryCoordinator) -> None:
    description = next(d for d in DESCRIPTIONS if d.binary and not d.has_dedicated_entity)
    entity = TelemetryBinarySensor(coordinator, description, TEST_VIN)
    _assert_common(entity, description.key)


def test_device_tracker_entity(coordinator: TelemetryCoordinator) -> None:
    entity = TeslaLocationTracker(
        coordinator, TEST_VIN, "Location", "Location", "mdi:map-marker", "location"
    )
    _assert_common(entity, "location")


def _command_entity_args(
    coordinator: TelemetryCoordinator, config_entry: ConfigEntry
) -> tuple:
    proxy_client = MagicMock()
    proxy_client.ready = True
    implementation = MagicMock()
    return (coordinator, TEST_VIN, proxy_client, implementation, config_entry)


def test_lock_entity(coordinator: TelemetryCoordinator, config_entry: ConfigEntry) -> None:
    entity = TeslaVehicleLock(*_command_entity_args(coordinator, config_entry))
    _assert_common(entity, "lock")


def test_cover_entity(coordinator: TelemetryCoordinator, config_entry: ConfigEntry) -> None:
    entity = TeslaChargePortCover(*_command_entity_args(coordinator, config_entry))
    _assert_common(entity, "charge_port_cover")


def test_switch_entity(coordinator: TelemetryCoordinator, config_entry: ConfigEntry) -> None:
    entity = ChargeSwitch(*_command_entity_args(coordinator, config_entry))
    _assert_common(entity, "charge_switch")


def test_number_entity(coordinator: TelemetryCoordinator, config_entry: ConfigEntry) -> None:
    entity = ChargeLimitNumber(*_command_entity_args(coordinator, config_entry))
    _assert_common(entity, "charge_limit")


def test_seat_select_defs_use_documented_wire_seat_indices() -> None:
    """remote_seat_cooler_request/remote_auto_seat_climate_request go out as
    plain JSON through the proxy (see proxy_client._dispatch_command), so the
    values here must match Tesla's documented wire format directly: seat
    position 1=front left, 2=front right (unlike seat_heater's 0/1).
    """
    from custom_components.tesla_fleet_telemetry.select import SEAT_SELECT_DEFS

    by_key = {d.key: d for d in SEAT_SELECT_DEFS}
    assert by_key["seat_heater_front_left"].cooler_seat_value == 1
    assert by_key["seat_heater_front_right"].cooler_seat_value == 2
    assert by_key["seat_heater_front_left"].auto_seat_position == 1
    assert by_key["seat_heater_front_right"].auto_seat_position == 2


async def test_cool_medium_sends_seat_cooler_level_three(
    coordinator: TelemetryCoordinator, config_entry: ConfigEntry
) -> None:
    """Tesla's seat_cooler_level wire enum is shifted by one vs. its own docs
    (Off=1/Low=2/Med=3/High=4, not 0-3) — confirmed empirically: sending the
    0-indexed level directly turned "Medium" into "Low" and "High" into
    "Medium" on the vehicle.
    """
    from unittest.mock import AsyncMock

    from custom_components.tesla_fleet_telemetry.select import SEAT_SELECT_DEFS, TeslaSeatLevelSelect

    select_def = next(d for d in SEAT_SELECT_DEFS if d.key == "seat_heater_front_left")
    entity = TeslaSeatLevelSelect(*_command_entity_args(coordinator, config_entry), select_def)
    entity._send_command = AsyncMock()

    await entity.async_select_option("Cool Medium")

    entity._send_command.assert_any_call(
        "remote_seat_cooler_request", {"seat_cooler_level": 3, "seat_position": 1}
    )


async def test_wheel_heater_auto_uses_auto_climate_endpoint(
    coordinator: TelemetryCoordinator, config_entry: ConfigEntry
) -> None:
    """remote_steering_wheel_heater_request is documented as the on/off command
    for vehicles that do NOT support auto steering wheel heat; sending it for
    "Auto" on a vehicle that does support it fails with InvalidCommand. The
    auto toggle must go through remote_auto_steering_wheel_heat_climate_request.
    """
    from unittest.mock import AsyncMock

    from custom_components.tesla_fleet_telemetry.select import SEAT_SELECT_DEFS, TeslaSeatLevelSelect

    select_def = next(d for d in SEAT_SELECT_DEFS if d.key == "wheel_heater")
    entity = TeslaSeatLevelSelect(*_command_entity_args(coordinator, config_entry), select_def)
    entity._send_command = AsyncMock()

    await entity.async_select_option("Auto")

    entity._send_command.assert_called_once_with(
        "remote_auto_steering_wheel_heat_climate_request", {"on": True}
    )


async def test_wheel_heater_high_sends_documented_wire_level_three(
    coordinator: TelemetryCoordinator, config_entry: ConfigEntry
) -> None:
    """Tesla's own docs for remote_steering_wheel_heat_level_request specify
    level 0=off/1=low/3=high (medium/2 is not exposed) — this is the plain
    JSON body we send via the proxy passthrough, so it must match the docs
    exactly, unlike the seat_cooler_level endpoint which is shifted vs. its docs.
    """
    from unittest.mock import AsyncMock

    from custom_components.tesla_fleet_telemetry.select import SEAT_SELECT_DEFS, TeslaSeatLevelSelect

    select_def = next(d for d in SEAT_SELECT_DEFS if d.key == "wheel_heater")
    entity = TeslaSeatLevelSelect(*_command_entity_args(coordinator, config_entry), select_def)
    entity._send_command = AsyncMock()

    await entity.async_select_option("High")

    entity._send_command.assert_called_once_with(
        "remote_steering_wheel_heat_level_request", {"level": 3}
    )
