"""Tests for TelemetryCoordinator's push-update / debounce path."""

from __future__ import annotations

import asyncio
from collections.abc import Generator

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_fleet_telemetry.const import (
    DOMAIN,
)
from custom_components.tesla_fleet_telemetry.coordinator import TelemetryCoordinator
from custom_components.tesla_fleet_telemetry.descriptions import (
    FIELD_DEFS,
)

from .conftest import TEST_VIN


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
    # Field updates arm a debounce timer; HA's test harness fails on lingering timers.
    coord.async_cancel_pending_update()


def _a_real_field() -> str:
    return next(iter(FIELD_DEFS))


async def test_update_field_stores_value_and_notifies(coordinator: TelemetryCoordinator) -> None:
    field = _a_real_field()
    seen: list[tuple[str, object]] = []
    coordinator.async_add_field_listener(field, lambda f, v: seen.append((f, v)))

    coordinator.async_update_field(field, "some_value")

    assert coordinator.get_field(field) == "some_value"
    assert seen == [(field, "some_value")]


async def test_update_field_ignores_unknown_field(coordinator: TelemetryCoordinator) -> None:
    coordinator.async_update_field("NotARealTelemetryField", 123)
    assert coordinator.get_field("NotARealTelemetryField") is None


async def test_update_field_debounces_listener_notifications(
    coordinator: TelemetryCoordinator,
) -> None:
    """A burst of field updates should fire async_update_listeners only once."""
    field_a, field_b = list(FIELD_DEFS)[:2]
    calls = 0

    def _count(*_args: object) -> None:
        nonlocal calls
        calls += 1

    coordinator.async_add_listener(_count)
    coordinator.async_update_field(field_a, "x")
    coordinator.async_update_field(field_b, "y")

    assert calls == 0  # not yet flushed
    await asyncio.sleep(0.1)
    assert calls == 1
    assert coordinator.last_update_success is True


async def test_update_alert_only_advances_on_newer_timestamp(
    coordinator: TelemetryCoordinator,
) -> None:
    coordinator.update_alert("AlertOne", {"StartedAt": "2026-01-01T00:00:00Z"})
    assert coordinator.get_field("LastAlertTriggered") == "AlertOne"

    coordinator.update_alert("AlertOlder", {"StartedAt": "2025-01-01T00:00:00Z"})
    assert coordinator.get_field("LastAlertTriggered") == "AlertOne"

    coordinator.update_alert("AlertTwo", {"StartedAt": "2027-01-01T00:00:00Z"})
    assert coordinator.get_field("LastAlertTriggered") == "AlertTwo"


async def test_wait_for_fields_resolves_once_value_arrives(
    coordinator: TelemetryCoordinator,
) -> None:
    field = _a_real_field()

    async def _set_later() -> None:
        await asyncio.sleep(0.05)
        coordinator.async_update_field(field, "arrived")

    task = asyncio.ensure_future(_set_later())
    result = await coordinator.async_wait_for_fields({field}, timeout=1.0)
    await task
    assert result is True


async def test_wait_for_fields_times_out(coordinator: TelemetryCoordinator) -> None:
    field = _a_real_field()
    result = await coordinator.async_wait_for_fields({field}, timeout=0.05)
    assert result is False
