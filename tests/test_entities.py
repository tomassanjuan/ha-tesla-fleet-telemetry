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
