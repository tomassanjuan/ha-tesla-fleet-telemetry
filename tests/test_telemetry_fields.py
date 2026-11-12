"""Tests for new telemetry fields and hot-path optimizations."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_fleet_telemetry import command_entity
from custom_components.tesla_fleet_telemetry.binary_sensor import TelemetryBinarySensor
from custom_components.tesla_fleet_telemetry.command_entity import CommandEntity
from custom_components.tesla_fleet_telemetry.const import (
    DOMAIN,
)
from custom_components.tesla_fleet_telemetry.coordinator import TelemetryCoordinator
from custom_components.tesla_fleet_telemetry.descriptions import (
    ENTITY_FIELD_MAPPINGS,
    FIELD_DEFS,
    SUPPORTED_STREAMING_FIELDS,
)
from custom_components.tesla_fleet_telemetry.mqtt_client import TeslaMQTTClient
from custom_components.tesla_fleet_telemetry.update import TeslaFirmwareUpdate

from .conftest import TEST_VIN

NEW_FIELDS = (
    "NominalFullPackEnergyKwh",
    "LifetimeEnergyChargedKwh",
    "LifetimeEnergyGainedRegen",
    "BrickSocMinPercent",
    "SoftwareUpdateAvailable",
    "SoftwareUpdateInProgress",
    "RemoteStartActive",
    "GpsAccuracyMeters",
    "GradeEstimatePercent",
    "MaxSpeedToReachDestinationMph",
    "SemiCruiseSpeedLimitMph",
    "SunroofInstalled",
    "DiTorquemotor",
    "DiVBatR",
)


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=TEST_VIN)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinator(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> Generator[TelemetryCoordinator]:
    coord = TelemetryCoordinator(hass, config_entry, TEST_VIN)
    yield coord
    coord.async_cancel_pending_update()


# --- TPMS -------------------------------------------------------------------


async def test_tpms_per_wheel_entities_refresh_from_aggregate(
    coordinator: TelemetryCoordinator,
) -> None:
    """Per-wheel fields are fanned out from the aggregate and reach their entities."""
    calls = 0

    def _count() -> None:
        nonlocal calls
        calls += 1

    wheel = TelemetryBinarySensor(coordinator, FIELD_DEFS["TpmsHardWarningFl"], TEST_VIN)
    coordinator.async_add_listener(_count, wheel.coordinator_context)

    coordinator.async_update_field("TpmsHardWarnings", {"front_left": True})
    await asyncio.sleep(0.1)
    assert wheel.is_on is True
    assert calls == 1


# --- New fields -------------------------------------------------------------


def test_new_fields_are_defined_and_streamable() -> None:
    for field in NEW_FIELDS:
        assert field in FIELD_DEFS, field
        assert field in SUPPORTED_STREAMING_FIELDS, field
    firmware_fields = ENTITY_FIELD_MAPPINGS["firmware"].fields
    assert {"SoftwareUpdateAvailable", "SoftwareUpdateInProgress"} <= firmware_fields


async def test_update_entity_prefers_vehicle_update_flags(
    coordinator: TelemetryCoordinator, config_entry: MockConfigEntry
) -> None:
    entity = TeslaFirmwareUpdate(coordinator, TEST_VIN, MagicMock(), MagicMock(), config_entry)
    coordinator.async_update_field("Version", "2026.20.1")
    coordinator.async_update_field("SoftwareUpdateVersion", "2026.26.1")
    coordinator.async_update_field("SoftwareUpdateDownloadPercentComplete", 40)
    assert entity.in_progress is True  # heuristic, no flags yet

    coordinator.async_update_field("SoftwareUpdateInProgress", False)
    assert entity.in_progress is False

    coordinator.async_update_field("SoftwareUpdateAvailable", False)
    assert entity._update_pending is False
    assert entity.extra_state_attributes["update_available"] is False

    coordinator.async_update_field("SoftwareUpdateInProgress", True)
    assert entity.in_progress is True
    assert entity._update_pending is True


# --- Coordinator optimizations ---------------------------------------------


async def test_listeners_only_run_for_their_fields(coordinator: TelemetryCoordinator) -> None:
    field_a, field_b = "Soc", "Odometer"
    calls: dict[str, int] = {"a": 0, "b": 0, "any": 0}

    def _bump(key: str):
        def _cb() -> None:
            calls[key] += 1

        return _cb

    coordinator.async_add_listener(_bump("a"), frozenset({field_a}))
    coordinator.async_add_listener(_bump("b"), frozenset({field_b}))
    coordinator.async_add_listener(_bump("any"))

    coordinator.async_update_field(field_a, 50)
    await asyncio.sleep(0.1)
    assert calls == {"a": 1, "b": 0, "any": 1}

    coordinator.async_update_listeners()  # explicit refresh reaches everyone
    assert calls == {"a": 2, "b": 1, "any": 2}


async def test_continuous_stream_still_flushes(coordinator: TelemetryCoordinator) -> None:
    """Messages closer together than the debounce window must not starve listeners."""
    calls = 0

    def _count() -> None:
        nonlocal calls
        calls += 1

    coordinator.async_add_listener(_count)
    for value in range(12):
        coordinator.async_update_field("Soc", value)
        await asyncio.sleep(0.02)
    assert calls >= 2


# --- Wake-and-retry ---------------------------------------------------------


async def test_wake_retry_returns_when_vehicle_connects(
    hass: HomeAssistant, coordinator: TelemetryCoordinator, config_entry: MockConfigEntry
) -> None:
    proxy = MagicMock()
    proxy.ready = True
    proxy.send_command = AsyncMock(
        side_effect=[HomeAssistantError("vehicle is asleep"), {"result": True}]
    )

    async def _wake(_token: str) -> dict:
        hass.loop.call_later(
            0.05, coordinator.async_update_field, "ConnectivityStatus", "Connected"
        )
        return {}

    proxy.wake_vehicle = AsyncMock(side_effect=_wake)
    entity = CommandEntity(coordinator, TEST_VIN, proxy, MagicMock(), config_entry)
    entity.hass = hass
    entity._get_token = AsyncMock(return_value="token")  # type: ignore[method-assign]

    with patch.object(command_entity, "WAKE_UP_CONNECTED_SETTLE_SECONDS", 0):
        async with asyncio.timeout(2):  # the old fixed wait was 12 s
            result = await entity._send_command("door_lock")

    assert result == {"result": True}
    assert proxy.send_command.await_count == 2
    assert not coordinator._field_listeners.get("ConnectivityStatus")


# --- MQTT batching ----------------------------------------------------------


async def test_mqtt_messages_are_batched_in_order(hass: HomeAssistant) -> None:
    received: list[tuple[str, object]] = []
    client = TeslaMQTTClient(
        hass, "mqtt.local", 1883, TEST_VIN, on_message=lambda f, v: received.append((f, v))
    )
    with patch.object(
        hass.loop, "call_soon_threadsafe", wraps=hass.loop.call_soon_threadsafe
    ) as call_soon:
        for field, payload in (("Soc", b"80"), ("Odometer", b"1000"), ("Soc", b"81")):
            msg = SimpleNamespace(
                topic=f"tesla/{TEST_VIN}/v/{field}", payload=payload, retain=False
            )
            client._on_mqtt_message(None, None, msg)
        await asyncio.sleep(0)

    assert call_soon.call_count == 1
    assert received == [("Soc", 80), ("Odometer", 1000), ("Soc", 81)]
